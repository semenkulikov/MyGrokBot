"""Создание задачи: запись в базу, «Принял, работаю…», вложения и вебхук."""

import logging
import secrets
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from aiogram import Bot
from aiogram.types import Message

from grok_bridge.services.attachments import AttachmentRef, download_attachments, extract_attachments
from grok_bridge.services.webhook_client import WebhookClient, WebhookError
from grok_bridge.storage import TaskStatus, TaskStore

logger = logging.getLogger(__name__)

ACK_TEXT = "Принял, работаю…"


def new_task_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def message_text(message: Message) -> str:
    return message.text or message.caption or ""


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

    async def submit_messages(self, bot: Bot, messages: Sequence[Message]) -> str:
        """Одна задача из сообщений пользователя (одного или пачки подряд)."""
        first = messages[0]
        refs = [ref for message in messages for ref in extract_attachments(message)]
        return await self.submit(
            bot,
            chat_id=first.chat.id,
            user_id=first.from_user.id if first.from_user else first.chat.id,
            text=combine_text(messages),
            attachments=refs,
        )

    async def submit(
        self,
        bot: Bot,
        *,
        chat_id: int,
        user_id: int,
        text: str,
        attachments: Sequence[AttachmentRef] = (),
        extra: dict[str, Any] | None = None,
    ) -> str:
        task_id = new_task_id()
        self._store.add(task_id=task_id, chat_id=chat_id, user_id=user_id, text=text)
        logger.info("Новая задача %s от %s: %s", task_id, user_id, _describe_for_log(text, attachments))

        ack = await bot.send_message(chat_id, f"{ACK_TEXT}\n(задача {task_id})")

        payload: dict[str, Any] = {"outbox_dir": str(self._outbox_dir), **(extra or {})}
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
