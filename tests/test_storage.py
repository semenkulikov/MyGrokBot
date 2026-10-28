from pathlib import Path

from grok_bridge.storage import TaskStatus, TaskStore


def test_add_and_get(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    store.add("t1", chat_id=100, user_id=200, text="привет")

    task = store.get("t1")
    assert task is not None
    assert task.chat_id == 100
    assert task.user_id == 200
    assert task.text == "привет"
    assert task.status is TaskStatus.PENDING
    assert store.get("missing") is None


def test_status_and_pending_count(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    store.add("a", 1, 1, "x")
    store.add("b", 1, 1, "y")
    assert store.count_pending() == 2

    assert store.set_status("a", TaskStatus.DONE)
    assert not store.set_status("missing", TaskStatus.DONE)
    assert store.count_pending() == 1
    assert [t.task_id for t in store.pending()] == ["b"]


def test_persists_between_instances(tmp_path: Path) -> None:
    db = tmp_path / "tasks.sqlite3"
    first = TaskStore(db)
    first.add("t1", 42, 42, "text")
    first.close()

    second = TaskStore(db)
    task = second.get("t1")
    assert task is not None and task.chat_id == 42
