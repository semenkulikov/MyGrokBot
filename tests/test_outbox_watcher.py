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


def test_buttons_are_attached_to_last_chunk(tmp_path: Path) -> None:
    watcher, store, bot = _watcher(tmp_path, typing_interval_seconds=None)
    store.add("t1", 42, 42, "удалить файлы?")
    outbox = tmp_path / "outbox"
    (outbox / "t1.buttons.json").write_text('["Да", "Нет"]', encoding="utf-8")
    (outbox / "t1.txt").write_text("Удалить 3 файла?", encoding="utf-8")

    asyncio.run(watcher.process_once())

    keyboard = bot.sent_kwargs[-1]["reply_markup"]
    callback = keyboard.inline_keyboard[0][0].callback_data
    button = store.get_button(int(callback.split(":")[1]))
    assert button is not None and (button.task_id, button.text, button.chat_id) == ("t1", "Да", 42)
    assert sorted(p.name for p in (tmp_path / "sent").iterdir()) == ["t1.buttons.json", "t1.txt"]

    assert store.use_buttons_of(button.button_id)
    assert not store.use_buttons_of(button.button_id + 1)  # «Нет» того же ответа тоже погашена


def test_broken_buttons_file_still_delivers_text(tmp_path: Path) -> None:
    watcher, _store, bot = _watcher(tmp_path, typing_interval_seconds=None)
    (tmp_path / "outbox" / "t2.buttons.json").write_text("не json", encoding="utf-8")
    (tmp_path / "outbox" / "t2.txt").write_text("ответ", encoding="utf-8")

    asyncio.run(watcher.process_once())

    assert [m.text for m in bot.sent] == ["ответ"]
    assert bot.sent_kwargs[0]["reply_markup"] is None


def test_files_dir_is_sent_before_text_and_archived(tmp_path: Path) -> None:
    watcher, store, bot = _watcher(tmp_path, typing_interval_seconds=None)
    store.add("t1", 42, 42, "сделай отчёт")
    outbox = tmp_path / "outbox"
    files = outbox / "t1.files"
    files.mkdir()
    for name in ("a.png", "b.jpg", "report.pdf", "clip.mp4", ".DS_Store"):
        (files / name).write_bytes(b"x")
    (outbox / "t1.png").write_bytes(b"x")
    (outbox / "t1.txt").write_text("Готово", encoding="utf-8")

    asyncio.run(watcher.process_once())

    kinds = [getattr(m, "media", ("text", m.text)) for m in bot.sent]
    assert kinds == [
        ("photo", "t1.png"),
        ("album", "a.png"),
        ("album", "b.jpg"),
        ("video", "clip.mp4"),
        ("document", "report.pdf"),
        ("text", "Готово"),
    ]
    assert bot.sent[0].text == ""  # при наличии файлов текст не уходит подписью
    assert (tmp_path / "sent" / "t1.files" / "report.pdf").is_file()
    assert not files.exists()


def test_only_files_without_text(tmp_path: Path) -> None:
    watcher, _store, bot = _watcher(tmp_path, typing_interval_seconds=None)
    files = tmp_path / "outbox" / "t1.files"
    files.mkdir()
    (files / "one.png").write_bytes(b"x")
    (tmp_path / "outbox" / "t1.txt").write_text("", encoding="utf-8")

    asyncio.run(watcher.process_once())

    assert [getattr(m, "media", None) for m in bot.sent] == [("photo", "one.png")]


def test_late_answer_for_cancelled_task(tmp_path: Path) -> None:
    watcher, store, bot = _watcher(tmp_path, typing_interval_seconds=None)
    store.add("t1", 42, 42, "x")
    store.set_status("t1", TaskStatus.CANCELLED)
    (tmp_path / "outbox" / "t1.txt").write_text("всё-таки готово", encoding="utf-8")

    asyncio.run(watcher.process_once())

    assert bot.sent[0].text == "(Ответ на отменённую задачу t1)\n\nвсё-таки готово"
    task = store.get("t1")
    assert task is not None and task.status is TaskStatus.CANCELLED


def test_markdown_is_sent_as_html(tmp_path: Path) -> None:
    watcher, _store, bot = _watcher(tmp_path, typing_interval_seconds=None)
    (tmp_path / "outbox" / "t1.txt").write_text("**Готово**: `ok`", encoding="utf-8")

    asyncio.run(watcher.process_once())

    assert bot.sent[0].text == "<b>Готово</b>: <code>ok</code>"
    assert bot.sent_kwargs[0]["parse_mode"] == "HTML"


def test_markup_error_falls_back_to_plain(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from aiogram.exceptions import TelegramBadRequest

    watcher, _store, bot = _watcher(tmp_path, typing_interval_seconds=None)
    original = bot.send_message

    async def picky(chat_id: int, text: str, **kwargs: object) -> object:
        if kwargs.get("parse_mode"):
            raise TelegramBadRequest(MagicMock(), "Bad Request: can't parse entities")
        return await original(chat_id, text, **kwargs)

    bot.send_message = picky  # type: ignore[method-assign]
    (tmp_path / "outbox" / "t1.txt").write_text("**Готово**", encoding="utf-8")

    asyncio.run(watcher.process_once())

    assert [(m.text, kw["parse_mode"]) for m, kw in zip(bot.sent, bot.sent_kwargs, strict=True)] == [
        ("**Готово**", None)
    ]


def test_plain_mode_and_long_text_chunks(tmp_path: Path) -> None:
    watcher, _store, bot = _watcher(tmp_path, typing_interval_seconds=None, markdown=False)
    long_text = "\n\n".join(f"Абзац {i} " + "слово " * 150 for i in range(10))
    (tmp_path / "outbox" / "t1.txt").write_text(long_text, encoding="utf-8")

    asyncio.run(watcher.process_once())

    assert len(bot.sent) > 1
    pairs = zip(bot.sent, bot.sent_kwargs, strict=True)
    assert all(len(m.text) <= 4096 and kw["parse_mode"] is None for m, kw in pairs)
    assert all(m.text.startswith("Абзац") for m in bot.sent)  # режется по абзацам
