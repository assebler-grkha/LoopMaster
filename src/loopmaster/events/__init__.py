"""Events module — LoopEvent schema and EventEmitter."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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
    """Collects and dispatches loop events to subscribers."""

    def __init__(self) -> None:
        self._listeners: dict[str, list[Callable[[LoopEvent], None]]] = {}
        self._history: list[LoopEvent] = []

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
        """All emitted events."""
        return list(self._history)

    def clear(self) -> None:
        """Clear event history."""
        self._history.clear()
