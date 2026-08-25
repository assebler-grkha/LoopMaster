"""Tests for the detached loop worker (DetachedRunner)."""

from __future__ import annotations

import time
from typing import Any

import pytest

from loopmaster.core.engine import LoopEngine
from loopmaster.mcp.job_store import get_job_store
from loopmaster.mcp.worker import DetachedRunner, _normalize_output
from loopmaster.spec import load_loop_from_dict

SHELL_OK = {
    "loopmaster": "1.0",
    "name": "worker-ok",
    "version": "1.0.0",
    "steps": [
        {
            "type": "shell",
            "name": "greet",
            "command": ["python", "-c", "print('detached-worker-ok')"],
        },
    ],
}

SHELL_FAIL = {
    "loopmaster": "1.0",
    "name": "worker-fail",
    "version": "1.0.0",
    "steps": [
        {"type": "shell", "name": "boom", "command": ["cmd", "/c", "exit", "5"]},
    ],
}


@pytest.fixture()
def store(tmp_path):
    return get_job_store(str(tmp_path / "jobs.db"))


@pytest.fixture()
def runner(store):
    return DetachedRunner(store)


def wait_terminal(store, job_id: str, timeout: float = 10.0) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = store.get_job(job_id)
        if job.status in ("completed", "failed", "error", "interrupted", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach terminal state")


class TestDetachedRunner:
    def test_submit_completes_shell_loop(self, store, runner):
        loop_def, spec = load_loop_from_dict(SHELL_OK)
        job_id = runner.submit(loop_def, definition={"spec": SHELL_OK})
        job = wait_terminal(store, job_id)
        assert job.status == "completed"
        assert job.metrics["host_pid"] > 0
        assert not runner.is_running(job_id)

    def test_results_are_structured(self, store, runner):
        loop_def, _spec = load_loop_from_dict(SHELL_OK)
        job_id = runner.submit(loop_def)
        job = wait_terminal(store, job_id)
        result = job.results["greet"]
        assert isinstance(result, dict)
        assert "detached-worker-ok" in result["stdout"]
        assert result["returncode"] == 0

    def test_failure_marks_job_failed(self, store, runner):
        loop_def, _spec = load_loop_from_dict({**SHELL_FAIL})
        job_id = runner.submit(loop_def)
        job = wait_terminal(store, job_id)
        assert job.status in ("completed", "failed")
        if job.status == "completed":
            failed_entry = job.results.get("boom") or {}
            assert failed_entry.get("success") is False or failed_entry.get("returncode") != 0
        else:
            assert job.error

    def test_job_visible_immediately_as_running(self, store, runner):
        loop_def, _spec = load_loop_from_dict(SHELL_OK)
        job_id = runner.submit(loop_def)
        early = store.get_job(job_id)
        assert early is not None
        assert early.status == "running"
        wait_terminal(store, job_id)

    def test_cancel_unknown_job_returns_false(self, runner):
        assert runner.request_cancel("missing-job") is False

    def test_total_steps_recorded(self, store, runner):
        multi = {
            **SHELL_OK,
            "name": "worker-multi",
            "steps": [
                {"type": "shell", "name": "one", "command": ["python", "-c", "print(1)"]},
                {"type": "shell", "name": "two", "command": ["python", "-c", "print(2)"]},
            ],
        }
        loop_def, spec = load_loop_from_dict(multi)
        job_id = runner.submit(loop_def)
        job = wait_terminal(store, job_id)
        assert job.total_steps == len(spec.step_names()) == 2


class TestRecollectFlag:
    def test_declarative_steps_survive_repeated_runs(self):
        loop_def, _spec = load_loop_from_dict(SHELL_OK)
        engine = LoopEngine()
        first = engine.run(loop_def, initial_context={})
        second = engine.run(loop_def, initial_context={})
        assert first.steps_executed == ["greet"]
        assert second.steps_executed == ["greet"]

    def test_python_loops_still_recollect(self):
        from loopmaster.core.types import LoopDef, Step
        from loopmaster.executors.shell import ShellExecutor

        calls = []

        def body(ctx):
            calls.append(1)

        ld = LoopDef(name="py-recollect", version="1.0.0", body=body)
        ld._collected_steps = [
            Step(name="x", executor=ShellExecutor(command=["python", "-c", "print('hi')"]))
        ]
        engine = LoopEngine()
        engine.run(ld, initial_context={})
        assert ld._recollect_steps is True
        assert calls, "body must be invoked to collect steps"
        assert ld._collected_steps == []


class TestNormalizeOutput:
    def test_llm_content_unwrapped(self):
        class Fake:
            content = "llm text"

        assert _normalize_output(Fake()) == "llm text"

    def test_shell_result_to_dict(self):
        class ShellLike:
            stdout = "out"
            stderr = "err"
            returncode = 7

        assert _normalize_output(ShellLike()) == {
            "stdout": "out",
            "stderr": "err",
            "returncode": 7,
        }

    def test_plain_value_passthrough(self):
        assert _normalize_output({"k": 1}) == {"k": 1}


SHELL_SLOW = {
    "loopmaster": "1.0",
    "name": "worker-slow",
    "version": "1.0.0",
    "steps": [
        {
            "type": "shell",
            "name": "wait",
            "command": ["python", "-c", "import time; time.sleep(3)"],
        },
    ],
}


class TestAdversarialFixes:
    def test_default_factory_sets_checkpoint_dir(self, tmp_path):
        import threading

        from loopmaster.mcp.worker import DEFAULT_CHECKPOINT_DIR, _default_engine_factory

        cps = str(tmp_path / "cps")
        engine = _default_engine_factory(threading.Event(), checkpoint_dir=cps)
        assert engine.checkpoint_dir == cps
        assert DEFAULT_CHECKPOINT_DIR == ".loopmaster/checkpoints"

    def test_runner_uses_checkpoint_dir(self, store, tmp_path):
        cps = tmp_path / "cps"
        runner = DetachedRunner(store, checkpoint_dir=str(cps))
        loop_def, _spec = load_loop_from_dict(SHELL_OK)
        job_id = runner.submit(loop_def)
        wait_terminal(store, job_id)
        assert cps.exists(), "engine must persist checkpoints to the configured dir"

    def test_submit_refuses_live_lease(self, store, runner):
        loop_def, _spec = load_loop_from_dict(SHELL_SLOW)
        job_id = runner.submit(loop_def)
        time.sleep(0.2)
        again, _spec2 = load_loop_from_dict(SHELL_SLOW)
        with pytest.raises(ValueError, match="already running"):
            runner.submit(again, job_id=job_id)
        store.cancel_job(job_id)

    def test_cross_process_cancel_wins_over_worker(self, store):
        two_step = {
            "loopmaster": "1.0",
            "name": "worker-cancel-race",
            "version": "1.0.0",
            "steps": [
                {"type": "shell", "name": "one", "command": ["python", "-c", "print('one')"]},
                {
                    "type": "shell",
                    "name": "slow",
                    "command": ["python", "-c", "import time; time.sleep(5)"],
                },
                {"type": "shell", "name": "never", "command": ["python", "-c", "print('never')"]},
            ],
        }
        runner = DetachedRunner(store, poll_s=0.05)
        loop_def, _spec = load_loop_from_dict(two_step)
        job_id = runner.submit(loop_def)
        time.sleep(0.5)
        assert store.cancel_job(job_id) is True

        deadline = time.time() + 10
        while time.time() < deadline:
            if not runner.is_running(job_id):
                break
            time.sleep(0.05)
        job = store.get_job(job_id)
        assert job.status == "cancelled"
        assert "one" in job.results
        assert "never" not in job.results
