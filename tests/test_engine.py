"""Tests for core/engine.py — LoopEngine runtime."""

from __future__ import annotations

import pytest

from loopmaster.core.engine import LoopEngine, LoopRunResult
from loopmaster.core.exceptions import BudgetExceededError
from loopmaster.core.types import (
    Budget,
    ErrorPolicy,
    InterruptionProtection,
    Loop,
    RecoveryAction,
    Step,
    StepOutput,
)

# ── Helpers ─────────────────────────────────────────────────


def make_step(name: str, output: str = "ok") -> Step:
    def callback(step, ctx_data):
        return StepOutput(updates={name: output})

    s = Step(name=name)
    s._engine_callback = callback
    return s


# ── Basic execution ─────────────────────────────────────────


class TestBasicExecution:
    def test_single_step(self):
        @Loop(name="simple")
        def my_loop(ctx):
            Step("s1", input="hello")

        engine = LoopEngine()
        engine.register(my_loop)
        result = engine.run(my_loop)
        assert result.success is True
        assert "s1" in result.results
        assert result.results["s1"].success is True

    def test_multiple_steps(self):
        @Loop(name="multi")
        def my_loop(ctx):
            Step("s1", input="a")
            Step("s2", input="b")

        engine = LoopEngine()
        engine.register(my_loop)
        result = engine.run(my_loop)
        assert result.success is True
        assert len(result.steps_executed) == 2
        assert result.steps_executed == ["s1", "s2"]

    def test_step_with_callback(self):
        @Loop(name="cb")
        def my_loop(ctx):
            s1 = Step("s1")
            s1._engine_callback = lambda s, c: StepOutput(updates={"x": 42})

        engine = LoopEngine()
        engine.register(my_loop)
        result = engine.run(my_loop)
        assert result.success is True
        assert "s1" in result.results
        assert result.results["s1"].success is True

    def test_context_passed_between_steps(self):
        @Loop(name="ctx_pass")
        def my_loop(ctx):
            s1 = Step("s1")
            s1._engine_callback = lambda s, c: StepOutput(updates={"val": 10})
            s2 = Step("s2")
            s2._engine_callback = lambda s, c: StepOutput(updates={"doubled": c.get("val", 0) * 2})

        engine = LoopEngine()
        result = engine.run(my_loop)
        assert result.success is True
        assert result.results["s2"].output.updates["doubled"] == 20


# ── Resume from checkpoint ──────────────────────────────────


class TestResume:
    def test_resume_skips_executed_steps(self):
        from loopmaster.core.types import CheckpointData

        @Loop(name="resume_test")
        def my_loop(ctx):
            Step("s1", input="a")
            Step("s2", input="b")
            Step("s3", input="c")

        engine = LoopEngine()
        engine.register(my_loop)

        # Simulate checkpoint after s1
        cp = CheckpointData(
            loop_name="resume_test",
            loop_version="0.1.0",
            loop_source_hash=my_loop.source_hash,
            step_index=1,
            context_data={},
            completed_results={},
            executed_step_names=["s1"],
        )

        result = engine.run(my_loop, resume_checkpoint=cp)
        assert result.success is True
        assert "s1" not in result.results
        assert "s2" in result.results
        assert "s3" in result.results
        assert result.resume_count == 1


# ── Budget enforcement ──────────────────────────────────────


class TestBudget:
    def test_max_steps_exceeded(self):
        @Loop(name="budget_steps")
        def my_loop(ctx):
            Step("s1", input="a")
            Step("s2", input="b")
            Step("s3", input="c")

        engine = LoopEngine(budget=Budget(max_steps=2))
        engine.register(my_loop)

        with pytest.raises(BudgetExceededError):
            engine.run(my_loop)

    def test_max_cost_exceeded(self):
        @Loop(name="budget_cost")
        def my_loop(ctx):
            Step("s1", input="a")

        engine = LoopEngine(budget=Budget(max_steps=0))
        engine.register(my_loop)
        with pytest.raises(BudgetExceededError):
            engine.run(my_loop)


# ── Error recovery ──────────────────────────────────────────


class TestErrorRecovery:
    def test_step_failure_with_skip(self):
        @Loop(name="skip_test")
        def my_loop(ctx):
            s1 = Step("s1")
            s1._engine_callback = lambda s, c: (_ for _ in ()).throw(RuntimeError("fail"))

        engine = LoopEngine(error_policy=ErrorPolicy(retry=0, on_failure=RecoveryAction.SKIP))
        engine.register(my_loop)
        result = engine.run(my_loop)
        assert result.success is False
        assert result.results["s1"].error is not None

    def test_step_failure_with_retry(self):
        call_count = 0

        def flaky_callback(step, ctx_data):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient")
            return StepOutput(updates={"x": 1})

        @Loop(name="retry_test")
        def my_loop(ctx):
            s1 = Step("s1")
            s1._engine_callback = flaky_callback

        engine = LoopEngine(error_policy=ErrorPolicy(retry=3, backoff=0.01))
        engine.register(my_loop)
        result = engine.run(my_loop)
        assert result.success is True
        assert call_count == 3


# ── Interruption protection ─────────────────────────────────


class TestInterruptionProtection:
    def test_heartbeat_starts_and_stops(self):
        ip = InterruptionProtection(
            enabled=True,
            heartbeat_interval=0.1,
            heartbeat_timeout=0.2,
        )

        @Loop(name="ip_test")
        def my_loop(ctx):
            Step("s1", input="ok")

        engine = LoopEngine(interruption_protection=ip)
        engine.register(my_loop)
        result = engine.run(my_loop)
        assert result.success is True
        assert engine._heartbeat is None


# ── Callbacks ───────────────────────────────────────────────


class TestCallbacks:
    def test_on_step_complete(self):
        completed_steps = []

        def on_complete(result):
            completed_steps.append(result.step_name)

        @Loop(name="cb_test")
        def my_loop(ctx):
            Step("s1", input="a")
            Step("s2", input="b")

        engine = LoopEngine()
        engine.on_step_complete(on_complete)
        engine.register(my_loop)
        engine.run(my_loop)
        assert completed_steps == ["s1", "s2"]


# ── LoopRunResult ───────────────────────────────────────────


class TestLoopRunResult:
    def test_success_result(self):
        @Loop(name="res")
        def my_loop(ctx):
            Step("s1", input="ok")

        engine = LoopEngine()
        result = engine.run(my_loop)
        assert isinstance(result, LoopRunResult)
        assert result.success is True
        assert result.total_cost == 0.0
        assert result.total_tokens == 0
        assert result.interrupted is False


# ── MetricsCollector + CostTracker integration ───────────────


class TestEngineMetricsIntegration:
    def test_metrics_collector_records_steps(self):
        from loopmaster.metrics.collector import MetricsCollector

        collector = MetricsCollector()
        engine = LoopEngine(metrics_collector=collector)

        @Loop(name="m1")
        def my_loop(ctx):
            Step("s1", input="ok")

        engine.run(my_loop)
        metrics = collector.get_loop_metrics("m1")
        assert metrics is not None
        assert metrics.steps_executed == 1

    def test_cost_tracker_records_steps(self):
        from loopmaster.cost.tracker import CostTracker

        tracker = CostTracker()
        engine = LoopEngine(cost_tracker=tracker)

        def step_fn(step, ctx_data):
            out = StepOutput(updates={"ok": True})
            out._tokens = 1000
            return out

        @Loop(name="c1")
        def my_loop(ctx):
            s = Step(name="s1", model="gpt-4o")
            s._engine_callback = step_fn

        engine.run(my_loop)
        assert tracker.total_cost > 0.0
        assert tracker.total_input_tokens > 0

    def test_both_collectors_together(self):
        from loopmaster.cost.tracker import CostTracker
        from loopmaster.metrics.collector import MetricsCollector

        collector = MetricsCollector()
        tracker = CostTracker()
        engine = LoopEngine(metrics_collector=collector, cost_tracker=tracker)

        def step_fn(step, ctx_data):
            out = StepOutput(updates={"ok": True})
            out._tokens = 500
            return out

        @Loop(name="both")
        def my_loop(ctx):
            s1 = Step(name="s1", model="gpt-4o")
            s1._engine_callback = step_fn
            s2 = Step(name="s2", model="gpt-4o-mini")
            s2._engine_callback = step_fn

        engine.run(my_loop)
        assert collector.get_loop_metrics("both").steps_executed == 2
        assert tracker.total_cost > 0.0

    def test_no_collectors_still_works(self):
        engine = LoopEngine()

        @Loop(name="nc")
        def my_loop(ctx):
            Step("s1", input="ok")

        result = engine.run(my_loop)
        assert result.success is True

    def test_retry_recorded_in_metrics(self):
        from loopmaster.metrics.collector import MetricsCollector

        collector = MetricsCollector()
        policy = ErrorPolicy(retry=1, backoff=0)
        engine = LoopEngine(error_policy=policy, metrics_collector=collector)

        attempt = {"n": 0}

        def flaky_fn(step, ctx_data):
            attempt["n"] += 1
            if attempt["n"] == 1:
                raise RuntimeError("RateLimitError")
            return StepOutput(updates={"ok": True})

        @Loop(name="retry_loop")
        def my_loop(ctx):
            s = Step(name="retry_s", retry=2)
            s._engine_callback = flaky_fn

        engine.run(my_loop)
        metrics = collector.get_loop_metrics("retry_loop")
        assert metrics.retries == 1


def test_loop_body_not_reexecuted_on_resume(tmp_path):
    """Loop body must not run again when resuming from checkpoint."""
    from loopmaster.checkpoint import CheckpointManager

    body_call_count = {"n": 0}

    @Loop(name="cache_test")
    def my_loop(ctx):
        body_call_count["n"] += 1
        s1 = Step("s1", input="hello")
        s1._engine_callback = lambda s, c: StepOutput(updates={"x": 1})
        s2 = Step("s2", input="world")
        s2._engine_callback = lambda s, c: StepOutput(updates={"y": 2})

    engine = LoopEngine(checkpoint_dir=str(tmp_path))
    result = engine.run(my_loop, initial_context={"a": 1})

    assert body_call_count["n"] == 1
    assert len(result.results) == 2

    mgr = CheckpointManager(str(tmp_path))
    cp = mgr.load_latest("cache_test")
    assert cp is not None

    engine.run(my_loop, resume_checkpoint=cp)
    assert body_call_count["n"] == 1, "Body was re-executed during resume"


def test_dynamic_body_fresh_run_recollects(tmp_path):
    """Dynamic body with context-dependent steps collects fresh on each run."""
    engine = LoopEngine(checkpoint_dir=str(tmp_path))

    @Loop(name="dynamic_loop")
    def my_loop(ctx):
        mode = ctx.get("mode", "default")
        s1 = Step(f"step_{mode}", input=mode)
        s1._engine_callback = lambda s, c: StepOutput(updates={"mode": c.get("mode")})

    r1 = engine.run(my_loop, initial_context={"mode": "fast"})
    assert "step_fast" in r1.results

    r2 = engine.run(my_loop, initial_context={"mode": "slow"})
    assert "step_slow" in r2.results
