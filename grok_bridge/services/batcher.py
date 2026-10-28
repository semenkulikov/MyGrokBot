"""Склейка нескольких сообщений подряд (и альбомов) в одну задачу.

Сообщения одного чата копятся, пока между ними меньше `delay` секунд.
После паузы вся пачка уходит в flush одним списком.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass(slots=True)
class _Batch[T]:
    items: list[T] = field(default_factory=list)
    timer: asyncio.Task[None] | None = None


class MessageBatcher[T]:
    def __init__(
        self,
        flush: Callable[[list[T]], Awaitable[object]],
        key: Callable[[T], Hashable],
        delay_seconds: float = 2.5,
        max_items: int = 50,
    ) -> None:
        self._flush = flush
        self._key = key
        self._delay = delay_seconds
        self._max_items = max(1, max_items)
        self._batches: dict[Hashable, _Batch[T]] = {}
        self._in_flight: set[asyncio.Task[None]] = set()

    @property
    def pending(self) -> int:
        return sum(len(batch.items) for batch in self._batches.values())

    async def add(self, item: T) -> None:
        if self._delay <= 0:
            await self._run_flush([item])
            return
        key = self._key(item)
        batch = self._batches.setdefault(key, _Batch())
        batch.items.append(item)
        if batch.timer is not None:
            batch.timer.cancel()
        if len(batch.items) >= self._max_items:
            self._start_flush(key)
        else:
            batch.timer = asyncio.create_task(self._wait_and_flush(key), name=f"batch-{key}")

    async def close(self) -> None:
        """Отправляет всё накопленное сразу и ждёт завершения отправок."""
        for key in list(self._batches):
            self._start_flush(key)
        if self._in_flight:
            await asyncio.gather(*self._in_flight, return_exceptions=True)

    async def _wait_and_flush(self, key: Hashable) -> None:
        await asyncio.sleep(self._delay)
        # Пачка снимается без await между sleep и pop, поэтому новый add
        # не может отменить уже начатую отправку.
        self._start_flush(key)

    def _start_flush(self, key: Hashable) -> None:
        batch = self._batches.pop(key, None)
        if batch is None or not batch.items:
            return
        if batch.timer is not None and batch.timer is not asyncio.current_task():
            batch.timer.cancel()
        task = asyncio.create_task(self._run_flush(batch.items), name=f"flush-{key}")
        self._in_flight.add(task)
        task.add_done_callback(self._in_flight.discard)

    async def _run_flush(self, items: list[T]) -> None:
        try:
            await self._flush(items)
        except Exception:
            logger.exception("Не удалось обработать пачку из %s сообщений", len(items))
