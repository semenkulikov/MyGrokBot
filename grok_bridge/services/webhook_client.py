"""HTTP-клиент вебхука регулярной задачи Grok Bot."""

import asyncio
import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

_RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}


class WebhookError(Exception):
    """Задачу не удалось доставить на вебхук."""


class WebhookClient:
    def __init__(
        self,
        url: str,
        key: str = "",
        auth_header: str = "Authorization",
        auth_scheme: str = "Bearer",
        timeout_seconds: float = 15.0,
        max_attempts: int = 3,
        backoff_base_seconds: float = 1.0,
    ) -> None:
        self._url = url
        self._key = key
        self._auth_header = auth_header
        self._auth_scheme = auth_scheme
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._max_attempts = max(1, max_attempts)
        self._backoff_base = backoff_base_seconds
        self._session: aiohttp.ClientSession | None = None

    @property
    def configured(self) -> bool:
        return bool(self._url)

    def build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._key:
            scheme = self._auth_scheme.strip()
            if scheme and scheme.lower() != "none":
                headers[self._auth_header] = f"{scheme} {self._key}"
            else:
                headers[self._auth_header] = self._key
        return headers

    async def send_task(self, task_id: str, chat_id: int, text: str, **extra: Any) -> None:
        if not self.configured:
            raise WebhookError("WEBHOOK_URL не задан в .env")

        payload: dict[str, Any] = {"task_id": task_id, "chat_id": chat_id, "text": text, **extra}
        last_error = "неизвестная ошибка"

        for attempt in range(1, self._max_attempts + 1):
            try:
                session = self._get_session()
                async with session.post(self._url, json=payload, headers=self.build_headers()) as resp:
                    if resp.status < 300:
                        logger.info("Задача %s передана на вебхук (HTTP %s)", task_id, resp.status)
                        return
                    body = (await resp.text())[:300]
                    last_error = f"HTTP {resp.status}: {body}"
                    if resp.status not in _RETRYABLE_STATUSES:
                        logger.error("Вебхук отклонил задачу %s: %s", task_id, last_error)
                        raise WebhookError(last_error)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                last_error = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__

            logger.warning(
                "Попытка %s/%s отправить задачу %s не удалась: %s",
                attempt, self._max_attempts, task_id, last_error,
            )
            if attempt < self._max_attempts:
                await asyncio.sleep(self._backoff_base * 2 ** (attempt - 1))

        raise WebhookError(last_error)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session
