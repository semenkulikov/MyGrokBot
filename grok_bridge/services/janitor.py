"""Периодическая чистка: старые ответы, вложения и ротированные логи.

Удаляются элементы runtime/sent, runtime/failed, runtime/inbox и файлы logs/*.log.N,
которые не менялись дольше CLEANUP_MAX_AGE_DAYS. База задач и текущие логи не трогаются.
"""

import asyncio
import logging
import shutil
import time
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)


def _age_reference(path: Path) -> float:
    """Время последнего изменения: для папки — самое свежее среди её файлов."""
    latest = path.stat().st_mtime
    if path.is_dir():
        for child in path.rglob("*"):
            try:
                latest = max(latest, child.stat().st_mtime)
            except FileNotFoundError:
                continue
    return latest


def cleanup_dir(directory: Path, max_age_seconds: float, now: float | None = None) -> int:
    """Удаляет файлы и папки первого уровня старше max_age_seconds. Возвращает их количество."""
    if not directory.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - max_age_seconds
    removed = 0
    for path in directory.iterdir():
        try:
            if _age_reference(path) >= cutoff:
                continue
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
        except OSError as exc:
            logger.warning("Не удалось удалить %s: %s", path, exc)
            continue
        removed += 1
    return removed


def cleanup_rotated_logs(log_dir: Path, max_age_seconds: float, now: float | None = None) -> int:
    """Удаляет ротированные логи (bot.log.1, …) старше max_age_seconds; текущие *.log не трогает."""
    if not log_dir.is_dir():
        return 0
    cutoff = (time.time() if now is None else now) - max_age_seconds
    removed = 0
    for path in log_dir.glob("*.log.*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError as exc:
            logger.warning("Не удалось удалить %s: %s", path, exc)
    return removed


class Janitor:
    def __init__(
        self,
        runtime_dirs: Iterable[Path],
        log_dir: Path,
        max_age_days: float,
        interval_hours: float = 6.0,
    ) -> None:
        self._runtime_dirs = list(runtime_dirs)
        self._log_dir = log_dir
        self._max_age = max_age_days * 86400
        self._interval = interval_hours * 3600

    def run_once(self) -> int:
        removed = sum(cleanup_dir(directory, self._max_age) for directory in self._runtime_dirs)
        removed += cleanup_rotated_logs(self._log_dir, self._max_age)
        if removed:
            logger.info("Чистка: удалено %s старых файлов/папок", removed)
        return removed

    async def run(self) -> None:
        logger.info("Чистка файлов старше %s дн. каждые %s ч", self._max_age / 86400, self._interval / 3600)
        while True:
            try:
                await asyncio.to_thread(self.run_once)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка при чистке старых файлов")
            await asyncio.sleep(self._interval)
