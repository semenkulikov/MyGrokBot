import asyncio
from pathlib import Path

from grok_bridge.services.outbox_watcher import OutboxWatcher, progress_message, scan_outbox, scan_progress
from grok_bridge.storage import TaskStatus, TaskStore
from tests.helpers import FakeBot


def _watcher(tmp_path: Path, **kwargs: object) -> tuple[OutboxWatcher, TaskStore, FakeBot]:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    bot = FakeBot()
    watcher = OutboxWatcher(
        bot=bot,  # type: ignore[arg-type]
        store=store,
        outbox_dir=tmp_path / "outbox",
        sent_dir=tmp_path / "sent",
        failed_dir=tmp_path / "failed",
        fallback_chat_id=1,
        min_file_age_seconds=0,
        **kwargs,  # type: ignore[arg-type]
    )
    (tmp_path / "outbox").mkdir()
    return watcher, store, bot


def test_delivers_text_and_links_messages(tmp_path: Path) -> None:
    watcher, store, bot = _watcher(tmp_path, typing_interval_seconds=None)
    store.add("t1", 42, 42, "задача")
    (tmp_path / "outbox" / "t1.txt").write_text("готово", encoding="utf-8")

    assert asyncio.run(watcher.process_once()) == 1

    assert [(m.chat_id, m.text) for m in bot.sent] == [(42, "готово")]
    task = store.get("t1")
    assert task is not None and task.status is TaskStatus.DONE
    assert store.task_for_message(42, bot.sent[0].message_id) == "t1"
    assert (tmp_path / "sent" / "t1.txt").is_file()


def test_progress_edits_ack_and_is_removed(tmp_path: Path) -> None:
    watcher, store, bot = _watcher(tmp_path, typing_interval_seconds=None)
    store.add("t1", 42, 42, "задача")
    store.set_ack_message("t1", 77)
    progress = tmp_path / "outbox" / "t1.progress.txt"
    progress.write_text("Собираю данные (1/3)", encoding="utf-8")

    assert scan_outbox(tmp_path / "outbox") == []  # progress — не готовый ответ
    asyncio.run(watcher.process_once())

    assert bot.edits == [(77, "⏳ Собираю данные (1/3)\n(задача t1)")]
    assert not progress.exists()
    task = store.get("t1")
    assert task is not None and task.status is TaskStatus.PENDING


def test_progress_for_finished_task_is_dropped(tmp_path: Path) -> None:
    watcher, store, bot = _watcher(tmp_path, typing_interval_seconds=None)
    store.add("t1", 42, 42, "задача")
    store.set_ack_message("t1", 77)
    store.set_status("t1", TaskStatus.DONE)
    (tmp_path / "outbox" / "t1.progress.txt").write_text("поздно", encoding="utf-8")

    asyncio.run(watcher.process_once())

    assert bot.edits == []
    assert scan_progress(tmp_path / "outbox") == []


def test_typing_for_pending_chats(tmp_path: Path) -> None:
    watcher, store, bot = _watcher(tmp_path, typing_interval_seconds=60)
    store.add("a", 42, 42, "x")
    store.add("b", 42, 42, "y")
    store.add("c", 7, 7, "z")
    store.set_status("c", TaskStatus.DONE)

    asyncio.run(watcher.process_once())
    asyncio.run(watcher.process_once())  # интервал не прошёл — повтора нет

    assert bot.actions == [(42, "typing")]


def test_progress_message_is_truncated() -> None:
    text = progress_message("t1", "x" * 5000)
    assert len(text) <= 4096 and text.endswith("(задача t1)")


def _age_task(store: TaskStore, task_id: str, minutes: float) -> None:
    from datetime import datetime, timedelta, timezone

    created = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    with store._conn:  # тест подкручивает время создания
        store._conn.execute("UPDATE tasks SET created_at = ? WHERE task_id = ?", (created, task_id))


def test_timeout_warns_once_and_stops_typing(tmp_path: Path) -> None:
    watcher, store, bot = _watcher(tmp_path, typing_interval_seconds=0, task_timeout_seconds=15 * 60)
    store.add("old", 42, 42, "долгая")
    store.set_ack_message("old", 5)
    store.add("fresh", 7, 7, "новая")
    _age_task(store, "old", 20)

    asyncio.run(watcher.process_once())
    asyncio.run(watcher.process_once())

    warnings = [m for m in bot.sent if "без ответа" in m.text]
    assert [(m.chat_id, "old" in m.text) for m in warnings] == [(42, True)]
    assert bot.sent_kwargs[0]["reply_parameters"].message_id == 5
    assert (42, "typing") not in bot.actions and (7, "typing") in bot.actions
    task = store.get("old")
    assert task is not None and task.timeout_notified and task.status is TaskStatus.PENDING


def test_timeout_disabled(tmp_path: Path) -> None:
    watcher, store, bot = _watcher(tmp_path, typing_interval_seconds=None, task_timeout_seconds=None)
    store.add("old", 42, 42, "долгая")
    _age_task(store, "old", 600)
    asyncio.run(watcher.process_once())
    assert bot.sent == []
