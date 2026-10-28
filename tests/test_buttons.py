import json
from pathlib import Path

import pytest

from grok_bridge.services.buttons import (
    ButtonSpec,
    ButtonsError,
    build_keyboard,
    load_buttons,
    parse_buttons,
    parse_callback,
)


def test_flat_list_is_one_row() -> None:
    assert parse_buttons(["Да", "Нет"]) == [[ButtonSpec("Да", "Да"), ButtonSpec("Нет", "Нет")]]


def test_many_buttons_go_one_per_row() -> None:
    rows = parse_buttons(["1", "2", "3", "4"])
    assert [len(row) for row in rows] == [1, 1, 1, 1]


def test_objects_rows_and_wrapper() -> None:
    rows = parse_buttons({"buttons": [[{"text": "Да, запускай", "value": "yes"}, "Нет"], ["Отмена"]]})
    assert rows == [[ButtonSpec("Да, запускай", "yes"), ButtonSpec("Нет", "Нет")], [ButtonSpec("Отмена", "Отмена")]]


@pytest.mark.parametrize("bad", [[], {}, "Да", [""], [{"value": "x"}], [1], ["x"] * 30])
def test_invalid(bad: object) -> None:
    with pytest.raises(ButtonsError):
        parse_buttons(bad)


def test_load_and_keyboard(tmp_path: Path) -> None:
    path = tmp_path / "t.buttons.json"
    path.write_text(json.dumps(["Да"]), encoding="utf-8")
    assert load_buttons(path) == [[ButtonSpec("Да", "Да")]]
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ButtonsError):
        load_buttons(path)

    keyboard = build_keyboard([[(5, "Да"), (6, "Нет")]])
    assert [b.callback_data for b in keyboard.inline_keyboard[0]] == ["btn:5", "btn:6"]
    assert parse_callback("btn:5") == 5
    assert parse_callback("btn:x") is None and parse_callback(None) is None
