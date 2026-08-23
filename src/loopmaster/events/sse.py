"""Server-Sent Events (SSE) formatting and real-time streaming bridge."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import queue
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import EventEmitter, LoopEvent

logger = logging.getLogger("loopmaster.events.sse")


def format_sse(
    data: dict[str, Any] | str,
    event_type: str | None = None,
    event_id: str | None = None,
    retry_ms: int | None = None,
) -> str:
    """Format data as a Server-Sent Events (SSE) message per W3C specification.

    Args:
        data: Payload dict (serialized to JSON) or string (split per line).
        event_type: Optional SSE event name (e.g. 'step_chunk', 'step_completed').
        event_id: Optional SSE message ID.
        retry_ms: Optional reconnection timeout in milliseconds for the client.

    Returns:
        Properly formatted SSE message ending with double newline.
    """
    lines: list[str] = []
    if event_type:
        lines.append(f"event: {event_type}")
    if event_id:
        lines.append(f"id: {event_id}")
    if retry_ms is not None:
        lines.append(f"retry: {retry_ms}")

    if isinstance(data, dict):
        lines.append(f"data: {json.dumps(data, ensure_ascii=False, default=str)}")
    else:
        for line in str(data).splitlines():
            lines.append(f"data: {line}")

    return "\n".join(lines) + "\n\n"


class SSEStream:
    """Bridges EventEmitter to async and sync SSE stream generators.

    Supports filtering by job_id, custom event types, and automatic cleanup
    of queues and listeners when clients disconnect or generator completes.
    """

    TERMINAL_EVENTS = ("loop_completed", "loop_failed", "loop_interrupted", "loop_cancelled")

    def __init__(
        self,
        emitter: EventEmitter,
        job_id: str | None = None,
        event_types: list[str] | None = None,
        maxsize: int = 500,
    ) -> None:
        self.emitter = emitter
        self.job_id = job_id
        self.event_types = event_types
        self.maxsize = maxsize

    def _matches(self, event: LoopEvent) -> bool:
        if self.job_id and event.job_id != self.job_id:
            return False
        return not (self.event_types and event.event_type not in self.event_types)

    def _event_to_sse(self, event: LoopEvent) -> str:
        payload = {
            "job_id": event.job_id,
            "event_type": event.event_type,
            "timestamp": event.timestamp,
            "step_index": event.step_index,
            "metrics": event.metrics_snapshot,
            "payload": event.payload,
        }
        return format_sse(
            data=payload,
            event_type=event.event_type,
            event_id=f"{event.job_id}:{event.step_index}:{int(event.timestamp * 1000)}",
        )

    async def __aiter__(self) -> AsyncIterator[str]:
        """Asynchronous iterator for FastAPI / Starlette / MCP SSE responses."""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue[LoopEvent | None] = asyncio.Queue(maxsize=self.maxsize)

        def _safe_put(target_q: asyncio.Queue[LoopEvent | None], ev: LoopEvent) -> None:
            if not target_q.full():
                target_q.put_nowait(ev)

        def listener(event: LoopEvent) -> None:
            if not self._matches(event):
                return
            if loop.is_running():
                loop.call_soon_threadsafe(_safe_put, q, event)
            else:
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(event)

        self.emitter.on("*", listener)
        try:
            while True:
                event = await q.get()
                if event is None:
                    break
                yield self._event_to_sse(event)
                if event.event_type in self.TERMINAL_EVENTS:
                    break
        finally:
            self.emitter.off("*", listener)

    def __iter__(self) -> Iterator[str]:
        """Synchronous iterator for WSGI / Flask / threaded HTTP streaming."""
        sync_q: queue.Queue[LoopEvent | None] = queue.Queue(maxsize=self.maxsize)

        def listener(event: LoopEvent) -> None:
            if not self._matches(event):
                return
            with contextlib.suppress(queue.Full):
                sync_q.put_nowait(event)

        self.emitter.on("*", listener)
        try:
            while True:
                event = sync_q.get()
                if event is None:
                    break
                yield self._event_to_sse(event)
                if event.event_type in self.TERMINAL_EVENTS:
                    break
        finally:
            self.emitter.off("*", listener)
