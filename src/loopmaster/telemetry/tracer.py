"""OpenTelemetry Tracer implementation and span lifecycle management."""

from __future__ import annotations

import secrets
from collections.abc import Callable, Generator
from contextlib import contextmanager
from typing import Any

from .context import get_current_span, reset_current_span, set_current_span
from .types import Span, SpanContext, SpanKind, SpanStatusCode


def generate_trace_id() -> str:
    """Generate a random 128-bit trace ID formatted as a 32-character lowercase hex string."""
    while True:
        tid = secrets.token_hex(16)
        if tid != "0" * 32:
            return tid


def generate_span_id() -> str:
    """Generate a random 64-bit span ID formatted as a 16-character lowercase hex string."""
    while True:
        sid = secrets.token_hex(8)
        if sid != "0" * 16:
            return sid


class NoOpSpan(Span):
    """Zero-allocation no-op span used when tracing is disabled."""

    def __init__(self) -> None:
        super().__init__(
            name="noop",
            context=SpanContext(trace_id="0" * 32, span_id="0" * 16),
        )

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_attributes(self, attributes: dict[str, Any]) -> None:
        pass

    def add_event(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
        timestamp_ns: int | None = None,
    ) -> None:
        pass

    def set_status(self, code: SpanStatusCode, message: str = "") -> None:
        pass

    def end(self, end_time_ns: int | None = None) -> None:
        pass


class Tracer:
    """Creates and manages the lifecycle of Spans."""

    def __init__(
        self,
        name: str = "loopmaster",
        version: str = "1.0.0",
        on_span_ended: Callable[[Span], None] | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self._on_span_ended = on_span_ended

    def start_span(
        self,
        name: str,
        parent_context: SpanContext | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict[str, Any] | None = None,
    ) -> Span:
        """Start a new span without making it active in context."""
        parent_span = get_current_span()
        if parent_context is not None and parent_context.is_valid:
            trace_id = parent_context.trace_id
            parent_id: str | None = parent_context.span_id
        elif parent_span is not None and parent_span.context.is_valid:
            trace_id = parent_span.context.trace_id
            parent_id = parent_span.context.span_id
        else:
            trace_id = generate_trace_id()
            parent_id = None

        span_id = generate_span_id()
        context = SpanContext(trace_id=trace_id, span_id=span_id)

        span = Span(
            name=name,
            context=context,
            parent_span_id=parent_id,
            kind=kind,
            attributes=dict(attributes or {}),
        )
        return span

    @contextmanager
    def start_as_current_span(
        self,
        name: str,
        parent_context: SpanContext | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict[str, Any] | None = None,
    ) -> Generator[Span, None, None]:
        """Context manager that starts a span, sets it as current, and ends it upon exit."""
        span = self.start_span(
            name=name,
            parent_context=parent_context,
            kind=kind,
            attributes=attributes,
        )
        token = set_current_span(span)
        try:
            yield span
        except Exception as exc:
            span.set_status(SpanStatusCode.ERROR, str(exc))
            span.add_event(
                "exception",
                {"exception.message": str(exc), "exception.type": exc.__class__.__name__},
            )
            raise
        finally:
            reset_current_span(token)
            span.end()
            if self._on_span_ended:
                self._on_span_ended(span)


class NoOpTracer(Tracer):
    """No-op tracer returning NoOpSpan with zero overhead."""

    def __init__(self) -> None:
        super().__init__(name="noop", version="0.0.0")
        self._noop_span = NoOpSpan()

    def start_span(
        self,
        name: str,
        parent_context: SpanContext | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict[str, Any] | None = None,
    ) -> Span:
        return self._noop_span

    @contextmanager
    def start_as_current_span(
        self,
        name: str,
        parent_context: SpanContext | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict[str, Any] | None = None,
    ) -> Generator[Span, None, None]:
        yield self._noop_span
