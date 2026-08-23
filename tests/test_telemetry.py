"""Tests for OpenTelemetry (OTel / OTLP) tracing, metrics, and exporter subsystems."""

from __future__ import annotations

import json
import tempfile
import urllib.error
from unittest.mock import MagicMock, patch

from loopmaster import Loop, Step
from loopmaster.core.engine import LoopEngine
from loopmaster.metrics.collector import MetricsCollector
from loopmaster.telemetry import (
    InMemorySpanExporter,
    JsonFileSpanExporter,
    NoOpSpan,
    NoOpTracer,
    OTLPConfig,
    OTLPHttpSpanExporter,
    SpanContext,
    SpanKind,
    SpanStatusCode,
    TelemetryProvider,
    Tracer,
    extract_w3c_traceparent,
    format_otlp_resource_spans,
    generate_span_id,
    generate_trace_id,
    inject_w3c_traceparent,
    reset_telemetry,
    set_global_provider,
)


class TestW3CTraceContext:
    def test_generate_ids(self):
        tid = generate_trace_id()
        sid = generate_span_id()
        assert len(tid) == 32
        assert len(sid) == 16
        assert tid != "0" * 32
        assert sid != "0" * 16

    def test_extract_valid_traceparent(self):
        header = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        ctx = extract_w3c_traceparent(header)
        assert ctx is not None
        assert ctx.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
        assert ctx.span_id == "00f067aa0ba902b7"
        assert ctx.trace_flags == 1
        assert ctx.is_remote is True
        assert ctx.is_valid is True

    def test_extract_invalid_traceparent(self):
        assert extract_w3c_traceparent(None) is None
        assert extract_w3c_traceparent("") is None
        assert extract_w3c_traceparent("invalid-header") is None
        # All zeros invalid in OTel
        assert (
            extract_w3c_traceparent("00-00000000000000000000000000000000-0000000000000000-01")
            is None
        )

    def test_inject_traceparent(self):
        ctx = SpanContext(
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736", span_id="00f067aa0ba902b7", trace_flags=1
        )
        header = inject_w3c_traceparent(ctx)
        assert header == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


class TestTracerAndHierarchy:
    def test_parent_child_nesting(self):
        exporter = InMemorySpanExporter()
        tracer = Tracer(on_span_ended=lambda s: exporter.export([s]))

        with tracer.start_as_current_span("root_span", attributes={"env": "prod"}) as root:
            root.set_attribute("version", 1)
            with tracer.start_as_current_span("child_span", kind=SpanKind.CLIENT) as child:
                child.add_event("event_a", {"key": "val"})
                child.set_status(SpanStatusCode.OK)

        spans = exporter.get_finished_spans()
        assert len(spans) == 2
        child_span, root_span = spans[0], spans[1]

        assert child_span.name == "child_span"
        assert root_span.name == "root_span"
        assert child_span.context.trace_id == root_span.context.trace_id
        assert child_span.parent_span_id == root_span.context.span_id
        assert child_span.kind == SpanKind.CLIENT
        assert len(child_span.events) == 1
        assert child_span.events[0].name == "event_a"

    def test_noop_tracer(self):
        noop = NoOpTracer()
        span = noop.start_span("test")
        assert isinstance(span, NoOpSpan)
        with noop.start_as_current_span("context_test") as s:
            s.set_attribute("foo", "bar")
            s.add_event("ev")
            s.set_status(SpanStatusCode.OK)
            s.end()


class TestOTLPFormattingAndExporters:
    def test_otlp_json_wire_format(self):
        exporter = InMemorySpanExporter()
        tracer = Tracer(on_span_ended=lambda s: exporter.export([s]))

        with tracer.start_as_current_span("test_span") as s:
            s.set_attribute("str_attr", "hello")
            s.set_attribute("int_attr", 42)
            s.set_attribute("float_attr", 3.14)
            s.set_attribute("bool_attr", True)

        spans = exporter.get_finished_spans()
        payload = format_otlp_resource_spans(spans, "test_service")

        assert "resourceSpans" in payload
        res_span = payload["resourceSpans"][0]
        scope_span = res_span["scopeSpans"][0]
        span_json = scope_span["spans"][0]

        assert span_json["name"] == "test_span"
        attrs = {a["key"]: a["value"] for a in span_json["attributes"]}
        assert attrs["str_attr"] == {"stringValue": "hello"}
        assert attrs["int_attr"] == {"intValue": "42"}
        assert attrs["float_attr"] == {"doubleValue": 3.14}
        assert attrs["bool_attr"] == {"boolValue": True}
        assert isinstance(span_json["startTimeUnixNano"], str)
        assert isinstance(span_json["endTimeUnixNano"], str)

    def test_json_file_exporter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = f"{tmpdir}/traces.json"
            exporter = JsonFileSpanExporter(fpath)
            tracer = Tracer(on_span_ended=lambda s: exporter.export([s]))

            with tracer.start_as_current_span("disk_span"):
                pass
            exporter.flush()

            from pathlib import Path

            data = json.loads(Path(fpath).read_text(encoding="utf-8"))
            assert len(data["resourceSpans"][0]["scopeSpans"][0]["spans"]) == 1

    def test_otlp_http_exporter_batching_and_resilience(self):
        cfg = OTLPConfig(endpoint="http://localhost:4318", flush_interval_seconds=0.1, timeout=1.0)
        exporter = OTLPHttpSpanExporter(config=cfg)

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            tracer = Tracer(on_span_ended=lambda s: exporter.export([s]))
            with tracer.start_as_current_span("http_span"):
                pass
            exporter.flush()

            assert mock_urlopen.called
            req = mock_urlopen.call_args[0][0]
            assert req.full_url.endswith("/v1/traces")
            assert req.get_header("Content-type") == "application/json"

        exporter.shutdown()

    def test_otlp_http_exporter_network_error_silent(self):
        cfg = OTLPConfig(endpoint="http://localhost:4318", flush_interval_seconds=0.1)
        exporter = OTLPHttpSpanExporter(config=cfg)

        # Ensure network exceptions do not propagate
        with patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")
        ):
            tracer = Tracer(on_span_ended=lambda s: exporter.export([s]))
            with tracer.start_as_current_span("error_span"):
                pass
            exporter.flush()

        exporter.shutdown()


class TestMetricsOTLPExport:
    def test_collector_to_otlp_payload(self):
        collector = MetricsCollector()
        collector.record("step.duration_ms", 125.5, tags={"step": "lint", "loop": "main"})
        collector.record("step.cost", 0.0042, tags={"step": "lint"})

        payload = collector.to_otlp_payload(service_name="agent_runner")
        assert "resourceMetrics" in payload
        res_metric = payload["resourceMetrics"][0]
        scope_metric = res_metric["scopeMetrics"][0]
        metrics = scope_metric["metrics"]

        assert len(metrics) == 2
        m0 = metrics[0]
        assert m0["name"] == "step.duration_ms"
        dp = m0["gauge"]["dataPoints"][0]
        assert dp["asDouble"] == 125.5
        assert len(dp["attributes"]) == 2


class TestLoopEngineTelemetryIntegration:
    def setup_method(self):
        reset_telemetry()

    def teardown_method(self):
        reset_telemetry()

    def test_engine_emits_loop_and_step_spans(self):
        exporter = InMemorySpanExporter()
        provider = TelemetryProvider(exporter=exporter, service_name="loop_test")
        set_global_provider(provider)

        @Loop(name="telemetry_pipeline", version="1.0.0")
        def pipeline(ctx):
            Step("step1", input="data1")
            Step("step2", input="data2")

        engine = LoopEngine()
        result = engine.run(pipeline)
        assert result.success is True

        spans = exporter.get_finished_spans()
        # Should have 2 step spans and 1 loop span
        assert len(spans) == 3
        loop_span = next(s for s in spans if s.name == "loop.telemetry_pipeline")
        step1_span = next(s for s in spans if s.name == "step.step1")
        step2_span = next(s for s in spans if s.name == "step.step2")

        assert step1_span.parent_span_id == loop_span.context.span_id
        assert step2_span.parent_span_id == loop_span.context.span_id
        assert step1_span.context.trace_id == loop_span.context.trace_id

        assert loop_span.attributes["loopmaster.loop.name"] == "telemetry_pipeline"
        assert loop_span.attributes["loopmaster.steps_count"] == 2
        assert step1_span.attributes["loopmaster.step.name"] == "step1"
        assert step1_span.attributes["loopmaster.step.success"] is True

    def test_engine_llm_step_spans_with_genai_attributes(self):
        exporter = InMemorySpanExporter()
        provider = TelemetryProvider(exporter=exporter)
        set_global_provider(provider)

        mock_llm = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = "mocked reply"
        mock_resp.prompt_tokens = 12
        mock_resp.completion_tokens = 6
        mock_resp.total_tokens = 18
        mock_resp.model = "gpt-4o"
        mock_llm.complete.return_value = mock_resp
        mock_llm.config.provider = "openai"

        @Loop(name="llm_pipeline")
        def llm_loop(ctx):
            Step("generate", model="gpt-4o", prompt="Generate a slogan")

        engine = LoopEngine(llm_client=mock_llm)
        result = engine.run(llm_loop)
        assert result.success is True

        spans = exporter.get_finished_spans()
        # Should have 1 llm span, 1 step span, and 1 loop span
        assert len(spans) == 3
        llm_span = next(s for s in spans if s.name == "llm.gpt-4o")
        step_span = next(s for s in spans if s.name == "step.generate")
        loop_span = next(s for s in spans if s.name == "loop.llm_pipeline")

        assert llm_span.kind == SpanKind.CLIENT
        assert llm_span.parent_span_id == step_span.context.span_id
        assert step_span.parent_span_id == loop_span.context.span_id

        assert llm_span.attributes["gen_ai.request.model"] == "gpt-4o"
        assert llm_span.attributes["gen_ai.response.model"] == "gpt-4o"
        assert llm_span.attributes["gen_ai.usage.input_tokens"] == 12
        assert llm_span.attributes["gen_ai.usage.output_tokens"] == 6
