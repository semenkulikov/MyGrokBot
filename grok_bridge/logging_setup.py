"""Настройка logging: консоль + ротируемый файл logs/bot.log."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
LOG_FILE_NAME = "bot.log"


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        log_dir / LOG_FILE_NAME,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    # Сторонние библиотеки слишком болтливы на INFO
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
