"""Создание задачи: запись в базу, «Принял, работаю…», вложения и вебхук."""

import logging
import secrets
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, ReplyParameters

from grok_bridge.services.attachments import (
    AttachmentRef,
    download_attachments,
    extract_attachments,
    forward_source,
    special_content_text,
)
from grok_bridge.services.webhook_client import WebhookClient, WebhookError
from grok_bridge.storage import TaskStatus, TaskStore

logger = logging.getLogger(__name__)

ACK_TEXT = "Принял, работаю…"
REPLY_TEXT_LIMIT = 1000


def new_task_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def message_text(message: Message) -> str:
    """Текст сообщения (text или caption) с пометкой о пересылке и описанием геолокации/контакта."""
    parts = [message.text or message.caption or "", special_content_text(message) or ""]
    text = "\n".join(part for part in parts if part)
    source = forward_source(message)
    if source is not None:
        text = f"[Переслано от {source}]" + (f"\n{text}" if text else "")
    return text


def combine_text(messages: Sequence[Message]) -> str:
    """Текст задачи из одного или нескольких сообщений."""
    return "\n\n".join(part for part in (message_text(m) for m in messages) if part)


def _describe_for_log(text: str, refs: Sequence[AttachmentRef]) -> str:
    kinds = ", ".join(ref.type for ref in refs)
    return f"{text[:200]!r}" + (f" + вложения: {kinds}" if kinds else "")


class TaskService:
    def __init__(
        self,
        store: TaskStore,
        webhook: WebhookClient,
        outbox_dir: Path,
        inbox_dir: Path,
        max_download_bytes: int | None = None,
    ) -> None:
        self._store = store
        self._webhook = webhook
        self._outbox_dir = outbox_dir
        self._inbox_dir = inbox_dir
        self._max_download_bytes = max_download_bytes

    def find_parent(self, messages: Sequence[Message]) -> tuple[str | None, str | None]:
        """(parent_task_id, текст сообщения, на которое ответили) — по первому reply в пачке."""
        for message in messages:
            replied = message.reply_to_message
            if replied is None:
                continue
            parent = self._store.task_for_message(message.chat.id, replied.message_id)
            quoted = (replied.text or replied.caption or "")[:REPLY_TEXT_LIMIT] or None
            return parent, quoted
        return None, None

    async def submit_messages(self, bot: Bot, messages: Sequence[Message]) -> str:
        """Одна задача из сообщений пользователя (одного или пачки подряд)."""
        first = messages[0]
        refs = [ref for message in messages for ref in extract_attachments(message)]
        parent_task_id, reply_to_text = self.find_parent(messages)
        extra: dict[str, Any] = {}
        if reply_to_text is not None:
            extra["reply_to_text"] = reply_to_text
        return await self.submit(
            bot,
            chat_id=first.chat.id,
            user_id=first.from_user.id if first.from_user else first.chat.id,
            text=combine_text(messages),
            attachments=refs,
            parent_task_id=parent_task_id,
            source_message_ids=[m.message_id for m in messages],
            extra=extra,
        )

    async def submit(
        self,
        bot: Bot,
        *,
        chat_id: int,
        user_id: int,
        text: str,
        attachments: Sequence[AttachmentRef] = (),
        parent_task_id: str | None = None,
        source_message_ids: Sequence[int] = (),
        extra: dict[str, Any] | None = None,
    ) -> str:
        task_id = new_task_id()
        self._store.add(task_id=task_id, chat_id=chat_id, user_id=user_id, text=text, parent_task_id=parent_task_id)
        logger.info(
            "Новая задача %s от %s%s: %s",
            task_id, user_id, f" (продолжение {parent_task_id})" if parent_task_id else "",
            _describe_for_log(text, attachments),
        )

        reply_to = (
            ReplyParameters(message_id=source_message_ids[0], allow_sending_without_reply=True)
            if source_message_ids else None
        )
        ack = await bot.send_message(chat_id, f"{ACK_TEXT}\n(задача {task_id})", reply_parameters=reply_to)
        self._store.set_ack_message(task_id, ack.message_id)
        self._store.link_messages(task_id, chat_id, [*source_message_ids, ack.message_id])
        try:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
        except TelegramAPIError as exc:
            logger.debug("Не удалось показать «печатает…»: %s", exc)

        payload: dict[str, Any] = {"outbox_dir": str(self._outbox_dir), **(extra or {})}
        if parent_task_id is not None:
            payload["parent_task_id"] = parent_task_id
        if attachments:
            payload["files"] = await download_attachments(
                bot, list(attachments), self._inbox_dir / task_id, self._max_download_bytes
            )

        try:
            await self._webhook.send_task(task_id=task_id, chat_id=chat_id, text=text, **payload)
        except WebhookError as exc:
            self._store.set_status(task_id, TaskStatus.FAILED)
            logger.error("Задача %s не передана: %s", task_id, exc)
            await ack.edit_text(f"⚠️ Не смог передать задачу {task_id} Джарвису.\nПричина: {exc}")
        return task_id
