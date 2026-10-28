"""Кнопки под ответом: формат <task_id>.buttons.json и inline-клавиатура.

Формат файла (любой из вариантов):
    ["Да", "Нет"]                                   — кнопки в один ряд (больше 3 — по одной в ряд)
    [{"text": "Да, запускай", "value": "yes"}, ...] — text на кнопке, value уходит задачей
    [["Да", "Нет"], ["Отмена"]]                     — явные ряды
    {"buttons": [...]}                              — то же самое внутри объекта
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CALLBACK_PREFIX = "btn:"
MAX_BUTTONS = 24
MAX_TEXT_LENGTH = 64
MAX_VALUE_LENGTH = 1000
AUTO_ROW_LIMIT = 3


class ButtonsError(ValueError):
    """Файл кнопок не удалось разобрать."""


@dataclass(frozen=True, slots=True)
class ButtonSpec:
    text: str
    value: str


def _parse_button(raw: Any) -> ButtonSpec:
    if isinstance(raw, str):
        text, value = raw, raw
    elif isinstance(raw, dict) and isinstance(raw.get("text"), str):
        text = raw["text"]
        value = raw.get("value", text)
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
    else:
        raise ButtonsError(f"кнопка должна быть строкой или объектом с text: {raw!r}")
    text = text.strip()
    if not text:
        raise ButtonsError("пустой текст кнопки")
    return ButtonSpec(text=text[:MAX_TEXT_LENGTH], value=value[:MAX_VALUE_LENGTH])


def parse_buttons(data: Any) -> list[list[ButtonSpec]]:
    """Ряды кнопок из JSON-данных."""
    if isinstance(data, dict):
        data = data.get("buttons")
    if not isinstance(data, list) or not data:
        raise ButtonsError("ожидается непустой список кнопок")

    if any(isinstance(item, list) for item in data):
        rows = [[_parse_button(b) for b in (item if isinstance(item, list) else [item])] for item in data]
    else:
        buttons = [_parse_button(item) for item in data]
        rows = [buttons] if len(buttons) <= AUTO_ROW_LIMIT else [[b] for b in buttons]

    rows = [row for row in rows if row]
    if sum(len(row) for row in rows) > MAX_BUTTONS:
        raise ButtonsError(f"слишком много кнопок (максимум {MAX_BUTTONS})")
    return rows


def load_buttons(path: Path) -> list[list[ButtonSpec]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ButtonsError(f"не читается {path.name}: {exc}") from exc
    return parse_buttons(data)


def build_keyboard(rows: list[list[tuple[int, str]]]) -> InlineKeyboardMarkup:
    """rows — (id кнопки в базе, текст)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=f"{CALLBACK_PREFIX}{button_id}") for button_id, text in row]
            for row in rows
        ]
    )


def parse_callback(data: str | None) -> int | None:
    if not data or not data.startswith(CALLBACK_PREFIX):
        return None
    try:
        return int(data[len(CALLBACK_PREFIX):])
    except ValueError:
        return None
