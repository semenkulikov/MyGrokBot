from pathlib import Path

from grok_bridge.bot.handlers import cancel_task, format_tasks
from grok_bridge.storage import TaskStatus, TaskStore


def test_format_tasks(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    assert "нет" in format_tasks(store.pending())
    store.add("t1", 1, 1, "очень " * 30)
    text = format_tasks(store.pending(chat_id=1))
    assert "Задач в работе: 1" in text and "t1" in text and "…" in text


def test_cancel_last_and_by_id(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    store.add("a", 1, 1, "x")
    store.add("b", 1, 1, "y")
    store.add("other", 2, 2, "z")

    task, reply = cancel_task(store, chat_id=1, task_id=None)
    assert task is not None and task.task_id == "b" and "отменена" in reply
    assert store.get("b").status is TaskStatus.CANCELLED  # type: ignore[union-attr]

    task, reply = cancel_task(store, chat_id=1, task_id="b")
    assert task is None and "уже не в работе" in reply
    task, reply = cancel_task(store, chat_id=1, task_id="other")  # чужой чат
    assert task is None and "не найдена" in reply

    task, _ = cancel_task(store, chat_id=1, task_id="a")
    assert task is not None
    assert cancel_task(store, chat_id=1, task_id=None) == (None, "Нечего отменять: задач в работе нет.")
