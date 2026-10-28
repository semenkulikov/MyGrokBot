"""Обработчики Telegram-команд и сообщений."""

import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from grok_bridge.bot.middlewares import AllowedUsersMiddleware
from grok_bridge.services.batcher import MessageBatcher
from grok_bridge.services.buttons import CALLBACK_PREFIX, parse_callback
from grok_bridge.services.task_service import TaskService
from grok_bridge.services.webhook_client import WebhookClient
from grok_bridge.storage import TaskStore

logger = logging.getLogger(__name__)

# Сообщения, которые бот превращает в задачи
TASK_FILTER = (
    F.text | F.caption | F.voice | F.audio | F.video_note | F.photo | F.document | F.video
    | F.animation | F.sticker | F.location | F.contact | F.forward_origin
)


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
        "Пиши задачу текстом или голосовым, присылай фото и файлы: я передам их и пришлю сюда ответ.\n\n"
        "/status — состояние бота\n/id — твой Telegram ID"
    )


async def cmd_status(message: Message, store: TaskStore, webhook: WebhookClient, started_at: float) -> None:
    await message.answer(
        "Бот работает ✅\n"
        f"Аптайм: {_format_uptime(time.monotonic() - started_at)}\n"
        f"Задач в работе: {store.count_pending()}\n"
        f"Вебхук: {'настроен' if webhook.configured else 'не настроен (WEBHOOK_URL пуст)'}"
    )


async def handle_task(message: Message, batcher: MessageBatcher[Message]) -> None:
    """Сообщения копятся и через MESSAGE_DEBOUNCE_SECONDS тишины становятся одной задачей."""
    await batcher.add(message)


async def handle_button(callback: CallbackQuery, bot: Bot, store: TaskStore, task_service: TaskService) -> None:
    """Нажатие кнопки из <task_id>.buttons.json: выбор отправляется Джарвису новой задачей."""
    button_id = parse_callback(callback.data)
    button = store.get_button(button_id) if button_id is not None else None
    if button is None or not store.use_buttons_of(button.button_id):
        await callback.answer("Эта кнопка уже неактуальна", show_alert=False)
        return

    await callback.answer(f"Выбрано: {button.text}")
    message = callback.message
    message_id = message.message_id if message is not None else None
    if isinstance(message, Message):
        try:
            await message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError as exc:  # клавиатуру убрать не критично
            logger.debug("Не удалось убрать кнопки: %s", exc)
    logger.info("Нажата кнопка %r (задача %s)", button.text, button.task_id)
    await task_service.submit_button(bot, user_id=callback.from_user.id, button=button, message_id=message_id)


async def handle_unsupported(message: Message) -> None:
    await message.answer("Такой тип сообщений пока не поддерживается: пришли текст, голосовое, фото или файл.")


def build_routers(allowed_user_ids: list[int]) -> list[Router]:
    """Публичный роутер (/id) идёт первым, остальное — только для разрешённых ID."""
    public = Router(name="public")
    public.message.register(cmd_id, Command("id"))

    private = Router(name="private")
    private.message.outer_middleware(AllowedUsersMiddleware(allowed_user_ids))
    private.callback_query.outer_middleware(AllowedUsersMiddleware(allowed_user_ids))
    private.callback_query.register(handle_button, F.data.startswith(CALLBACK_PREFIX))
    private.message.register(cmd_start, CommandStart())
    private.message.register(cmd_status, Command("status"))
    private.message.register(handle_task, TASK_FILTER)
    private.message.register(handle_unsupported)

    return [public, private]
