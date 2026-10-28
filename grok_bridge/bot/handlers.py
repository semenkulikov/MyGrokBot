"""Обработчики Telegram-команд и сообщений."""

import logging
import time

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from grok_bridge.bot.middlewares import AllowedUsersMiddleware
from grok_bridge.services.task_service import TaskService
from grok_bridge.services.webhook_client import WebhookClient
from grok_bridge.storage import TaskStore

logger = logging.getLogger(__name__)

# Сообщения, которые бот превращает в задачи
TASK_FILTER = F.text | F.caption | F.voice | F.audio | F.video_note


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
        "Пиши задачу текстом или голосовым: я передам её и пришлю сюда ответ.\n\n"
        "/status — состояние бота\n/id — твой Telegram ID"
    )


async def cmd_status(message: Message, store: TaskStore, webhook: WebhookClient, started_at: float) -> None:
    await message.answer(
        "Бот работает ✅\n"
        f"Аптайм: {_format_uptime(time.monotonic() - started_at)}\n"
        f"Задач в работе: {store.count_pending()}\n"
        f"Вебхук: {'настроен' if webhook.configured else 'не настроен (WEBHOOK_URL пуст)'}"
    )


async def handle_task(message: Message, bot: Bot, task_service: TaskService) -> None:
    await task_service.submit_messages(bot, [message])


async def handle_unsupported(message: Message) -> None:
    await message.answer("Пока понимаю только текст и голосовые сообщения.")


def build_routers(allowed_user_ids: list[int]) -> list[Router]:
    """Публичный роутер (/id) идёт первым, остальное — только для разрешённых ID."""
    public = Router(name="public")
    public.message.register(cmd_id, Command("id"))

    private = Router(name="private")
    private.message.outer_middleware(AllowedUsersMiddleware(allowed_user_ids))
    private.message.register(cmd_start, CommandStart())
    private.message.register(cmd_status, Command("status"))
    private.message.register(handle_task, TASK_FILTER)
    private.message.register(handle_unsupported)

    return [public, private]
