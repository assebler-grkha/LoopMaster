"""OTLP JSON exporters and serialization compliant with OpenTelemetry specifications."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .types import Span

logger = logging.getLogger("loopmaster.telemetry.exporter")


def parse_otlp_headers(headers_str: str | None) -> dict[str, str]:
    """Parse comma-separated key=value pairs into a headers dictionary."""
    if not headers_str:
        return {}
    res: dict[str, str] = {}
    for part in headers_str.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            res[k.strip()] = v.strip()
    return res


@dataclass
class OTLPConfig:
    """Configuration for OpenTelemetry OTLP exporters."""

    endpoint: str = field(
        default_factory=lambda: os.getenv(
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
            os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"),
        )
    )
    headers: dict[str, str] = field(
        default_factory=lambda: parse_otlp_headers(os.getenv("OTEL_EXPORTER_OTLP_HEADERS", ""))
    )
    timeout: float = 5.0
    batch_size: int = 64
    flush_interval_seconds: float = 1.0
    service_name: str = field(default_factory=lambda: os.getenv("OTEL_SERVICE_NAME", "loopmaster"))


def format_otlp_attribute(key: str, value: Any) -> dict[str, Any]:
    """Format a python primitive into a standard OTLP Proto3 JSON attribute object."""
    if isinstance(value, bool):
        val_obj: dict[str, Any] = {"boolValue": value}
    elif isinstance(value, int):
        val_obj = {"intValue": str(value)}
    elif isinstance(value, float):
        val_obj = {"doubleValue": value}
    else:
        val_obj = {"stringValue": str(value)}
    return {"key": key, "value": val_obj}


def format_otlp_span(span: Span) -> dict[str, Any]:
    """Format a Span into an OTLP JSON Span object."""
    start_str = str(span.start_time_ns)
    end_str = str(span.end_time_ns if span.end_time_ns is not None else span.start_time_ns)

    attrs = [format_otlp_attribute(k, v) for k, v in span.attributes.items()]
    events = [
        {
            "name": ev.name,
            "timeUnixNano": str(ev.timestamp_ns),
            "attributes": [format_otlp_attribute(k, v) for k, v in ev.attributes.items()],
        }
        for ev in span.events
    ]

    otlp_span: dict[str, Any] = {
        "traceId": span.context.trace_id,
        "spanId": span.context.span_id,
        "name": span.name,
        "kind": int(span.kind),
        "startTimeUnixNano": start_str,
        "endTimeUnixNano": end_str,
        "attributes": attrs,
        "events": events,
        "status": {"code": int(span.status.code), "message": span.status.message},
    }
    if span.parent_span_id:
        otlp_span["parentSpanId"] = span.parent_span_id

    return otlp_span


def format_otlp_resource_spans(spans: Sequence[Span], service_name: str) -> dict[str, Any]:
    """Wrap a list of spans into the standard OTLP ResourceSpans payload."""
    resource_attrs = [format_otlp_attribute("service.name", service_name)]
    otlp_spans = [format_otlp_span(s) for s in spans]

    return {
        "resourceSpans": [
            {
                "resource": {"attributes": resource_attrs},
                "scopeSpans": [
                    {
                        "scope": {"name": "loopmaster", "version": "1.0.0"},
                        "spans": otlp_spans,
                    }
                ],
            }
        ]
    }


class SpanExporter(Protocol):
    """Protocol for span exporters."""

    def export(self, spans: Sequence[Span]) -> bool: ...

    def flush(self) -> None: ...

    def shutdown(self) -> None: ...


class InMemorySpanExporter:
    """Thread-safe in-memory span collector for unit testing and local inspection."""

    def __init__(self) -> None:
        self._spans: list[Span] = []
        self._lock = threading.Lock()

    def export(self, spans: Sequence[Span]) -> bool:
        with self._lock:
            self._spans.extend(spans)
        return True

    def get_finished_spans(self) -> list[Span]:
        with self._lock:
            return list(self._spans)

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()

    def flush(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


class JsonFileSpanExporter:
    """Exports spans directly to an OTLP JSON formatted file on disk."""

    def __init__(self, filepath: str | Path, service_name: str = "loopmaster") -> None:
        self.filepath = Path(filepath)
        self.service_name = service_name
        self._spans: list[Span] = []
        self._lock = threading.Lock()

    def export(self, spans: Sequence[Span]) -> bool:
        with self._lock:
            self._spans.extend(spans)
            self._write_file()
        return True

    def _write_file(self) -> None:
        self.filepath.parent.mkdir(parents=True, exist_ok=True)
        payload = format_otlp_resource_spans(self._spans, self.service_name)
        self.filepath.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def flush(self) -> None:
        with self._lock:
            self._write_file()

    def shutdown(self) -> None:
        self.flush()


class OTLPHttpSpanExporter:
    """Non-blocking background OTLP/HTTP JSON span exporter via standard library."""

    def __init__(self, config: OTLPConfig | None = None) -> None:
        self.config = config or OTLPConfig()
        self._endpoint = self._normalize_endpoint(self.config.endpoint)
        self._queue: queue.Queue[Span | None] = queue.Queue(maxsize=2000)
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    @staticmethod
    def _normalize_endpoint(ep: str) -> str:
        trimmed = ep.rstrip("/")
        if trimmed.endswith("/v1/traces"):
            return trimmed
        return f"{trimmed}/v1/traces"

    def export(self, spans: Sequence[Span]) -> bool:
        for span in spans:
            try:
                self._queue.put_nowait(span)
            except queue.Full as exc:
                logger.warning("OTLP span queue full; dropping span '%s': %s", span.name, exc)
        return True

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            batch: list[Span] = []
            try:
                item = self._queue.get(timeout=self.config.flush_interval_seconds)
                if item is None:
                    break
                batch.append(item)
                while len(batch) < self.config.batch_size and not self._queue.empty():
                    next_item = self._queue.get_nowait()
                    if next_item is None:
                        break
                    batch.append(next_item)
            except queue.Empty:
                continue

            if batch:
                self._send_batch(batch)

    def _send_batch(self, batch: list[Span]) -> None:
        payload = format_otlp_resource_spans(batch, self.config.service_name)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self._endpoint, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        for k, v in self.config.headers.items():
            req.add_header(k, v)

        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                if resp.status >= 400:
                    logger.warning("OTLP export failed with status %s", resp.status)
        except Exception as exc:
            logger.debug("OTLP export network/server error: %s", exc)

    def flush(self) -> None:
        remaining: list[Span] = []
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                if item is not None:
                    remaining.append(item)
            except queue.Empty:
                break
        if remaining:
            self._send_batch(remaining)

    def shutdown(self) -> None:
        self._stop_event.set()
        self._queue.put(None)
        if self._worker.is_alive():
            self._worker.join(timeout=2.0)
        self.flush()
