"""Limited parallelism keeps successes when one item fails."""

from __future__ import annotations

import asyncio

from gsc_mcp.concurrency import gather_limited


def test_gather_limited_caps_concurrency_and_keeps_partial_results():
    current = 0
    max_seen = 0

    async def worker(item: int) -> int:
        nonlocal current, max_seen
        current += 1
        max_seen = max(max_seen, current)
        await asyncio.sleep(0.02)
        current -= 1
        if item == 3:
            raise RuntimeError("boom")
        return item

    async def run():
        return await gather_limited(list(range(10)), worker, limit=5)

    results = asyncio.run(run())
    assert max_seen <= 5
    assert max_seen >= 2
    assert sum(isinstance(item, int) for item in results) == 9
    failure = next(item for item in results if isinstance(item, Exception))
    assert str(failure) == "boom"
    assert results[3] is failure
