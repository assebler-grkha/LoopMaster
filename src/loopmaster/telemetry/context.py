"""Context management and W3C TraceContext propagation for OpenTelemetry."""

from __future__ import annotations

import re
from contextvars import ContextVar, Token

from .types import Span, SpanContext

_CURRENT_SPAN: ContextVar[Span | None] = ContextVar("loopmaster_current_span", default=None)

W3C_TRACEPARENT_PATTERN = re.compile(
    r"^00-(?P<trace_id>[0-9a-f]{32})-(?P<span_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)


def get_current_span() -> Span | None:
    """Return the currently active Span in the execution context."""
    return _CURRENT_SPAN.get()


def set_current_span(span: Span | None) -> Token[Span | None]:
    """Set the active Span in the execution context, returning a restore token."""
    return _CURRENT_SPAN.set(span)


def reset_current_span(token: Token[Span | None]) -> None:
    """Restore the previous active Span using the token."""
    _CURRENT_SPAN.reset(token)


def extract_w3c_traceparent(header: str | None) -> SpanContext | None:
    """Parse a W3C traceparent header string.

    Example: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
    """
    if not header or not isinstance(header, str):
        return None
    match = W3C_TRACEPARENT_PATTERN.match(header.strip())
    if not match:
        return None

    trace_id = match.group("trace_id")
    span_id = match.group("span_id")
    flags = int(match.group("flags"), 16)

    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None

    return SpanContext(
        trace_id=trace_id,
        span_id=span_id,
        trace_flags=flags,
        is_remote=True,
    )


def inject_w3c_traceparent(context: SpanContext) -> str:
    """Format a SpanContext into a standard W3C traceparent header string."""
    flags_hex = f"{context.trace_flags:02x}"
    return f"00-{context.trace_id}-{context.span_id}-{flags_hex}"
