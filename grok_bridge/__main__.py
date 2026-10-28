"""Точка входа: python -m grok_bridge."""

import asyncio
import contextlib
import logging
import sys
import time

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, Message
from aiogram.utils.token import TokenValidationError, validate_token
from pydantic import ValidationError

from grok_bridge.bot.handlers import build_routers
from grok_bridge.config import ENV_FILE, Settings
from grok_bridge.logging_setup import setup_logging
from grok_bridge.services.batcher import MessageBatcher
from grok_bridge.services.outbox_watcher import OutboxWatcher
from grok_bridge.services.task_service import TaskService
from grok_bridge.services.webhook_client import WebhookClient
from grok_bridge.storage import TaskStore

logger = logging.getLogger("grok_bridge")

BOT_COMMANDS = [
    BotCommand(command="start", description="Что умеет бот"),
    BotCommand(command="tasks", description="Задачи в работе"),
    BotCommand(command="cancel", description="Отменить задачу: последнюю или /cancel <id>"),
    BotCommand(command="status", description="Состояние бота и задачи в работе"),
    BotCommand(command="id", description="Показать мой Telegram ID"),
]


async def run(settings: Settings) -> None:
    settings.ensure_dirs()
    store = TaskStore(settings.db_path)
    webhook = WebhookClient(
        url=settings.webhook_url,
        key=settings.webhook_key.get_secret_value(),
        auth_header=settings.webhook_auth_header,
        auth_scheme=settings.webhook_auth_scheme,
        timeout_seconds=settings.webhook_timeout_seconds,
        max_attempts=settings.webhook_max_attempts,
    )
    bot = Bot(token=settings.bot_token.get_secret_value())

    task_service = TaskService(
        store=store,
        webhook=webhook,
        outbox_dir=settings.outbox_dir,
        inbox_dir=settings.inbox_dir,
        max_download_bytes=settings.max_download_bytes,
    )

    batcher: MessageBatcher[Message] = MessageBatcher(
        flush=lambda messages: task_service.submit_messages(bot, messages),
        key=lambda message: message.chat.id,
        delay_seconds=settings.message_debounce_seconds,
    )

    dp = Dispatcher(
        settings=settings,
        store=store,
        webhook=webhook,
        task_service=task_service,
        batcher=batcher,
        started_at=time.monotonic(),
    )
    dp.include_routers(*build_routers(settings.allowed_user_ids))

    watcher = OutboxWatcher(
        bot=bot,
        store=store,
        outbox_dir=settings.outbox_dir,
        sent_dir=settings.sent_dir,
        failed_dir=settings.failed_dir,
        poll_seconds=settings.outbox_poll_seconds,
        fallback_chat_id=settings.allowed_user_ids[0] if settings.allowed_user_ids else None,
        typing_interval_seconds=4.5 if settings.typing_indicator else None,
        task_timeout_seconds=settings.task_timeout_minutes * 60 or None,
    )
    watcher_task = asyncio.create_task(watcher.run(), name="outbox-watcher")

    try:
        with contextlib.suppress(Exception):
            await bot.set_my_commands(BOT_COMMANDS)
        logger.info("Бот запущен, разрешённые ID: %s", settings.allowed_user_ids or "—")
        await dp.start_polling(bot)
    finally:
        await batcher.close()
        watcher_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher_task
        await webhook.close()
        await bot.session.close()
        store.close()
        logger.info("Бот остановлен")


def main() -> int:
    try:
        settings = Settings()
    except ValidationError as exc:
        print(f"Ошибка в настройках ({ENV_FILE}):\n{exc}", file=sys.stderr)
        return 2

    setup_logging(settings.log_dir, settings.log_level)

    if not settings.bot_token.get_secret_value():
        logger.error("BOT_TOKEN не задан. Впишите токен от @BotFather в %s (пример — .env.example).", ENV_FILE)
        return 2
    try:
        validate_token(settings.bot_token.get_secret_value())
    except TokenValidationError:
        logger.error("BOT_TOKEN имеет неверный формат. Проверьте значение в %s.", ENV_FILE)
        return 2
    if not settings.allowed_user_ids:
        logger.warning("ALLOWED_USER_IDS пуст: бот ответит только на /id. Узнайте свой ID и впишите его в .env.")
    if not settings.webhook_url:
        logger.warning("WEBHOOK_URL не задан: задачи не будут передаваться Grok Bot.")

    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
