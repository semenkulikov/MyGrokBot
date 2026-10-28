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
