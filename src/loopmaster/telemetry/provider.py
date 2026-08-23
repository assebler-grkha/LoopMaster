"""Telemetry provider and global configuration for OpenTelemetry tracing."""

from __future__ import annotations

import logging
import os
from typing import Any

from .exporter import (
    InMemorySpanExporter,
    JsonFileSpanExporter,
    OTLPConfig,
    OTLPHttpSpanExporter,
    SpanExporter,
)
from .tracer import NoOpTracer, Tracer
from .types import Span

logger = logging.getLogger("loopmaster.telemetry.provider")


class TelemetryProvider:
    """Manages Tracers and Exporters for the application."""

    def __init__(
        self,
        exporter: SpanExporter | None = None,
        service_name: str = "loopmaster",
    ) -> None:
        self.service_name = service_name
        self.exporter = exporter
        self._tracers: dict[str, Tracer] = {}

    def get_tracer(self, name: str = "loopmaster", version: str = "1.0.0") -> Tracer:
        """Get or create a Tracer instance."""
        if self.exporter is None:
            return NoOpTracer()

        if name not in self._tracers:
            self._tracers[name] = Tracer(
                name=name,
                version=version,
                on_span_ended=self._on_span_ended,
            )
        return self._tracers[name]

    def _on_span_ended(self, span: Span) -> None:
        if self.exporter is not None:
            self.exporter.export([span])

    def flush(self) -> None:
        """Flush pending spans in the exporter."""
        if self.exporter is not None:
            self.exporter.flush()

    def shutdown(self) -> None:
        """Shutdown exporter and background resources."""
        if self.exporter is not None:
            self.exporter.shutdown()


_GLOBAL_PROVIDER: TelemetryProvider | None = None


def get_global_provider() -> TelemetryProvider:
    """Return the global TelemetryProvider instance or initialize a default no-op one."""
    global _GLOBAL_PROVIDER
    if _GLOBAL_PROVIDER is None:
        _GLOBAL_PROVIDER = TelemetryProvider(exporter=None)
    return _GLOBAL_PROVIDER


def set_global_provider(provider: TelemetryProvider) -> None:
    """Set the global TelemetryProvider instance."""
    global _GLOBAL_PROVIDER
    _GLOBAL_PROVIDER = provider


def get_tracer(name: str = "loopmaster", version: str = "1.0.0") -> Tracer:
    """Convenience function to get a Tracer from the global provider."""
    return get_global_provider().get_tracer(name, version)


def configure_telemetry(
    exporter_type: str | None = None,
    service_name: str | None = None,
    otlp_endpoint: str | None = None,
    file_path: str | None = None,
    **kwargs: Any,
) -> TelemetryProvider:
    """Configure the global OpenTelemetry provider based on parameters or environment variables."""
    exp_type = exporter_type or os.getenv("OTEL_TRACES_EXPORTER", "none").lower()
    svc_name = service_name or os.getenv("OTEL_SERVICE_NAME") or "loopmaster"

    exporter: SpanExporter | None = None
    if exp_type in ("otlp", "http", "otlp_http"):
        cfg = OTLPConfig(service_name=svc_name)
        if otlp_endpoint:
            cfg.endpoint = otlp_endpoint
        exporter = OTLPHttpSpanExporter(config=cfg)
    elif exp_type == "file":
        target_path = file_path or os.getenv("OTEL_FILE_PATH") or ".loopmaster/traces.json"
        exporter = JsonFileSpanExporter(target_path, service_name=svc_name)
    elif exp_type in ("memory", "in_memory"):
        exporter = InMemorySpanExporter()

    provider = TelemetryProvider(exporter=exporter, service_name=svc_name)
    set_global_provider(provider)
    return provider


def reset_telemetry() -> None:
    """Reset the global provider to None (used in tests)."""
    global _GLOBAL_PROVIDER
    if _GLOBAL_PROVIDER is not None:
        _GLOBAL_PROVIDER.shutdown()
        _GLOBAL_PROVIDER = None
