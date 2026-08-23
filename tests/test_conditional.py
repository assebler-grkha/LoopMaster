"""Tests for Conditional branching in LoopMaster DSL and LoopEngine."""

from __future__ import annotations

import yaml

from loopmaster import (
    Conditional,
    Loop,
    LoopEngine,
    Step,
)
from loopmaster.core.condition import evaluate_condition
from loopmaster.core.yaml_export import export_loop
from loopmaster.events import EventEmitter
from loopmaster.mcp.discovery import loop_def_to_dict
from loopmaster.telemetry import (
    InMemorySpanExporter,
    TelemetryProvider,
    reset_telemetry,
    set_global_provider,
)


class TestConditionEvaluation:
    def test_callable_condition(self):
        assert evaluate_condition(lambda ctx: ctx.get("is_bug") is True, {"is_bug": True}) is True
        assert evaluate_condition(lambda ctx: ctx.get("is_bug") is True, {"is_bug": False}) is False
        # Exception in callable returns False safely
        assert evaluate_condition(lambda ctx: ctx["missing_key"] == 1, {}) is False

    def test_key_lookup_condition(self):
        assert evaluate_condition("is_active", {"is_active": True}) is True
        assert evaluate_condition("is_active", {"is_active": False}) is False
        assert evaluate_condition("missing", {}) is False

    def test_safe_ast_expression_condition(self):
        ctx = {"status": "success", "score": 85, "tags": ["prod", "ai"]}
        assert evaluate_condition("status == 'success'", ctx) is True
        assert evaluate_condition("score >= 80", ctx) is True
        assert evaluate_condition("score < 50", ctx) is False
        assert evaluate_condition("'prod' in tags", ctx) is True
        assert evaluate_condition("status == 'success' and score > 80", ctx) is True
        assert evaluate_condition("not (score < 50)", ctx) is True

    def test_malicious_expression_rejected_safely(self):
        ctx = {"x": 10}
        # Function calls or imports should be rejected by whitelist and return False safely
        assert evaluate_condition("__import__('os').system('echo pwned')", ctx) is False
        assert evaluate_condition("exec('import sys')", ctx) is False


class TestDSLCollectionAndScoping:
    def test_conditional_scoping_no_unhashable_error(self):
        @Loop(name="branching_loop")
        def pipeline(ctx):
            Step("step_pre", input="start")
            Conditional(
                condition=lambda c: c.get("flag") is True,
                then_steps=[
                    Step("step_then_1", input="t1"),
                    Step("step_then_2", input="t2"),
                ],
                else_steps=[
                    Step("step_else_1", input="e1"),
                ],
            )
            Step("step_post", input="end")

        engine = LoopEngine()
        collected = engine._collect_steps_from_loop(
            pipeline, ctx=None, executed_steps=[], results={}
        )

        # Outer list must contain only step_pre, Conditional, step_post
        assert len(collected) == 3
        assert collected[0].name == "step_pre"
        assert isinstance(collected[1], Conditional)
        assert collected[2].name == "step_post"

        cond = collected[1]
        assert len(cond.then_steps) == 2
        assert cond.then_steps[0].name == "step_then_1"
        assert cond.then_steps[1].name == "step_then_2"
        assert len(cond.else_steps) == 1
        assert cond.else_steps[0].name == "step_else_1"

    def test_nested_conditionals(self):
        @Loop(name="nested_loop")
        def nested_pipeline(ctx):
            Conditional(
                name="outer_cond",
                condition="outer_flag",
                then_steps=[
                    Conditional(
                        name="inner_cond",
                        condition="inner_flag",
                        then_steps=[Step("deep_step")],
                    )
                ],
            )

        engine = LoopEngine()
        collected = engine._collect_steps_from_loop(
            nested_pipeline, ctx=None, executed_steps=[], results={}
        )
        assert len(collected) == 1
        outer = collected[0]
        assert isinstance(outer, Conditional)
        assert len(outer.then_steps) == 1
        inner = outer.then_steps[0]
        assert isinstance(inner, Conditional)
        assert len(inner.then_steps) == 1
        assert inner.then_steps[0].name == "deep_step"


class TestEngineExecutionFlow:
    def test_executes_then_branch_when_condition_true(self):
        executed_order: list[str] = []

        @Loop(name="true_branch_loop")
        def pipeline(ctx):
            Step("start", _engine_callback=lambda s, c: executed_order.append("start"))
            Conditional(
                condition=lambda c: c.get("run_then") is True,
                then_steps=[
                    Step(
                        "then_1",
                        _engine_callback=lambda s, c: (
                            executed_order.append("then_1"),
                            {"val": 42},
                        )[1],
                    ),
                    Step("then_2", _engine_callback=lambda s, c: executed_order.append("then_2")),
                ],
                else_steps=[
                    Step("else_1", _engine_callback=lambda s, c: executed_order.append("else_1")),
                ],
            )
            Step("end", _engine_callback=lambda s, c: executed_order.append(f"end_{c.get('val')}"))

        engine = LoopEngine()
        res = engine.run(pipeline, initial_context={"run_then": True})

        assert res.success is True
        assert executed_order == ["start", "then_1", "then_2", "end_42"]
        assert "then_1" in res.results
        assert "then_2" in res.results
        assert "else_1" not in res.results

    def test_executes_else_branch_when_condition_false(self):
        executed_order: list[str] = []

        @Loop(name="false_branch_loop")
        def pipeline(ctx):
            Conditional(
                condition=lambda c: c.get("flag") is True,
                then_steps=[
                    Step("then_step", _engine_callback=lambda s, c: executed_order.append("then"))
                ],
                else_steps=[
                    Step("else_step", _engine_callback=lambda s, c: executed_order.append("else"))
                ],
            )

        engine = LoopEngine()
        res = engine.run(pipeline, initial_context={"flag": False})

        assert res.success is True
        assert executed_order == ["else"]
        assert "else_step" in res.results
        assert "then_step" not in res.results


class TestResumeBranchStickiness:
    def test_branch_stickiness_prevents_flipping_when_context_mutated(self):
        """If step_then_1 ran and flipped status='done', resume must stick to then_steps."""
        run_count = {"step_1": 0, "step_2": 0, "else_step": 0}

        @Loop(name="sticky_loop")
        def sticky_pipeline(ctx):
            Step("classify", _engine_callback=lambda s, c: {"status": "pending"})
            Conditional(
                condition=lambda c: c.get("status") == "pending",
                then_steps=[
                    Step(
                        "step_1",
                        _engine_callback=lambda s, c: (
                            run_count.__setitem__("step_1", run_count["step_1"] + 1),
                            {"status": "done"},
                        )[1],
                    ),
                    Step(
                        "step_2",
                        _engine_callback=lambda s, c: run_count.__setitem__(
                            "step_2", run_count["step_2"] + 1
                        ),
                    ),
                ],
                else_steps=[
                    Step(
                        "else_step",
                        _engine_callback=lambda s, c: run_count.__setitem__(
                            "else_step", run_count["else_step"] + 1
                        ),
                    ),
                ],
            )

        # Simulate checkpoint where 'classify' and 'step_1' completed, and context['status'] became 'done'
        from loopmaster.core.types import CheckpointData

        cp = CheckpointData(
            loop_name="sticky_loop",
            loop_version="0.1.0",
            loop_source_hash=sticky_pipeline.source_hash,
            step_index=1,
            context_data={"status": "done"},
            completed_results={},
            executed_step_names=["classify", "step_1"],
        )

        engine = LoopEngine()
        res = engine.run(sticky_pipeline, resume_checkpoint=cp)

        assert res.success is True
        # step_1 was skipped because it was already executed
        assert run_count["step_1"] == 0
        # step_2 executed because branch stickiness kept then_steps despite status == 'done'
        assert run_count["step_2"] == 1
        # else_step was NOT executed
        assert run_count["else_step"] == 0


class TestObservabilityAndExport:
    def test_branch_selected_event_and_otel_span(self):
        reset_telemetry()
        exporter = InMemorySpanExporter()
        provider = TelemetryProvider(exporter=exporter)
        set_global_provider(provider)

        emitter = EventEmitter()
        engine = LoopEngine(event_emitter=emitter)

        @Loop(name="traced_branch_loop")
        def pipeline(ctx):
            Conditional(
                name="check_active",
                condition=lambda c: True,
                then_steps=[Step("step_a")],
                else_steps=[Step("step_b")],
            )

        res = engine.run(pipeline)
        assert res.success is True

        events = [e for e in emitter.history if e.event_type == "branch_selected"]
        assert len(events) == 1
        ev = events[0]
        assert ev.payload["branch"] == "then"
        assert ev.payload["condition_result"] is True

        spans = exporter.get_finished_spans()
        cond_span = next(s for s in spans if s.name == "conditional.check_active")
        assert cond_span.attributes["loopmaster.branch"] == "then"
        assert cond_span.attributes["loopmaster.condition_result"] is True
        reset_telemetry()

    def test_yaml_export_and_mcp_discovery(self):
        @Loop(name="export_test_loop", version="1.2.0")
        def pipeline(ctx):
            Step("init", model="gpt-4o")
            Conditional(
                name="is_error",
                condition="has_error",
                then_steps=[Step("retry_step", model="gpt-4o", prompt="Retry prompt")],
                else_steps=[Step("proceed_step", model="gpt-4o", prompt="Proceed prompt")],
            )

        # YAML export
        yaml_str = export_loop(pipeline)
        data = yaml.safe_load(yaml_str)
        assert data["name"] == "export_test_loop"
        assert data["version"] == "1.2.0"

        # MCP discovery
        from pathlib import Path

        disc = loop_def_to_dict(pipeline, Path("loops/test.py"))
        assert disc["name"] == "export_test_loop"
        assert len(disc["steps"]) == 2
        cond_dict = disc["steps"][1]
        assert "conditional" in cond_dict
        assert len(cond_dict["conditional"]["then"]) == 1
        assert len(cond_dict["conditional"]["else"]) == 1
