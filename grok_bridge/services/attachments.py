"""Вложения из Telegram: что прислал пользователь и как сохранить это на Мак.

Бот только скачивает файлы в runtime/inbox/<task_id>/ и передаёт Grok Bot их
абсолютные пути. Распознавание (например, расшифровку голосовых) делает Grok Bot.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

logger = logging.getLogger(__name__)

_SAFE_NAME_RE = re.compile(r"[^\w.-]+", re.UNICODE)

# Расширения по умолчанию, если Telegram не вернул путь с расширением
_DEFAULT_SUFFIX = {
    "photo": ".jpg",
    "video": ".mp4",
    "animation": ".mp4",
    "sticker": ".webp",
    "voice": ".ogg",
    "audio": ".mp3",
    "video_note": ".mp4",
}


@dataclass(frozen=True, slots=True)
class AttachmentRef:
    """Ссылка на файл в Telegram (ещё не скачанный)."""

    type: str
    file_id: str
    caption: str | None = None
    file_name: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    duration: int | None = None


class FileDownloader(Protocol):
    """Часть aiogram.Bot, нужная для скачивания (упрощает тесты)."""

    async def get_file(self, file_id: str) -> Any: ...

    async def download_file(self, file_path: str, destination: Path) -> Any: ...


def _attr(obj: Any, name: str) -> Any:
    return getattr(obj, name, None)


def extract_attachments(message: Message) -> list[AttachmentRef]:
    """Все вложения одного сообщения. Подпись (caption) достаётся первому вложению."""
    refs: list[AttachmentRef] = []
    caption = message.caption or None

    def add(kind: str, media: Any) -> None:
        nonlocal caption
        refs.append(
            AttachmentRef(
                type=kind,
                file_id=media.file_id,
                caption=caption,
                file_name=_attr(media, "file_name"),
                mime_type=_attr(media, "mime_type"),
                file_size=_attr(media, "file_size"),
                duration=_attr(media, "duration"),
            )
        )
        caption = None

    if message.photo:
        add("photo", message.photo[-1])  # самый крупный размер
    if message.animation is not None:
        add("animation", message.animation)  # у GIF Telegram дублирует его в document
    elif message.document is not None:
        add("document", message.document)
    for kind in ("video", "sticker", "voice", "audio", "video_note"):
        media = getattr(message, kind, None)
        if media is not None:
            add(kind, media)
    return refs


def _user_name(user: Any) -> str:
    name = " ".join(part for part in (_attr(user, "first_name"), _attr(user, "last_name")) if part)
    username = _attr(user, "username")
    return f"{name} (@{username})" if username else name or "неизвестно"


def forward_source(message: Message) -> str | None:
    """От кого переслано сообщение (None, если оно не пересланное)."""
    origin = message.forward_origin
    if origin is None:
        return None
    if (user := _attr(origin, "sender_user")) is not None:
        return _user_name(user)
    if (name := _attr(origin, "sender_user_name")) is not None:
        return str(name)
    chat = _attr(origin, "sender_chat") or _attr(origin, "chat")
    if chat is not None:
        return _attr(chat, "title") or _attr(chat, "username") or str(_attr(chat, "id"))
    return "неизвестно"


def special_content_text(message: Message) -> str | None:
    """Текстовое описание содержимого без файла: геолокация, контакт, стикер."""
    if message.venue is not None:
        venue = message.venue
        return (f"[Место: {venue.title}, {venue.address} "
                f"({venue.location.latitude}, {venue.location.longitude})]")
    if message.location is not None:
        return f"[Геолокация: {message.location.latitude}, {message.location.longitude}]"
    if message.contact is not None:
        contact = message.contact
        name = " ".join(part for part in (contact.first_name, contact.last_name) if part)
        return f"[Контакт: {name}, {contact.phone_number}]"
    if message.sticker is not None:
        return f"[Стикер {message.sticker.emoji or ''}]".replace(" ]", "]")
    return None


def safe_file_name(name: str) -> str:
    """Имя файла без путей и спецсимволов (кириллица сохраняется)."""
    base = PurePosixPath(name.replace("\\", "/")).name
    cleaned = _SAFE_NAME_RE.sub("_", base).strip("._")
    return cleaned[:100] or "file"


def _target_name(index: int, ref: AttachmentRef, telegram_path: str | None) -> str:
    suffix = PurePosixPath(telegram_path).suffix if telegram_path else ""
    if ref.file_name:
        name = safe_file_name(ref.file_name)
        if not PurePosixPath(name).suffix and suffix:
            name += suffix
    else:
        name = ref.type + (suffix or _DEFAULT_SUFFIX.get(ref.type, ""))
    return f"{index:02d}-{name}"


def describe(ref: AttachmentRef, path: Path | None, error: str | None = None) -> dict[str, Any]:
    """Элемент массива "files" в payload вебхука."""
    info: dict[str, Any] = {
        "type": ref.type,
        "path": str(path) if path is not None else None,
        "caption": ref.caption,
        "file_name": ref.file_name,
        "mime_type": ref.mime_type,
        "file_size": ref.file_size,
    }
    if ref.duration is not None:
        info["duration"] = ref.duration
    if error is not None:
        info["error"] = error
    return info


async def download_attachments(
    bot: FileDownloader,
    refs: list[AttachmentRef],
    dest_dir: Path,
    max_bytes: int | None = None,
) -> list[dict[str, Any]]:
    """Скачивает вложения в dest_dir. Ошибки не прерывают задачу: элемент получает "error"."""
    if not refs:
        return []
    dest_dir.mkdir(parents=True, exist_ok=True)
    result: list[dict[str, Any]] = []
    for index, ref in enumerate(refs, start=1):
        if max_bytes is not None and ref.file_size and ref.file_size > max_bytes:
            error = f"файл больше лимита ({ref.file_size} > {max_bytes} байт)"
            logger.warning("Вложение %s не скачано: %s", ref.type, error)
            result.append(describe(ref, None, error))
            continue
        try:
            tg_file = await bot.get_file(ref.file_id)
            telegram_path = _attr(tg_file, "file_path")
            if not telegram_path:
                raise ValueError("Telegram не вернул путь к файлу")
            target = (dest_dir / _target_name(index, ref, telegram_path)).resolve()
            await bot.download_file(telegram_path, destination=target)
        except (TelegramAPIError, OSError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("Не удалось скачать вложение %s: %s", ref.type, error)
            result.append(describe(ref, None, error))
            continue
        logger.info("Вложение %s сохранено: %s", ref.type, target)
        result.append(describe(ref, target))
    return result
