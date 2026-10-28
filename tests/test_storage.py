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


def test_message_links_and_parent(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    store.add("parent", 1, 1, "x")
    store.add("child", 1, 1, "y", parent_task_id="parent")
    store.link_messages("parent", chat_id=1, message_ids=[10, 11])

    assert store.task_for_message(1, 11) == "parent"
    assert store.task_for_message(2, 11) is None
    child = store.get("child")
    assert child is not None and child.parent_task_id == "parent"


def test_migrates_old_schema(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE tasks (task_id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, user_id INTEGER NOT NULL,"
        " text TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);"
        "INSERT INTO tasks VALUES ('old', 1, 1, 't', 'done', '2026-10-28T00:00:00+00:00', '2026-10-28T00:00:00+00:00');"
    )
    conn.commit()
    conn.close()

    store = TaskStore(db)
    old = store.get("old")
    assert old is not None and old.status is TaskStatus.DONE and old.parent_task_id is None
    store.add("new", 1, 1, "n", parent_task_id="old")
