"""Core data structures and types for OpenTelemetry tracing and observability."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class SpanKind(IntEnum):
    """OpenTelemetry span kinds."""

    INTERNAL = 1
    SERVER = 2
    CLIENT = 3
    PRODUCER = 4
    CONSUMER = 5


class SpanStatusCode(IntEnum):
    """OpenTelemetry span status codes."""

    UNSET = 0
    OK = 1
    ERROR = 2


@dataclass
class SpanStatus:
    """Status associated with a Span."""

    code: SpanStatusCode = SpanStatusCode.UNSET
    message: str = ""


@dataclass(frozen=True)
class SpanContext:
    """Identifying information for a Span in distributed tracing."""

    trace_id: str
    span_id: str
    trace_flags: int = 1
    is_remote: bool = False

    @property
    def is_valid(self) -> bool:
        return bool(
            self.trace_id
            and self.span_id
            and self.trace_id != "0" * 32
            and self.span_id != "0" * 16
        )


@dataclass
class SpanEvent:
    """A timestamped event attached to a Span."""

    name: str
    timestamp_ns: int = field(default_factory=time.time_ns)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class Span:
    """An OpenTelemetry-compliant Span."""

    name: str
    context: SpanContext
    parent_span_id: str | None = None
    kind: SpanKind = SpanKind.INTERNAL
    start_time_ns: int = field(default_factory=time.time_ns)
    end_time_ns: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[SpanEvent] = field(default_factory=list)
    status: SpanStatus = field(default_factory=SpanStatus)

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a single attribute on the span."""
        if value is not None:
            self.attributes[key] = value

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        """Set multiple attributes on the span."""
        for k, v in attributes.items():
            if v is not None:
                self.attributes[k] = v

    def add_event(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
        timestamp_ns: int | None = None,
    ) -> None:
        """Add a timestamped event to the span."""
        ts = timestamp_ns if timestamp_ns is not None else time.time_ns()
        self.events.append(SpanEvent(name=name, timestamp_ns=ts, attributes=attributes or {}))

    def set_status(self, code: SpanStatusCode, message: str = "") -> None:
        """Set the status of the span."""
        self.status = SpanStatus(code=code, message=message)

    def end(self, end_time_ns: int | None = None) -> None:
        """End the span, recording its completion timestamp."""
        if self.end_time_ns is None:
            self.end_time_ns = end_time_ns if end_time_ns is not None else time.time_ns()

    @property
    def duration_ms(self) -> float:
        """Calculate duration in milliseconds."""
        end = self.end_time_ns if self.end_time_ns is not None else time.time_ns()
        return (end - self.start_time_ns) / 1_000_000.0
