"""Tests for YAML export functionality."""

from __future__ import annotations

import yaml

from loopmaster.core.types import (
    Budget,
    ErrorPolicy,
    InterruptionProtection,
    LoopDef,
    Parallel,
    RecoveryAction,
    Step,
)
from loopmaster.core.yaml_export import export_loop


def _dummy_body(ctx):
    return ctx


def test_export_basic_loop():
    loop_def = LoopDef(name="test_loop", version="0.1.0", body=_dummy_body)
    result = export_loop(loop_def)
    data = yaml.safe_load(result)
    assert data["name"] == "test_loop"
    assert data["version"] == "0.1.0"


def test_export_loop_with_agent():
    loop_def = LoopDef(name="agent_loop", version="1.0.0", body=_dummy_body, agent="opencode")
    result = export_loop(loop_def)
    data = yaml.safe_load(result)
    assert data["agent"] == "opencode"


def test_export_loop_with_budget():
    budget = Budget(max_cost=5.0, max_tokens=10000)
    loop_def = LoopDef(name="budgeted", version="0.1.0", body=_dummy_body, budget=budget)
    result = export_loop(loop_def)
    data = yaml.safe_load(result)
    assert data["budget"]["max_cost"] == 5.0
    assert data["budget"]["max_tokens"] == 10000


def test_export_loop_with_interruption_protection():
    ip = InterruptionProtection(enabled=True, heartbeat_interval=15.0)
    loop_def = LoopDef(
        name="protected",
        version="0.1.0",
        body=_dummy_body,
        interruption_protection=ip,
    )
    result = export_loop(loop_def)
    data = yaml.safe_load(result)
    assert data["interruption_protection"]["enabled"] is True
    assert data["interruption_protection"]["heartbeat_interval"] == 15.0


def test_export_step_basic():
    step = Step(name="greet", model="gpt-4", prompt="Hello")
    d = step.to_dict()
    assert d["name"] == "greet"
    assert d["model"] == "gpt-4"
    assert d["prompt"] == "Hello"


def test_export_step_with_retry():
    step = Step(name="retry_step", tool="search", retry=3)
    d = step.to_dict()
    assert d["retry"] == 3
    assert d["tool"] == "search"


def test_export_step_defaults_omitted():
    step = Step(name="minimal")
    d = step.to_dict()
    assert d == {"name": "minimal"}


def test_export_step_with_on_error():
    policy = ErrorPolicy(retry=1, backoff=2.0, on_failure=RecoveryAction.SKIP)
    step = Step(name="safe_step", on_error=policy)
    d = step.to_dict()
    assert d["on_error"]["retry"] == 1
    assert d["on_error"]["backoff"] == 2.0
    assert d["on_error"]["on_failure"] == "skip"


def test_export_parallel():
    s1 = Step(name="a", model="gpt-4")
    s2 = Step(name="b", model="gpt-4")
    p = Parallel(s1, s2)
    d = p.to_dict()
    assert "parallel" in d
    assert len(d["parallel"]) == 2
    assert d["parallel"][0]["name"] == "a"
    assert d["parallel"][1]["name"] == "b"


def test_export_interruption_protection_defaults_omitted():
    ip = InterruptionProtection()
    d = ip.to_dict()
    assert d == {}


def test_export_interruption_protection_all_set():
    ip = InterruptionProtection(
        enabled=True,
        heartbeat_interval=10.0,
        heartbeat_timeout=30.0,
        pre_step_checkpoint=False,
        post_step_checkpoint=False,
        context_overflow_strategy="restart",
        max_resume_attempts=1,
    )
    d = ip.to_dict()
    assert d["enabled"] is True
    assert d["heartbeat_interval"] == 10.0
    assert d["heartbeat_timeout"] == 30.0
    assert d["pre_step_checkpoint"] is False
    assert d["post_step_checkpoint"] is False
    assert d["context_overflow_strategy"] == "restart"
    assert d["max_resume_attempts"] == 1


def test_export_budget_empty():
    b = Budget()
    d = b.to_dict()
    assert d == {}


def test_export_loop_has_source_hash():
    loop_def = LoopDef(name="hash_test", version="0.1.0", body=_dummy_body)
    result = export_loop(loop_def)
    data = yaml.safe_load(result)
    assert "source_hash" in data
    assert len(data["source_hash"]) == 16


def test_export_yaml_valid():
    loop_def = LoopDef(name="valid_yaml", version="0.1.0", body=_dummy_body)
    result = export_loop(loop_def)
    parsed = yaml.safe_load(result)
    assert isinstance(parsed, dict)
