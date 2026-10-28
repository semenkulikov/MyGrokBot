"""Хранилище задач: соответствие task_id -> chat_id и статус (SQLite в runtime/)."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path


class TaskStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Task:
    task_id: str
    chat_id: int
    user_id: int
    text: str
    status: TaskStatus
    created_at: str
    updated_at: str
    parent_task_id: str | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id    TEXT PRIMARY KEY,
    chat_id    INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    text       TEXT NOT NULL,
    status     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status);
CREATE TABLE IF NOT EXISTS task_messages (
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    task_id    TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);
"""

# Колонки, добавленные после первой версии: миграция через ALTER TABLE на старой базе
_ADDED_COLUMNS: dict[str, str] = {
    "parent_task_id": "TEXT",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TaskStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(tasks)")}
        for column, ddl in _ADDED_COLUMNS.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE tasks ADD COLUMN {column} {ddl}")

    def add(
        self,
        task_id: str,
        chat_id: int,
        user_id: int,
        text: str,
        parent_task_id: str | None = None,
    ) -> Task:
        now = _now()
        with self._conn:
            self._conn.execute(
                "INSERT INTO tasks (task_id, chat_id, user_id, text, status, created_at, updated_at, parent_task_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, chat_id, user_id, text, TaskStatus.PENDING, now, now, parent_task_id),
            )
        return Task(task_id, chat_id, user_id, text, TaskStatus.PENDING, now, now, parent_task_id)

    def link_messages(self, task_id: str, chat_id: int, message_ids: list[int]) -> None:
        """Запоминает, к какой задаче относятся сообщения чата (для ответов reply)."""
        if not message_ids:
            return
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO task_messages (chat_id, message_id, task_id) VALUES (?, ?, ?)",
                [(chat_id, message_id, task_id) for message_id in message_ids],
            )

    def task_for_message(self, chat_id: int, message_id: int) -> str | None:
        row = self._conn.execute(
            "SELECT task_id FROM task_messages WHERE chat_id = ? AND message_id = ?", (chat_id, message_id)
        ).fetchone()
        return row["task_id"] if row else None

    def get(self, task_id: str) -> Task | None:
        row = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def set_status(self, task_id: str, status: TaskStatus) -> bool:
        with self._conn:
            cursor = self._conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (status, _now(), task_id),
            )
        return cursor.rowcount > 0

    def pending(self) -> list[Task]:
        rows = self._conn.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY created_at", (TaskStatus.PENDING,)
        ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def count_pending(self) -> int:
        (count,) = self._conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status = ?", (TaskStatus.PENDING,)
        ).fetchone()
        return int(count)

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Task:
        return Task(
            task_id=row["task_id"],
            chat_id=row["chat_id"],
            user_id=row["user_id"],
            text=row["text"],
            status=TaskStatus(row["status"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            parent_task_id=row["parent_task_id"],
        )
