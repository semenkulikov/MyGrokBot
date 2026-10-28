import asyncio
from pathlib import Path
from typing import Any

from grok_bridge.services.task_service import TaskService, combine_text
from grok_bridge.services.webhook_client import WebhookError
from grok_bridge.storage import TaskStatus, TaskStore
from tests.helpers import FakeBot, FakeWebhook, make_message


def _service(tmp_path: Path, webhook: FakeWebhook) -> tuple[TaskService, TaskStore]:
    store = TaskStore(tmp_path / "tasks.sqlite3")
    service = TaskService(
        store=store,
        webhook=webhook,  # type: ignore[arg-type]
        outbox_dir=tmp_path / "outbox",
        inbox_dir=tmp_path / "inbox",
    )
    return service, store


def test_text_task_keeps_old_payload(tmp_path: Path) -> None:
    webhook = FakeWebhook()
    service, store = _service(tmp_path, webhook)
    bot = FakeBot()

    task_id = asyncio.run(service.submit_messages(bot, [make_message(text="привет")]))  # type: ignore[arg-type]

    (payload,) = webhook.calls
    assert payload == {"task_id": task_id, "chat_id": 100, "text": "привет", "outbox_dir": str(tmp_path / "outbox")}
    assert "Принял" in bot.sent[0].text and task_id in bot.sent[0].text
    task = store.get(task_id)
    assert task is not None and task.status is TaskStatus.PENDING


def test_voice_task_passes_file_path(tmp_path: Path) -> None:
    webhook = FakeWebhook()
    service, _ = _service(tmp_path, webhook)
    bot = FakeBot(paths={"v1": "voice/file_1.oga"})
    message = make_message(voice={"file_id": "v1", "file_unique_id": "u", "duration": 4})

    task_id = asyncio.run(service.submit_messages(bot, [message]))  # type: ignore[arg-type]

    (payload,) = webhook.calls
    assert payload["text"] == ""
    (file_info,) = payload["files"]
    assert file_info["type"] == "voice"
    expected: Any = (tmp_path / "inbox" / task_id / "01-voice.oga").resolve()
    assert file_info["path"] == str(expected)
    assert expected.is_file()


def test_webhook_error_marks_task_failed(tmp_path: Path) -> None:
    service, store = _service(tmp_path, FakeWebhook(error=WebhookError("HTTP 500")))
    bot = FakeBot()

    task_id = asyncio.run(service.submit_messages(bot, [make_message(text="x")]))  # type: ignore[arg-type]

    task = store.get(task_id)
    assert task is not None and task.status is TaskStatus.FAILED
    assert "HTTP 500" in bot.edits[-1][1]


def test_combine_text() -> None:
    voice = {"file_id": "v", "file_unique_id": "u", "duration": 1}
    messages = [make_message(text="раз"), make_message(caption="два", voice=voice), make_message(voice=voice)]
    assert combine_text(messages) == "раз\n\nдва"


def test_forwarded_photo_album_text_and_files(tmp_path: Path) -> None:
    webhook = FakeWebhook()
    service, _ = _service(tmp_path, webhook)
    bot = FakeBot(paths={"p1": "photos/file_1.jpg", "d1": "documents/file_2.pdf"})
    photo = make_message(
        photo=[{"file_id": "p1", "file_unique_id": "p", "width": 10, "height": 10}],
        caption="что тут?",
        forward_origin={"type": "hidden_user", "date": 0, "sender_user_name": "Аня"},
    )
    doc = make_message(message_id=2, document={"file_id": "d1", "file_unique_id": "d", "file_name": "cv.pdf"})

    asyncio.run(service.submit_messages(bot, [photo, doc]))  # type: ignore[arg-type]

    (payload,) = webhook.calls
    assert payload["text"] == "[Переслано от Аня]\nчто тут?"
    assert [(f["type"], Path(f["path"]).name, f["caption"]) for f in payload["files"]] == [
        ("photo", "01-photo.jpg", "что тут?"),
        ("document", "02-cv.pdf", None),
    ]


def test_reply_to_bot_message_continues_task(tmp_path: Path) -> None:
    webhook = FakeWebhook()
    service, store = _service(tmp_path, webhook)
    bot = FakeBot()
    store.add("20261028-100000-aaaaaa", 100, 100, "старая")
    store.link_messages("20261028-100000-aaaaaa", 100, [555])
    reply = make_message(
        message_id=7,
        text="а теперь сделай так",
        reply_to_message={"message_id": 555, "date": 0, "chat": {"id": 100, "type": "private"}, "text": "ответ бота"},
    )

    task_id = asyncio.run(service.submit_messages(bot, [reply]))  # type: ignore[arg-type]

    (payload,) = webhook.calls
    assert payload["parent_task_id"] == "20261028-100000-aaaaaa"
    assert payload["reply_to_text"] == "ответ бота"
    task = store.get(task_id)
    assert task is not None and task.parent_task_id == "20261028-100000-aaaaaa"
    # и пользовательское сообщение, и «Принял…» теперь ведут к новой задаче
    assert store.task_for_message(100, 7) == task_id
    assert store.task_for_message(100, bot.sent[0].message_id) == task_id
