"""LoopMaster OpenTelemetry observability package."""

from __future__ import annotations

from .context import (
    extract_w3c_traceparent,
    get_current_span,
    inject_w3c_traceparent,
    reset_current_span,
    set_current_span,
)
from .exporter import (
    InMemorySpanExporter,
    JsonFileSpanExporter,
    OTLPConfig,
    OTLPHttpSpanExporter,
    SpanExporter,
    format_otlp_resource_spans,
)
from .provider import (
    TelemetryProvider,
    configure_telemetry,
    get_global_provider,
    get_tracer,
    reset_telemetry,
    set_global_provider,
)
from .tracer import NoOpSpan, NoOpTracer, Tracer, generate_span_id, generate_trace_id
from .types import (
    Span,
    SpanContext,
    SpanEvent,
    SpanKind,
    SpanStatus,
    SpanStatusCode,
)

__all__ = [
    "InMemorySpanExporter",
    "JsonFileSpanExporter",
    "NoOpSpan",
    "NoOpTracer",
    "OTLPConfig",
    "OTLPHttpSpanExporter",
    "Span",
    "SpanContext",
    "SpanEvent",
    "SpanExporter",
    "SpanKind",
    "SpanStatus",
    "SpanStatusCode",
    "TelemetryProvider",
    "Tracer",
    "configure_telemetry",
    "extract_w3c_traceparent",
    "format_otlp_resource_spans",
    "generate_span_id",
    "generate_trace_id",
    "get_current_span",
    "get_global_provider",
    "get_tracer",
    "inject_w3c_traceparent",
    "reset_current_span",
    "reset_telemetry",
    "set_current_span",
    "set_global_provider",
]
