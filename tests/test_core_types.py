"""Tests for core/types.py — DSL primitives."""

from __future__ import annotations

import pytest

from loopmaster.core.types import (
    Budget,
    CheckpointData,
    ErrorPolicy,
    InterruptionProtection,
    Loop,
    LoopDef,
    Parallel,
    RecoveryAction,
    Step,
    StepInput,
    StepOutput,
    StepResult,
    _get_source_hash,
)

# ── RecoveryAction ──────────────────────────────────────────


class TestRecoveryAction:
    def test_enum_values(self):
        assert RecoveryAction.ABORT.value == "abort"
        assert RecoveryAction.SKIP.value == "skip"
        assert RecoveryAction.RETRY.value == "retry"
        assert RecoveryAction.FALLBACK.value == "fallback"

    def test_all_members(self):
        assert set(RecoveryAction) == {
            RecoveryAction.ABORT,
            RecoveryAction.SKIP,
            RecoveryAction.RETRY,
            RecoveryAction.FALLBACK,
        }


# ── ErrorPolicy ─────────────────────────────────────────────


class TestErrorPolicy:
    def test_defaults(self):
        p = ErrorPolicy()
        assert p.retry == 2
        assert p.backoff == 1.0
        assert p.on_failure == RecoveryAction.ABORT
        assert p.fallback_model is None

    def test_classify_rate_limit(self):
        p = ErrorPolicy()
        assert p.classify("RateLimitError") == RecoveryAction.RETRY

    def test_classify_timeout(self):
        p = ErrorPolicy()
        assert p.classify("TimeoutError") == RecoveryAction.RETRY

    def test_classify_validation(self):
        p = ErrorPolicy()
        assert p.classify("ValidationError") == RecoveryAction.SKIP

    def test_classify_schema(self):
        p = ErrorPolicy()
        assert p.classify("SchemaError") == RecoveryAction.SKIP

    def test_classify_unknown_falls_back(self):
        p = ErrorPolicy(on_failure=RecoveryAction.ABORT)
        assert p.classify("RuntimeError") == RecoveryAction.ABORT

    def test_classify_custom_on_failure(self):
        p = ErrorPolicy(on_failure=RecoveryAction.SKIP)
        assert p.classify("RuntimeError") == RecoveryAction.SKIP


# ── Budget ──────────────────────────────────────────────────


class TestBudget:
    def test_from_string_dollar(self):
        b = Budget.from_string("$5.00")
        assert b.max_cost == 5.0
        assert b.max_tokens is None
        assert b.max_steps is None

    def test_from_string_plain_number(self):
        b = Budget.from_string("10.5")
        assert b.max_cost == 10.5

    def test_manual_fields(self):
        b = Budget(max_cost=1.0, max_tokens=1000, max_steps=50)
        assert b.max_cost == 1.0
        assert b.max_tokens == 1000
        assert b.max_steps == 50

    def test_defaults_are_none(self):
        b = Budget()
        assert b.max_cost is None
        assert b.max_tokens is None
        assert b.max_steps is None


# ── InterruptionProtection ──────────────────────────────────


class TestInterruptionProtection:
    def test_defaults(self):
        ip = InterruptionProtection()
        assert ip.enabled is False
        assert ip.heartbeat_interval == 30.0
        assert ip.heartbeat_timeout == 60.0
        assert ip.pre_step_checkpoint is True
        assert ip.post_step_checkpoint is True
        assert ip.context_overflow_strategy == "compress_and_resume"
        assert ip.max_resume_attempts == 3

    def test_custom(self):
        ip = InterruptionProtection(enabled=True, heartbeat_interval=10.0)
        assert ip.enabled is True
        assert ip.heartbeat_interval == 10.0


# ── StepInput ───────────────────────────────────────────────


class TestStepInput:
    def test_getattr(self):
        si = StepInput(_data={"x": 42, "y": "hello"})
        assert si.x == 42
        assert si.y == "hello"

    def test_getattr_missing(self):
        si = StepInput(_data={})
        with pytest.raises(AttributeError, match="has no attribute 'z'"):
            _ = si.z

    def test_get_method(self):
        si = StepInput(_data={"x": 42})
        assert si.get("x") == 42
        assert si.get("missing", "default") == "default"

    def test_to_dict(self):
        si = StepInput(_data={"x": 1})
        assert si.to_dict() == {"x": 1}

    def test_private_attr(self):
        si = StepInput()
        si._data = {"x": 1}  # direct set is fine for private
        assert si._data == {"x": 1}


# ── StepOutput ──────────────────────────────────────────────


class TestStepOutput:
    def test_default_empty(self):
        so = StepOutput()
        assert so.updates == {}

    def test_with_updates(self):
        so = StepOutput(updates={"x": 1, "y": 2})
        assert so.updates == {"x": 1, "y": 2}


# ── StepResult ──────────────────────────────────────────────


class TestStepResult:
    def test_success(self):
        sr = StepResult(step_name="s1", success=True, output="result")
        assert sr.success is True
        assert sr.output == "result"
        assert sr.error is None
        assert sr.tokens_used == 0
        assert sr.cost == 0.0

    def test_failure(self):
        sr = StepResult(step_name="s1", success=False, error="boom")
        assert sr.success is False
        assert sr.error == "boom"


# ── Step ────────────────────────────────────────────────────


class TestStep:
    def test_execute_with_input(self):
        s = Step(name="s1", input="hello")
        result = s.execute({})
        assert result.success is True
        assert result.output == "hello"
        assert result.step_name == "s1"

    def test_execute_with_prompt(self):
        s = Step(name="s1", prompt="Hello {name}", input=None)
        result = s.execute({"name": "World"})
        assert result.success is True
        assert result.output == "Hello World"

    def test_execute_with_engine_callback(self):
        def my_callback(step, ctx_data):
            return f"from_callback:{ctx_data.get('x', 0)}"

        s = Step(name="s1")
        s._engine_callback = my_callback
        result = s.execute({"x": 99})
        assert result.success is True
        assert result.output == "from_callback:99"

    def test_execute_error(self):
        def bad_callback(step, ctx_data):
            raise ValueError("test error")

        s = Step(name="s1")
        s._engine_callback = bad_callback
        result = s.execute({})
        assert result.success is False
        assert "test error" in result.error

    def test_execute_stores_result(self):
        s = Step(name="s1", input="data")
        s.execute({})
        assert s._result is not None
        assert s._result.success is True

    def test_duration_positive(self):
        s = Step(name="s1", input="data")
        result = s.execute({})
        assert result.duration_ms >= 0


# ── Parallel ────────────────────────────────────────────────


class TestParallel:
    def test_init(self):
        s1 = Step(name="s1", input="a")
        s2 = Step(name="s2", input="b")
        p = Parallel(s1, s2)
        assert len(p.steps) == 2
        assert p.steps[0].name == "s1"
        assert p.steps[1].name == "s2"

    def test_empty(self):
        p = Parallel()
        assert p.steps == []


# ── _get_source_hash ───────────────────────────────────────


class TestGetSourceHash:
    def test_hash_of_function(self):
        def foo():
            return 42

        h = _get_source_hash(foo)
        assert len(h) == 16
        assert h != "unknown"

    def test_hash_deterministic(self):
        def bar():
            return 1

        assert _get_source_hash(bar) == _get_source_hash(bar)

    def test_different_functions_different_hash(self):
        def a():
            return 1

        def b():
            return 2

        assert _get_source_hash(a) != _get_source_hash(b)


# ── CheckpointData ──────────────────────────────────────────


class TestCheckpointData:
    def test_creation(self):
        cp = CheckpointData(
            loop_name="test",
            loop_version="1.0.0",
            loop_source_hash="abc123",
            step_index=0,
            context_data={},
            completed_results={},
            executed_step_names=[],
        )
        assert cp.loop_name == "test"
        assert cp.loop_version == "1.0.0"
        assert cp.loop_source_hash == "abc123"
        assert cp.created_at != ""

    def test_auto_created_at(self):
        cp1 = CheckpointData(
            loop_name="a",
            loop_version="1.0.0",
            loop_source_hash="h",
            step_index=0,
            context_data={},
            completed_results={},
            executed_step_names=[],
        )
        cp2 = CheckpointData(
            loop_name="a",
            loop_version="1.0.0",
            loop_source_hash="h",
            step_index=0,
            context_data={},
            completed_results={},
            executed_step_names=[],
        )
        # Both should have timestamps
        assert cp1.created_at != ""
        assert cp2.created_at != ""


# ── LoopDef + Loop decorator ───────────────────────────────


class TestLoopDef:
    def test_loop_decorator(self):
        @Loop(name="test_loop", version="1.0.0")
        def my_loop(ctx):
            pass

        assert isinstance(my_loop, LoopDef)
        assert my_loop.name == "test_loop"
        assert my_loop.version == "1.0.0"
        assert my_loop.agent is None
        assert my_loop.budget is None

    def test_loop_with_budget_string(self):
        @Loop(name="budgeted", budget="$5.00")
        def my_loop(ctx):
            pass

        assert isinstance(my_loop, LoopDef)
        assert my_loop.budget is not None
        assert my_loop.budget.max_cost == 5.0

    def test_loop_with_budget_object(self):
        @Loop(name="budgeted2", budget=Budget(max_tokens=1000))
        def my_loop(ctx):
            pass

        assert my_loop.budget.max_tokens == 1000

    def test_loop_with_agent(self):
        @Loop(name="agent_loop", agent="opencode")
        def my_loop(ctx):
            pass

        assert my_loop.agent == "opencode"

    def test_loop_source_hash(self):
        @Loop(name="hash_loop")
        def my_loop(ctx):
            pass

        assert my_loop.source_hash != ""
        assert len(my_loop.source_hash) == 16

    def test_loop_def_attached_to_func(self):
        @Loop(name="attached")
        def my_loop(ctx):
            pass

        assert isinstance(my_loop, LoopDef)
        assert my_loop.name == "attached"

    def test_loop_with_interruption_protection(self):
        ip = InterruptionProtection(enabled=True)

        @Loop(name="protected", interruption_protection=ip)
        def my_loop(ctx):
            pass

        assert my_loop.interruption_protection is not None
        assert my_loop.interruption_protection.enabled is True
