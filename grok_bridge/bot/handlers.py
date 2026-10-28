"""Обработчики Telegram-команд и сообщений."""

import logging
import secrets
import time
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from grok_bridge.bot.middlewares import AllowedUsersMiddleware
from grok_bridge.config import Settings
from grok_bridge.services.webhook_client import WebhookClient, WebhookError
from grok_bridge.storage import TaskStatus, TaskStore

logger = logging.getLogger(__name__)

ACK_TEXT = "Принял, работаю…"


def new_task_id() -> str:
    return f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"


def _format_uptime(seconds: float) -> str:
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    return f"{days}д {hours:02}:{minutes:02}:{sec:02}" if days else f"{hours:02}:{minutes:02}:{sec:02}"


async def cmd_id(message: Message) -> None:
    """Доступна всем, чтобы можно было узнать свой ID для ALLOWED_USER_IDS."""
    user_id = message.from_user.id if message.from_user else message.chat.id
    logger.info("/id запрошен пользователем %s", user_id)
    await message.answer(str(user_id))


async def cmd_start(message: Message) -> None:
    await message.answer(
        "Привет! Я мост к Джарвису (Grok Bot).\n"
        "Пиши задачу обычным сообщением: я передам её и пришлю сюда ответ.\n\n"
        "/status — состояние бота\n/id — твой Telegram ID"
    )


async def cmd_status(message: Message, store: TaskStore, webhook: WebhookClient, started_at: float) -> None:
    await message.answer(
        "Бот работает ✅\n"
        f"Аптайм: {_format_uptime(time.monotonic() - started_at)}\n"
        f"Задач в работе: {store.count_pending()}\n"
        f"Вебхук: {'настроен' if webhook.configured else 'не настроен (WEBHOOK_URL пуст)'}"
    )


async def handle_task(message: Message, store: TaskStore, webhook: WebhookClient, settings: Settings) -> None:
    text = message.text or message.caption or ""
    user_id = message.from_user.id if message.from_user else message.chat.id
    task_id = new_task_id()
    store.add(task_id=task_id, chat_id=message.chat.id, user_id=user_id, text=text)
    logger.info("Новая задача %s от %s: %r", task_id, user_id, text[:200])

    ack = await message.answer(f"{ACK_TEXT}\n(задача {task_id})")
    try:
        await webhook.send_task(
            task_id=task_id,
            chat_id=message.chat.id,
            text=text,
            outbox_dir=str(settings.outbox_dir),
        )
    except WebhookError as exc:
        store.set_status(task_id, TaskStatus.FAILED)
        logger.error("Задача %s не передана: %s", task_id, exc)
        await ack.edit_text(f"⚠️ Не смог передать задачу {task_id} Джарвису.\nПричина: {exc}")


async def handle_unsupported(message: Message) -> None:
    await message.answer("Пока понимаю только текстовые сообщения.")


def build_routers(allowed_user_ids: list[int]) -> list[Router]:
    """Публичный роутер (/id) идёт первым, остальное — только для разрешённых ID."""
    public = Router(name="public")
    public.message.register(cmd_id, Command("id"))

    private = Router(name="private")
    private.message.outer_middleware(AllowedUsersMiddleware(allowed_user_ids))
    private.message.register(cmd_start, CommandStart())
    private.message.register(cmd_status, Command("status"))
    private.message.register(handle_task, F.text | F.caption)
    private.message.register(handle_unsupported)

    return [public, private]
