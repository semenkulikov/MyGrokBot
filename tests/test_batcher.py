import asyncio

from grok_bridge.services.batcher import MessageBatcher


def _collect(delay: float, max_items: int = 50) -> tuple[MessageBatcher[tuple[int, str]], list[list[str]]]:
    flushed: list[list[str]] = []

    async def flush(items: list[tuple[int, str]]) -> None:
        flushed.append([text for _, text in items])

    return MessageBatcher(flush, key=lambda item: item[0], delay_seconds=delay, max_items=max_items), flushed


def test_messages_in_a_row_become_one_batch() -> None:
    async def scenario() -> list[list[str]]:
        batcher, flushed = _collect(0.05)
        await batcher.add((1, "фото"))
        await asyncio.sleep(0.02)
        await batcher.add((1, "подпись"))
        await batcher.add((2, "другой чат"))
        await asyncio.sleep(0.15)
        await batcher.add((1, "позже"))
        await asyncio.sleep(0.15)
        return flushed

    assert asyncio.run(scenario()) == [["фото", "подпись"], ["другой чат"], ["позже"]]


def test_zero_delay_flushes_immediately() -> None:
    async def scenario() -> list[list[str]]:
        batcher, flushed = _collect(0)
        await batcher.add((1, "a"))
        await batcher.add((1, "b"))
        return flushed

    assert asyncio.run(scenario()) == [["a"], ["b"]]


def test_close_flushes_pending_and_max_items() -> None:
    async def scenario() -> tuple[list[list[str]], int]:
        batcher, flushed = _collect(10, max_items=2)
        await batcher.add((1, "a"))
        await batcher.add((1, "b"))  # достигнут max_items — уходит сразу
        await batcher.add((1, "c"))
        pending = batcher.pending
        await batcher.close()
        return flushed, pending

    flushed, pending = asyncio.run(scenario())
    assert pending == 1
    assert flushed == [["a", "b"], ["c"]]


def test_flush_errors_are_logged_not_raised() -> None:
    async def broken(items: list[int]) -> None:
        raise RuntimeError("boom")

    async def scenario() -> None:
        batcher: MessageBatcher[int] = MessageBatcher(broken, key=lambda _: 0, delay_seconds=0)
        await batcher.add(1)

    asyncio.run(scenario())
