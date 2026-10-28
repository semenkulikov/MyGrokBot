"""Доступ к боту только для разрешённых Telegram ID."""

import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

logger = logging.getLogger(__name__)


class AllowedUsersMiddleware(BaseMiddleware):
    def __init__(self, allowed_user_ids: Iterable[int]) -> None:
        self._allowed = frozenset(allowed_user_ids)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.id not in self._allowed:
            logger.warning(
                "Игнорирую сообщение от неразрешённого пользователя id=%s username=%s",
                user.id if user else None,
                user.username if user else None,
            )
            return None
        return await handler(event, data)
