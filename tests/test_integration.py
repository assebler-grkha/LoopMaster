"""Integration tests — end-to-end loop execution."""

from __future__ import annotations

from pathlib import Path

import pytest

from loopmaster import (
    Budget,
    Context,
    ErrorPolicy,
    Loop,
    LoopEngine,
    Step,
)
from loopmaster.cost.tracker import CostTracker
from loopmaster.metrics.collector import MetricsCollector


class TestEndToEndLoop:
    """Full loop execution with all features."""

    def test_basic_loop_execution(self) -> None:
        engine = LoopEngine()

        @Loop(name="basic", version="0.1.0")
        def basic_loop(ctx: Context) -> Context:
            Step("greet", model="gpt-4", prompt="Hello")
            Step("farewell", model="gpt-4", prompt="Goodbye")
            return ctx

        engine.register(basic_loop)
        result = engine.run(basic_loop, {})

        assert result.success
        assert len(result.steps_executed) == 2
        assert "greet" in result.steps_executed
        assert "farewell" in result.steps_executed

    def test_loop_with_metrics_and_cost(self, tmp_path: Path) -> None:
        collector = MetricsCollector(storage_dir=str(tmp_path))
        tracker = CostTracker()
        engine = LoopEngine(
            metrics_collector=collector,
            cost_tracker=tracker,
            checkpoint_dir=str(tmp_path),
        )

        @Loop(name="tracked", version="0.1.0")
        def tracked_loop(ctx: Context) -> Context:
            Step("step_a", model="gpt-4o", prompt="Task A")
            Step("step_b", model="gpt-4o-mini", prompt="Task B")
            return ctx

        engine.register(tracked_loop)
        result = engine.run(tracked_loop, {})

        assert result.success
        assert collector.get_loop_metrics("tracked") is not None
        metrics = collector.get_loop_metrics("tracked")
        assert metrics is not None
        assert metrics.steps_executed == 2
        assert len(collector._points) > 0

    def test_loop_with_budget_limit(self) -> None:
        from loopmaster.core.exceptions import BudgetExceededError

        engine = LoopEngine(budget=Budget(max_steps=2))

        @Loop(name="budgeted", version="0.1.0")
        def budgeted_loop(ctx: Context) -> Context:
            Step("s1", model="gpt-4", prompt="One")
            Step("s2", model="gpt-4", prompt="Two")
            Step("s3", model="gpt-4", prompt="Three")
            return ctx

        engine.register(budgeted_loop)
        with pytest.raises(BudgetExceededError):
            engine.run(budgeted_loop, {})

    def test_loop_with_error_recovery(self) -> None:
        engine = LoopEngine(error_policy=ErrorPolicy(retry=0, on_failure="skip"))

        @Loop(name="recovery", version="0.1.0")
        def recovery_loop(ctx: Context) -> Context:
            Step("ok_step", model="gpt-4", prompt="Works")
            Step("bad_step", model="gpt-4", prompt="Fails")
            return ctx

        engine.register(recovery_loop)
        result = engine.run(recovery_loop, {})

        assert "ok_step" in result.steps_executed

    def test_loop_resume_from_checkpoint(self, tmp_path: Path) -> None:
        engine1 = LoopEngine(checkpoint_dir=str(tmp_path))

        @Loop(name="resumable", version="0.1.0")
        def resumable_loop(ctx: Context) -> Context:
            Step("first", model="gpt-4", prompt="First")
            Step("second", model="gpt-4", prompt="Second")
            return ctx

        engine1.register(resumable_loop)
        result1 = engine1.run(resumable_loop, {})
        assert result1.success
        assert result1.last_checkpoint is not None

        engine2 = LoopEngine(checkpoint_dir=str(tmp_path))
        engine2.register(resumable_loop)
        result2 = engine2.run(resumable_loop, {}, resume_checkpoint=result1.last_checkpoint)
        assert result2.resume_count == 1

    def test_loop_with_context_passing(self) -> None:
        engine = LoopEngine()

        @Loop(name="ctx_test", version="0.1.0")
        def ctx_loop(ctx: Context) -> Context:
            Step("step1", model="gpt-4", prompt="hello")
            return ctx

        engine.register(ctx_loop)
        result = engine.run(ctx_loop, {"key": "value"})
        assert result.success

    def test_loop_step_callback(self) -> None:
        engine = LoopEngine()
        results_received: list = []

        engine.on_step_complete(lambda r: results_received.append(r))

        @Loop(name="callback_test", version="0.1.0")
        def cb_loop(ctx: Context) -> Context:
            Step("s1", model="gpt-4", prompt="One")
            return ctx

        engine.register(cb_loop)
        engine.run(cb_loop, {})

        assert len(results_received) == 1
        assert results_received[0].step_name == "s1"

    def test_loop_checkpoint_saved_during_run(self, tmp_path: Path) -> None:
        engine = LoopEngine(checkpoint_dir=str(tmp_path))

        @Loop(name="cp_test", version="0.1.0")
        def cp_loop(ctx: Context) -> Context:
            Step("s1", model="gpt-4", prompt="One")
            return ctx

        engine.register(cp_loop)
        result = engine.run(cp_loop, {})
        assert result.checkpoint_saved
        assert (tmp_path / "cp_test_latest.json").exists()


class TestLoopDefIntrospection:
    """Test LoopDef metadata and decorator behavior."""

    def test_loop_def_has_metadata(self) -> None:
        @Loop(name="meta", version="0.2.0")
        def meta_loop(ctx: Context) -> Context:
            return ctx

        assert meta_loop.name == "meta"
        assert meta_loop.version == "0.2.0"
        assert meta_loop.source_hash is not None

    def test_step_auto_registers(self) -> None:
        @Loop(name="auto", version="0.1.0")
        def auto_loop(ctx: Context) -> Context:
            Step("a", model="gpt-4", prompt="A")
            Step("b", model="gpt-4", prompt="B")
            return ctx

        assert auto_loop.name == "auto"


class TestAgentAdapterIntegration:
    """Test agent adapter discovery and registry."""

    def test_registry_discovers_adapters(self) -> None:
        from loopmaster.agents import AgentRegistry

        registry = AgentRegistry()
        adapters = registry.get_all_adapters()
        assert "opencode" in adapters
        assert "claude-code" in adapters
        assert "cursor" in adapters

    def test_registry_get_adapter(self) -> None:
        from loopmaster.agents import AgentRegistry

        registry = AgentRegistry()
        adapter = registry.get_adapter("opencode")
        assert adapter is not None


class TestSQLiteExporterIntegration:
    """Test SQLite exporter with real MetricsCollector."""

    def test_export_and_query(self, tmp_path: Path) -> None:
        from loopmaster.metrics.collector import MetricsCollector
        from loopmaster.metrics.sqlite_exporter import SQLiteExporter

        collector = MetricsCollector()
        collector.start_loop("test_loop")
        collector.record_step("test_loop", "step1", 0.01, 100, 50.0, True)
        collector.record_step("test_loop", "step2", 0.02, 200, 80.0, True)
        collector.end_loop("test_loop")

        db_path = tmp_path / "test.db"
        with SQLiteExporter(str(db_path)) as exp:
            exp.export_collector(collector)
            points = exp.query_points()
            assert len(points) >= 6
            loops = exp.query_loops()
            assert len(loops) == 1
            summary = exp.summary()
            assert summary["total_loop_metrics"] == 1
