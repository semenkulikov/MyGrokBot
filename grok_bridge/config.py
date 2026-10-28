"""Настройки приложения: читаются из переменных окружения и файла .env."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_FILE = BASE_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    bot_token: SecretStr = SecretStr("")
    allowed_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    webhook_url: str = ""
    webhook_key: SecretStr = SecretStr("")
    webhook_auth_header: str = "Authorization"
    webhook_auth_scheme: str = "Bearer"
    webhook_timeout_seconds: float = Field(default=15.0, gt=0)
    webhook_max_attempts: int = Field(default=3, ge=1)

    outbox_poll_seconds: float = Field(default=2.0, gt=0)

    # Сообщения с паузой меньше этой склеиваются в одну задачу (альбомы, фото + текст). 0 — не склеивать
    message_debounce_seconds: float = Field(default=2.5, ge=0)

    # Bot API отдаёт ботам файлы не больше 20 МБ
    max_download_mb: float = Field(default=20.0, gt=0)

    runtime_dir: Path = BASE_DIR / "runtime"
    log_dir: Path = BASE_DIR / "logs"
    log_level: str = "INFO"

    @field_validator("allowed_user_ids", mode="before")
    @classmethod
    def _parse_user_ids(cls, value: Any) -> Any:
        """Разрешает формат "123,456" (а также пробелы и ';' как разделители)."""
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            parts = value.replace(";", ",").replace(" ", ",").split(",")
            return [int(part) for part in parts if part.strip()]
        return value

    @property
    def outbox_dir(self) -> Path:
        return self.runtime_dir / "outbox"

    @property
    def inbox_dir(self) -> Path:
        return self.runtime_dir / "inbox"

    @property
    def max_download_bytes(self) -> int:
        return int(self.max_download_mb * 1024 * 1024)

    @property
    def sent_dir(self) -> Path:
        return self.runtime_dir / "sent"

    @property
    def failed_dir(self) -> Path:
        return self.runtime_dir / "failed"

    @property
    def db_path(self) -> Path:
        return self.runtime_dir / "tasks.sqlite3"

    def ensure_dirs(self) -> None:
        for directory in (self.outbox_dir, self.inbox_dir, self.sent_dir, self.failed_dir, self.log_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
