"""Heartbeat monitoring for interruption detection."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class HeartbeatState:
    """Tracks heartbeat for interruption detection."""

    last_heartbeat: float = 0.0
    thread: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)


def start_heartbeat(
    state: HeartbeatState,
    heartbeat_timeout: float,
    heartbeat_interval: float,
    loop_name: str,
) -> None:
    """Start a background heartbeat monitoring thread."""
    state.last_heartbeat = time.monotonic()

    def _heartbeat_loop() -> None:
        while not state.stop_event.is_set():
            elapsed = time.monotonic() - state.last_heartbeat
            if elapsed > heartbeat_timeout:
                logger.warning(
                    "Heartbeat timeout after %.1fs — interruption detected for loop %s",
                    elapsed,
                    loop_name,
                )
                state.stop_event.set()
                return
            state.stop_event.wait(timeout=heartbeat_interval)

    state.thread = threading.Thread(
        target=_heartbeat_loop, daemon=True, name=f"heartbeat-{loop_name}"
    )
    state.thread.start()


def stop_heartbeat(state: HeartbeatState) -> None:
    """Stop the heartbeat monitoring thread."""
    state.stop_event.set()
    if state.thread:
        state.thread.join(timeout=2.0)


def ping_heartbeat(state: HeartbeatState) -> None:
    """Update the heartbeat timestamp."""
    state.last_heartbeat = time.monotonic()


def is_interrupted(state: HeartbeatState) -> bool:
    """Check if the heartbeat has been interrupted."""
    return state.stop_event.is_set()
