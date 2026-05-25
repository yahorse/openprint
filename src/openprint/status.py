from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from openprint.models import SSEEvent


class EventBus:
    """Pub/sub event bus for Server-Sent Events."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[SSEEvent | None]]] = {}

    def subscribe(self, channel: str) -> asyncio.Queue[SSEEvent | None]:
        queue: asyncio.Queue[SSEEvent | None] = asyncio.Queue()
        self._subscribers.setdefault(channel, []).append(queue)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue[SSEEvent | None]) -> None:
        if channel in self._subscribers:
            self._subscribers[channel] = [
                q for q in self._subscribers[channel] if q is not queue
            ]

    async def publish(self, channel: str, event: str, data: dict[str, Any]) -> None:
        sse_event = SSEEvent(event=event, data=data)
        for queue in self._subscribers.get(channel, []):
            await queue.put(sse_event)

    async def close_channel(self, channel: str) -> None:
        for queue in self._subscribers.get(channel, []):
            await queue.put(None)
        self._subscribers.pop(channel, None)


async def event_stream(
    bus: EventBus, channel: str
) -> AsyncGenerator[str, None]:
    queue = bus.subscribe(channel)
    try:
        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"event: {event.event}\ndata: {json.dumps(event.data)}\n\n"
    finally:
        bus.unsubscribe(channel, queue)
