"""In-memory SSE broadcaster.

Each SSE client gets its own asyncio.Queue; the worker calls `publish()` to fan out
an event to all current subscribers. Queues are bounded so a slow client cannot
accumulate unbounded memory — excess events are dropped for that subscriber only.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

_subscribers: list[asyncio.Queue] = []


async def publish(event: dict[str, Any]) -> None:
    """Fan out an event to every current subscriber (best-effort; drops on full queue)."""
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            # Slow client — skip this event for them rather than blocking everyone.
            pass


async def subscribe() -> AsyncIterator[str]:
    """Yield SSE-formatted event strings for one subscriber until disconnect."""
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subscribers.append(q)
    try:
        # Send an initial comment so the browser knows the connection is alive.
        yield ": connected\n\n"
        while True:
            event = await q.get()
            yield f"data: {json.dumps(event)}\n\n"
    finally:
        if q in _subscribers:
            _subscribers.remove(q)
