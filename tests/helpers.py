"""Общие заготовки для тестов: сообщения Telegram и фейковый бот."""

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from aiogram.types import Message


def make_message(message_id: int = 1, chat_id: int = 100, user_id: int = 100, **fields: Any) -> Message:
    data: dict[str, Any] = {
        "message_id": message_id,
        "date": datetime.now(),
        "chat": {"id": chat_id, "type": "private"},
        "from": {"id": user_id, "is_bot": False, "first_name": "Sem"},
        **fields,
    }
    return Message.model_validate(data)


class FakeDownloader:
    """Имитирует get_file/download_file у aiogram.Bot."""

    def __init__(self, paths: dict[str, str] | None = None, broken: set[str] | None = None) -> None:
        self.paths = paths or {}
        self.broken = broken or set()
        self.downloaded: list[Path] = []

    async def get_file(self, file_id: str) -> Any:
        if file_id in self.broken:
            raise OSError("сеть недоступна")
        return SimpleNamespace(file_id=file_id, file_path=self.paths.get(file_id, f"files/{file_id}.bin"))

    async def download_file(self, file_path: str, destination: Path) -> None:
        destination.write_bytes(b"data")
        self.downloaded.append(destination)


class FakeSentMessage:
    def __init__(self, bot: "FakeBot", chat_id: int, message_id: int, text: str) -> None:
        self.bot, self.chat_id, self.message_id, self.text = bot, chat_id, message_id, text

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.text = text
        self.bot.edits.append((self.message_id, text))


class FakeBot(FakeDownloader):
    """Минимальный aiogram.Bot для тестов TaskService."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.sent: list[FakeSentMessage] = []
        self.sent_kwargs: list[dict[str, Any]] = []
        self.edits: list[tuple[int, str]] = []
        self.actions: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> FakeSentMessage:
        message = FakeSentMessage(self, chat_id, 1000 + len(self.sent), text)
        self.sent.append(message)
        self.sent_kwargs.append(kwargs)
        return message

    async def send_chat_action(self, chat_id: int, action: str, **kwargs: Any) -> bool:
        self.actions.append((chat_id, action))
        return True


class FakeWebhook:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error
        self.configured = True

    async def send_task(self, **payload: Any) -> None:
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
