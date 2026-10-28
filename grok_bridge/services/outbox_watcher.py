"""Слежение за runtime/outbox: доставка ответов Grok Bot в Telegram.

Протокол: ассистент сначала (опционально) кладёт <task_id>.png, затем <task_id>.txt.
Появление .txt означает, что ответ готов к отправке.
"""

import asyncio
import logging
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import FSInputFile

from grok_bridge.storage import TaskStatus, TaskStore

logger = logging.getLogger(__name__)

TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PHOTO_SUFFIXES = (".png", ".jpg", ".jpeg")
TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024


@dataclass(frozen=True, slots=True)
class OutboxItem:
    task_id: str
    text_path: Path
    photo_path: Path | None = None

    @property
    def files(self) -> list[Path]:
        return [p for p in (self.photo_path, self.text_path) if p is not None]

    def read_text(self) -> str:
        return self.text_path.read_text(encoding="utf-8").strip()


def scan_outbox(outbox_dir: Path, min_age_seconds: float = 0.0, now: float | None = None) -> list[OutboxItem]:
    """Готовые ответы в папке, от старых к новым.

    min_age_seconds защищает от чтения файла, который ещё дописывается.
    """
    if not outbox_dir.is_dir():
        return []
    now = time.time() if now is None else now

    candidates: list[tuple[float, Path]] = []
    for text_path in outbox_dir.glob("*.txt"):
        try:
            mtime = text_path.stat().st_mtime
        except FileNotFoundError:
            continue
        candidates.append((mtime, text_path))

    items: list[OutboxItem] = []
    for mtime, text_path in sorted(candidates):
        task_id = text_path.stem
        if not TASK_ID_RE.match(task_id):
            logger.warning("Пропускаю файл с некорректным task_id: %s", text_path.name)
            continue
        if now - mtime < min_age_seconds:
            continue
        photo = next(
            (p for p in (text_path.with_suffix(s) for s in PHOTO_SUFFIXES) if p.is_file()),
            None,
        )
        items.append(OutboxItem(task_id=task_id, text_path=text_path, photo_path=photo))
    return items


def split_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    """Режет длинный текст на части под лимит Telegram, по возможности по строкам."""
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest:
        chunks.append(rest)
    return chunks


def archive(item: OutboxItem, target_dir: Path) -> None:
    """Переносит файлы ответа в target_dir, не перезаписывая существующие."""
    target_dir.mkdir(parents=True, exist_ok=True)
    for path in item.files:
        destination = target_dir / path.name
        if destination.exists():
            destination = target_dir / f"{path.stem}.{int(time.time())}{path.suffix}"
        shutil.move(path, destination)


class OutboxWatcher:
    def __init__(
        self,
        bot: Bot,
        store: TaskStore,
        outbox_dir: Path,
        sent_dir: Path,
        failed_dir: Path,
        poll_seconds: float = 2.0,
        fallback_chat_id: int | None = None,
        max_attempts: int = 5,
        min_file_age_seconds: float = 0.5,
    ) -> None:
        self._bot = bot
        self._store = store
        self._outbox_dir = outbox_dir
        self._sent_dir = sent_dir
        self._failed_dir = failed_dir
        self._poll_seconds = poll_seconds
        self._fallback_chat_id = fallback_chat_id
        self._max_attempts = max_attempts
        self._min_file_age = min_file_age_seconds
        self._attempts: dict[str, int] = {}

    async def run(self) -> None:
        logger.info("Слежу за %s (каждые %s с)", self._outbox_dir, self._poll_seconds)
        while True:
            try:
                await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка при обработке outbox")
            await asyncio.sleep(self._poll_seconds)

    async def process_once(self) -> int:
        delivered = 0
        for item in scan_outbox(self._outbox_dir, min_age_seconds=self._min_file_age):
            if await self._deliver(item):
                delivered += 1
        return delivered

    def _resolve_chat_id(self, task_id: str) -> int | None:
        task = self._store.get(task_id)
        if task is not None:
            return task.chat_id
        if self._fallback_chat_id is not None:
            logger.info("Задача %s не найдена в базе, отправляю владельцу (%s)", task_id, self._fallback_chat_id)
        return self._fallback_chat_id

    async def _deliver(self, item: OutboxItem) -> bool:
        chat_id = self._resolve_chat_id(item.task_id)
        if chat_id is None:
            logger.error("Некуда отправить ответ %s: нет чата и ALLOWED_USER_IDS пуст", item.task_id)
            archive(item, self._failed_dir)
            return False

        try:
            await self._send(chat_id, item)
        except (TelegramAPIError, OSError) as exc:
            attempts = self._attempts.get(item.task_id, 0) + 1
            self._attempts[item.task_id] = attempts
            logger.warning("Не удалось отправить ответ %s (попытка %s/%s): %s",
                           item.task_id, attempts, self._max_attempts, exc)
            if attempts >= self._max_attempts:
                archive(item, self._failed_dir)
                self._store.set_status(item.task_id, TaskStatus.FAILED)
                self._attempts.pop(item.task_id, None)
            return False

        archive(item, self._sent_dir)
        self._store.set_status(item.task_id, TaskStatus.DONE)
        self._attempts.pop(item.task_id, None)
        logger.info("Ответ на задачу %s отправлен в чат %s", item.task_id, chat_id)
        return True

    async def _send(self, chat_id: int, item: OutboxItem) -> None:
        text = item.read_text()
        if item.photo_path is not None:
            caption = text if len(text) <= TELEGRAM_CAPTION_LIMIT else None
            await self._bot.send_photo(chat_id, FSInputFile(item.photo_path), caption=caption or None)
            if caption is not None:
                return
        elif not text:
            text = "(пустой ответ)"
        for chunk in split_text(text):
            await self._bot.send_message(chat_id, chunk)
