"""Round-2 adversarial fixes: C1 Parallel, H1/H2 loader, M1-M6 worker/store."""

from __future__ import annotations

import os
import time

from loopmaster.core.engine import LoopEngine
from loopmaster.mcp.job_store import TERMINAL_STATUSES, JobData, get_job_store
from loopmaster.mcp.worker import DetachedRunner
from loopmaster.spec import load_loop_from_dict, validate_loop_spec


def _store(tmp_path):
    return get_job_store(str(tmp_path / "jobs.db"))


def _runner(store, **kwargs):
    kwargs.setdefault("poll_s", 0.05)
    kwargs.setdefault("heartbeat_s", 60.0)
    return DetachedRunner(store, **kwargs)


def _wait_terminal(store, job_id, timeout=20.0) -> JobData:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = store.get_job(job_id)
        if job is None or job.status in TERMINAL_STATUSES or job.status == "error":
            assert job is not None, f"job {job_id} disappeared"
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach terminal state")


def _spec(steps, ctx=None):
    spec: dict = {"loopmaster": "1.0", "name": "r2", "version": "1.0.0", "steps": steps}
    if ctx:
        spec["context"] = ctx
    return spec


class TestParallelExecution:
    """C1: Parallel blocks must actually execute their children."""

    def test_parallel_children_execute(self):
        data = _spec(
            [
                {
                    "type": "parallel",
                    "name": "fan",
                    "steps": [
                        {"type": "llm", "name": "a1", "prompt": "A"},
                        {"type": "llm", "name": "b1", "prompt": "B"},
                    ],
                },
                {"type": "llm", "name": "after", "prompt": "{a1} done"},
            ]
        )
        loop_def, _spec_obj = load_loop_from_dict(data)
        result = LoopEngine().run(loop_def, initial_context={})
        names = list(result.results.keys())
        assert names == ["a1", "b1", "after"]
        assert result.success

    def test_parallel_sibling_refs_rejected(self):
        data = _spec(
            [
                {
                    "type": "parallel",
                    "name": "fan",
                    "steps": [
                        {"type": "shell", "name": "s1", "command": "echo hi"},
                        {"type": "llm", "name": "uses", "prompt": "{s1.stdout}"},
                    ],
                }
            ]
        )
        errors = validate_loop_spec(data)
        assert any("unknown placeholder" in e for e in errors)


class TestExecutorFieldValidation:
    """H1: executor fields are template-resolved at runtime -> validated at load."""

    def test_shell_command_unknown_ref(self):
        data = _spec([{"type": "shell", "name": "sh", "command": "echo {goal_missing}"}])
        assert any("unknown placeholder" in e for e in validate_loop_spec(data))

    def test_shell_list_item_unknown_ref(self):
        data = _spec([{"type": "shell", "name": "sh", "command": ["echo", "{nope}"]}])
        assert any("unknown placeholder" in e for e in validate_loop_spec(data))

    def test_http_url_unknown_ref(self):
        data = _spec(
            [{"type": "http", "name": "h", "url": "https://api.test/{missing}/x", "method": "GET"}]
        )
        assert any("unknown placeholder" in e for e in validate_loop_spec(data))

    def test_mcp_arguments_unknown_ref(self):
        data = _spec(
            [
                {
                    "type": "mcp",
                    "name": "m",
                    "server_command": ["python"],
                    "tool_name": "t",
                    "arguments": {"path": "{absent}"},
                }
            ]
        )
        assert any("unknown placeholder" in e for e in validate_loop_spec(data))

    def test_known_context_ref_in_command_ok(self):
        data = _spec(
            [{"type": "shell", "name": "sh", "command": "echo {topic}"}], ctx={"topic": "x"}
        )
        assert validate_loop_spec(data) == []


class TestBlindSpotScan:
    """H2: refs outside the strict regex shape are flagged instead of ignored."""

    def test_hyphen_ref_flagged(self):
        data = _spec([{"type": "llm", "name": "j", "prompt": "use {my-step} now"}])
        errors = validate_loop_spec(data)
        assert any("looks like a reference" in e for e in errors)

    def test_digit_lead_ref_flagged(self):
        data = _spec([{"type": "llm", "name": "j", "prompt": "value {9var} here"}])
        assert any("looks like a reference" in e for e in validate_loop_spec(data))

    def test_json_example_not_flagged(self):
        data = _spec([{"type": "llm", "name": "j", "prompt": 'Return {"key": "value"} please'}])
        assert validate_loop_spec(data) == []


class TestConditionProbe:
    """M1/M2: quoted placeholders and negative literals parse cleanly."""

    def test_quoted_placeholder_condition_ok(self):
        data = _spec(
            [
                {
                    "type": "conditional",
                    "name": "route",
                    "condition": "'{answer}' == 'yes'",
                    "then": [{"type": "llm", "name": "t", "prompt": "T"}],
                },
                {"type": "llm", "name": "end", "prompt": "E"},
            ],
            ctx={"answer": "yes"},
        )
        assert validate_loop_spec(data) == []

    def test_negative_literal_condition_ok(self):
        data = _spec(
            [
                {
                    "type": "conditional",
                    "name": "route",
                    "condition": "{temp} < -10",
                    "then": [{"type": "llm", "name": "cold", "prompt": "C"}],
                },
                {"type": "llm", "name": "end", "prompt": "E"},
            ],
            ctx={"temp": 5},
        )
        assert validate_loop_spec(data) == []

    def test_runtime_negative_condition_evaluates(self):
        from loopmaster.core.condition import evaluate_condition

        assert evaluate_condition("{temp} < -10", {"temp": -42}) is True
        assert evaluate_condition("{temp} < -10", {"temp": 5}) is False


class TestResubmitAfterTerminal:
    """M4: re-submitting an explicit job_id after a terminal state must work."""

    def test_resubmit_same_job_id(self, tmp_path):
        store = _store(tmp_path)
        runner = _runner(store)
        data = _spec([{"type": "shell", "name": "one", "command": ["python", "-c", "print('ok')"]}])
        loop_def, _spec_obj = load_loop_from_dict(data)
        first_id = runner.submit(loop_def, job_id="retry-me")
        _wait_terminal(store, first_id)
        second_id = runner.submit(loop_def, job_id="retry-me")
        final = _wait_terminal(store, second_id)
        assert final.status == "completed"
        assert final.job_id == "retry-me"


class TestFactoryFailure:
    """M3: engine factory crash still finalizes the job row."""

    def test_factory_crash_marks_failed(self, tmp_path):
        store = _store(tmp_path)

        def bad_factory(_cancel_event):
            raise RuntimeError("boom")

        runner = DetachedRunner(store, engine_factory=bad_factory)
        loop_def, _spec_obj = load_loop_from_dict(
            _spec([{"type": "llm", "name": "a", "prompt": "hi"}])
        )
        job_id = runner.submit(loop_def)
        job = _wait_terminal(store, job_id)
        assert job.status == "failed"
        assert not runner.is_running(job_id)


class TestCreateJobUpsert:
    """M4/M6: create_job accepts metrics and resets stale rows."""

    def test_metrics_persisted_at_create(self, tmp_path):
        store = _store(tmp_path)
        store.create_job(
            "j1",
            "loop",
            {},
            status="running",
            total_steps=1,
            metrics={"host_pid": os.getpid()},
        )
        job1 = store.get_job("j1")
        assert job1 is not None and job1.metrics is not None
        assert job1.metrics["host_pid"] == os.getpid()

    def test_upsert_resets_stale_row(self, tmp_path):
        store = _store(tmp_path)
        store.create_job("j2", "loop", {}, status="running", total_steps=1)
        store.update_job("j2", results={"old": 1}, error="stale", completed=True)
        fresh = store.create_job("j2", "loop", {}, status="running", total_steps=2)
        assert fresh.status == "running"
        assert fresh.results == {}
        stored = store.get_job("j2")
        assert stored is not None
        assert stored.completed_at is None
        assert stored.error is None
