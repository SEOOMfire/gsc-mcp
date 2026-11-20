"""Run awaitables with a fixed concurrency limit.

Exceptions from one item are returned in place so the other items finish.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


async def gather_limited(
    items: Sequence[T],
    worker: Callable[[T], Awaitable[R]],
    *,
    limit: int,
) -> list[R | BaseException]:
    """Preserve input order. A failure becomes the exception value, not a cancel."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    semaphore = asyncio.Semaphore(limit)

    async def run(item: T) -> R | BaseException:
        async with semaphore:
            try:
                return await worker(item)
            except Exception as exc:
                return exc

    return list(await asyncio.gather(*(run(item) for item in items)))
