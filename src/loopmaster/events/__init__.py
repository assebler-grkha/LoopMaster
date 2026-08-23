"""Events module — LoopEvent schema, EventEmitter, and SSE streaming."""

from __future__ import annotations

import collections
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .sse import SSEStream, format_sse

logger = logging.getLogger(__name__)

__all__ = [
    "LoopEvent",
    "EventEmitter",
    "format_sse",
    "SSEStream",
]


@dataclass
class LoopEvent:
    """Event emitted during loop execution."""

    job_id: str
    event_type: str
    timestamp: float
    step_index: int = 0
    metrics_snapshot: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)


class EventEmitter:
    """Collects and dispatches loop events to subscribers with bounded history."""

    def __init__(self, max_history: int = 500) -> None:
        self._listeners: dict[str, list[Callable[[LoopEvent], None]]] = {}
        self._history: collections.deque[LoopEvent] = collections.deque(maxlen=max_history)

    def on(self, event_type: str, callback: Callable[[LoopEvent], None]) -> None:
        """Register a callback for an event type. Use '*' for all events."""
        self._listeners.setdefault(event_type, []).append(callback)

    def off(self, event_type: str, callback: Callable[[LoopEvent], None]) -> None:
        """Remove a callback for an event type."""
        if event_type in self._listeners:
            self._listeners[event_type] = [
                cb for cb in self._listeners[event_type] if cb is not callback
            ]

    def emit(
        self,
        job_id: str,
        event_type: str,
        step_index: int = 0,
        metrics: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> LoopEvent:
        """Create and dispatch an event."""
        event = LoopEvent(
            job_id=job_id,
            event_type=event_type,
            timestamp=time.time(),
            step_index=step_index,
            metrics_snapshot=metrics or {},
            payload=payload or {},
        )
        # Exclude high-frequency transient streaming tokens from persistent history
        if event_type != "step_chunk":
            self._history.append(event)
        self._dispatch(event)
        return event

    def _dispatch(self, event: LoopEvent) -> None:
        """Dispatch event to registered listeners."""
        for callback in self._listeners.get(event.event_type, []):
            try:
                callback(event)
            except Exception as exc:
                logger.warning("Event listener error for %s: %s", event.event_type, exc)
        for callback in self._listeners.get("*", []):
            try:
                callback(event)
            except Exception as exc:
                logger.warning("Wildcard listener error: %s", exc)

    @property
    def history(self) -> list[LoopEvent]:
        """All emitted lifecycle events."""
        return list(self._history)

    def clear(self) -> None:
        """Clear event history."""
        self._history.clear()
