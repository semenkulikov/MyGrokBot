"""Слежение за runtime/outbox: доставка ответов Grok Bot в Telegram.

Протокол: ассистент сначала (опционально) кладёт <task_id>.png, затем <task_id>.txt.
Появление .txt означает, что ответ готов к отправке.
Папка <task_id>.files/ (кладётся до .txt) — любые файлы в ответ: картинки уходят фото/альбомом,
остальное документами.
<task_id>.buttons.json (кладётся до .txt) — варианты ответа, бот покажет их inline-кнопками.
Пока задача в работе, <task_id>.progress.txt — промежуточный статус: бот подставляет его
в сообщение «Принял, работаю…» и удаляет файл.
"""

import asyncio
import logging
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InputMediaPhoto, Message, ReplyParameters

from grok_bridge.services.buttons import ButtonsError, build_keyboard, load_buttons
from grok_bridge.services.formatting import is_parse_error, markdown_to_html
from grok_bridge.storage import TaskStatus, TaskStore, parse_time

logger = logging.getLogger(__name__)

TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PHOTO_SUFFIXES = (".png", ".jpg", ".jpeg")
TELEGRAM_TEXT_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024
PROGRESS_SUFFIX = ".progress.txt"
BUTTONS_SUFFIX = ".buttons.json"
FILES_DIR_SUFFIX = ".files"
ALBUM_PHOTO_SUFFIXES = (".png", ".jpg", ".jpeg")
VIDEO_SUFFIXES = (".mp4", ".mov")
MEDIA_GROUP_LIMIT = 10
# Сырой текст режется с запасом: HTML-разметка удлиняет его
MARKDOWN_CHUNK_LIMIT = 3500


@dataclass(frozen=True, slots=True)
class OutboxItem:
    task_id: str
    text_path: Path
    photo_path: Path | None = None
    buttons_path: Path | None = None
    files_dir: Path | None = None

    @property
    def files(self) -> list[Path]:
        paths = (self.photo_path, self.files_dir, self.buttons_path, self.text_path)
        return [p for p in paths if p is not None]

    def attachments(self) -> list[Path]:
        """Файлы из <task_id>.files/ по имени (скрытые и вложенные папки пропускаются)."""
        if self.files_dir is None:
            return []
        return sorted(p for p in self.files_dir.iterdir() if p.is_file() and not p.name.startswith("."))

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
        if text_path.name.endswith(PROGRESS_SUFFIX):
            continue
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
        buttons = outbox_dir / f"{task_id}{BUTTONS_SUFFIX}"
        files_dir = outbox_dir / f"{task_id}{FILES_DIR_SUFFIX}"
        items.append(
            OutboxItem(
                task_id=task_id,
                text_path=text_path,
                photo_path=photo,
                buttons_path=buttons if buttons.is_file() else None,
                files_dir=files_dir if files_dir.is_dir() else None,
            )
        )
    return items


def scan_progress(outbox_dir: Path, min_age_seconds: float = 0.0, now: float | None = None) -> list[tuple[str, Path]]:
    """Файлы <task_id>.progress.txt: (task_id, путь), от старых к новым."""
    if not outbox_dir.is_dir():
        return []
    now = time.time() if now is None else now
    found: list[tuple[float, str, Path]] = []
    for path in outbox_dir.glob(f"*{PROGRESS_SUFFIX}"):
        task_id = path.name[: -len(PROGRESS_SUFFIX)]
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            continue
        if TASK_ID_RE.match(task_id) and now - mtime >= min_age_seconds:
            found.append((mtime, task_id, path))
    return [(task_id, path) for _, task_id, path in sorted(found)]


def progress_message(task_id: str, progress: str) -> str:
    text = f"⏳ {progress}\n(задача {task_id})"
    if len(text) > TELEGRAM_TEXT_LIMIT:
        keep = TELEGRAM_TEXT_LIMIT - len(f"⏳ …\n(задача {task_id})")
        text = f"⏳ {progress[:keep]}…\n(задача {task_id})"
    return text


def split_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    """Режет длинный текст на части под лимит Telegram: по абзацам, строкам, пробелам."""
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = -1
        for separator in ("\n\n", "\n", " "):
            cut = rest.rfind(separator, 0, limit)
            if cut > limit // 2:  # не режем слишком мелко ради красивой границы
                break
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
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
        typing_interval_seconds: float | None = 4.5,
        task_timeout_seconds: float | None = 15 * 60,
        markdown: bool = True,
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
        self._typing_interval = typing_interval_seconds
        self._last_typing = 0.0
        self._task_timeout = task_timeout_seconds
        self._markdown = markdown

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
        await self._apply_progress()
        await self._show_typing()
        await self._notify_overdue()
        delivered = 0
        for item in scan_outbox(self._outbox_dir, min_age_seconds=self._min_file_age):
            if await self._deliver(item):
                delivered += 1
        return delivered

    async def _apply_progress(self) -> None:
        for task_id, path in scan_progress(self._outbox_dir, min_age_seconds=self._min_file_age):
            try:
                progress = path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                logger.warning("Не удалось прочитать %s: %s", path.name, exc)
                continue
            path.unlink(missing_ok=True)
            task = self._store.get(task_id)
            if task is None or task.status is not TaskStatus.PENDING or task.ack_message_id is None or not progress:
                logger.debug("Пропускаю промежуточный статус для %s", task_id)
                continue
            try:
                await self._bot.edit_message_text(
                    progress_message(task_id, progress), chat_id=task.chat_id, message_id=task.ack_message_id
                )
            except TelegramBadRequest as exc:
                if "not modified" not in str(exc):
                    logger.warning("Не удалось обновить статус задачи %s: %s", task_id, exc)
            except TelegramAPIError as exc:
                logger.warning("Не удалось обновить статус задачи %s: %s", task_id, exc)
            else:
                logger.info("Статус задачи %s: %r", task_id, progress[:200])

    def _typing_chats(self) -> set[int]:
        """Чаты с задачами в работе; после таймаута «печатает…» больше не показываем."""
        tasks = self._store.pending()
        if self._task_timeout is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._task_timeout)
            tasks = [task for task in tasks if parse_time(task.created_at) >= cutoff]
        return {task.chat_id for task in tasks}

    async def _notify_overdue(self) -> None:
        """Один раз предупреждает, если на задачу долго нет ответа."""
        if self._task_timeout is None:
            return
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self._task_timeout)
        for task in self._store.overdue(cutoff):
            minutes = max(1, round(self._task_timeout / 60))
            reply_to = (
                ReplyParameters(message_id=task.ack_message_id, allow_sending_without_reply=True)
                if task.ack_message_id else None
            )
            try:
                await self._bot.send_message(
                    task.chat_id,
                    f"⌛ Задача {task.task_id} в работе уже больше {minutes} мин без ответа.\n"
                    "Возможно, она ещё выполняется, а может, зависла. Если ответ появится, пришлю его сюда.\n"
                    f"Отменить: /cancel {task.task_id}",
                    reply_parameters=reply_to,
                )
            except TelegramAPIError as exc:
                logger.warning("Не удалось предупредить о долгой задаче %s: %s", task.task_id, exc)
                continue
            self._store.mark_timeout_notified(task.task_id)
            logger.warning("Задача %s без ответа дольше %s мин", task.task_id, minutes)

    async def _show_typing(self) -> None:
        """«печатает…» в чатах, где есть задачи в работе (Telegram держит его ~5 с)."""
        if self._typing_interval is None:
            return
        now = time.monotonic()
        if now - self._last_typing < self._typing_interval:
            return
        self._last_typing = now
        for chat_id in self._typing_chats():
            try:
                await self._bot.send_chat_action(chat_id, ChatAction.TYPING)
            except TelegramAPIError as exc:
                logger.debug("Не удалось показать «печатает…» в %s: %s", chat_id, exc)

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
            task = self._store.get(item.task_id)
            cancelled = task is not None and task.status is TaskStatus.CANCELLED
            note = f"(Ответ на отменённую задачу {item.task_id})" if cancelled else None
            message_ids = await self._send(chat_id, item, note=note)
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
        self._store.link_messages(item.task_id, chat_id, message_ids)
        if not cancelled:  # отменённая остаётся отменённой, даже если ответ всё же пришёл
            self._store.set_status(item.task_id, TaskStatus.DONE)
        self._attempts.pop(item.task_id, None)
        logger.info("Ответ на задачу %s отправлен в чат %s", item.task_id, chat_id)
        return True

    def _keyboard(self, chat_id: int, item: OutboxItem) -> InlineKeyboardMarkup | None:
        if item.buttons_path is None:
            return None
        try:
            rows = load_buttons(item.buttons_path)
        except ButtonsError as exc:
            logger.warning("Кнопки для %s не показаны: %s", item.task_id, exc)
            return None
        saved = self._store.add_buttons(item.task_id, chat_id, [[(b.text, b.value) for b in row] for row in rows])
        return build_keyboard(saved)

    async def _send_files(self, chat_id: int, paths: list[Path]) -> list[int]:
        """Картинки — фото или альбомами по 10, видео — видео, остальное — документами."""
        photos = [p for p in paths if p.suffix.lower() in ALBUM_PHOTO_SUFFIXES]
        others = [p for p in paths if p not in photos]
        message_ids: list[int] = []
        for start in range(0, len(photos), MEDIA_GROUP_LIMIT):
            group = photos[start:start + MEDIA_GROUP_LIMIT]
            try:
                if len(group) == 1:
                    sent = await self._bot.send_photo(chat_id, FSInputFile(group[0]))
                    message_ids.append(sent.message_id)
                else:
                    media = [InputMediaPhoto(media=FSInputFile(p)) for p in group]
                    message_ids.extend(m.message_id for m in await self._bot.send_media_group(chat_id, media))
            except TelegramBadRequest as exc:
                # Например, слишком большая картинка для фото — отправим файлами
                logger.warning("Картинки не ушли как фото (%s), отправляю документами", exc)
                others = group + others
        for path in others:
            if path.suffix.lower() in VIDEO_SUFFIXES:
                sent = await self._bot.send_video(chat_id, FSInputFile(path))
            else:
                sent = await self._bot.send_document(chat_id, FSInputFile(path))
            message_ids.append(sent.message_id)
        return message_ids

    def _render(self, text: str, limit: int) -> tuple[str, ParseMode | None]:
        if self._markdown and text:
            rendered = markdown_to_html(text)
            if len(rendered) <= limit:
                return rendered, ParseMode.HTML
        return text, None

    async def _send_formatted(
        self, send: Callable[[str | None, ParseMode | None], Awaitable[Message]], text: str, limit: int
    ) -> Message:
        """Отправка с Markdown (через HTML); если Telegram не принял разметку — обычным текстом."""
        body, mode = self._render(text, limit)
        try:
            return await send(body or None, mode)
        except TelegramBadRequest as exc:
            if mode is None or not is_parse_error(exc):
                raise
            logger.warning("Telegram не принял разметку (%s), отправляю без форматирования", exc)
            return await send(text or None, None)

    async def _send(self, chat_id: int, item: OutboxItem, note: str | None = None) -> list[int]:
        """Отправляет ответ и возвращает id сообщений (чтобы reply на них продолжал задачу)."""
        text = item.read_text()
        if note:
            text = f"{note}\n\n{text}" if text else note
        keyboard = self._keyboard(chat_id, item)
        attachments = item.attachments()
        message_ids: list[int] = []
        if item.photo_path is not None:
            caption = text if len(text) <= TELEGRAM_CAPTION_LIMIT and not attachments else None
            photo = FSInputFile(item.photo_path)
            photo_markup = keyboard if caption is not None else None
            sent = await self._send_formatted(
                lambda body, mode: self._bot.send_photo(
                    chat_id, photo, caption=body, parse_mode=mode, reply_markup=photo_markup
                ),
                caption or "",
                TELEGRAM_CAPTION_LIMIT,
            )
            message_ids.append(sent.message_id)
            if caption is not None:
                return message_ids
        message_ids.extend(await self._send_files(chat_id, attachments))
        if not text and not message_ids:
            text = "(пустой ответ)"
        elif not text and keyboard is not None:
            text = "Выбери вариант:"
        chunks = split_text(text, MARKDOWN_CHUNK_LIMIT if self._markdown else TELEGRAM_TEXT_LIMIT)
        for index, chunk in enumerate(chunks):
            markup = keyboard if index == len(chunks) - 1 else None
            sent = await self._send_formatted(
                lambda body, mode, markup=markup: self._bot.send_message(
                    chat_id, body or "", parse_mode=mode, reply_markup=markup
                ),
                chunk,
                TELEGRAM_TEXT_LIMIT,
            )
            message_ids.append(sent.message_id)
        return message_ids
