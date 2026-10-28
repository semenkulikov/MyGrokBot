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
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TaskStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def add(self, task_id: str, chat_id: int, user_id: int, text: str) -> Task:
        now = _now()
        with self._conn:
            self._conn.execute(
                "INSERT INTO tasks (task_id, chat_id, user_id, text, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task_id, chat_id, user_id, text, TaskStatus.PENDING, now, now),
            )
        return Task(task_id, chat_id, user_id, text, TaskStatus.PENDING, now, now)

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
        )
