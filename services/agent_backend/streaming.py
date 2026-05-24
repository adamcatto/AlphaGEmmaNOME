from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal

EventType = Literal[
    "token",
    "thought",
    "progress",
    "tool_call_start",
    "tool_call_result",
    "viz_spec",
    "error",
    "final",
]


@dataclass
class EventBus:
    """Single-consumer asyncio queue wrapper used to pipe agent step callbacks
    from the worker thread into the SSE generator. The agent runs in a thread
    so blocking LLM/tool calls don't stall the event loop.
    """

    _queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    _loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def emit(self, event_type: EventType, payload: Any) -> None:
        if self._loop is None:
            raise RuntimeError("EventBus not bound to a loop.")
        frame = {"event": event_type, "data": payload}
        asyncio.run_coroutine_threadsafe(self._queue.put(frame), self._loop)

    async def iterator(self) -> AsyncIterator[dict]:
        while True:
            frame = await self._queue.get()
            yield frame
            if frame["event"] == "final" or frame["event"] == "error":
                break


def sse_format(frame: dict) -> str:
    data = json.dumps(frame["data"], default=str)
    return f"event: {frame['event']}\ndata: {data}\n\n"
