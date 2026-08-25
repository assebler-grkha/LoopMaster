"""Phase 4 HITL: messages table, HumanInputExecutor, detached waiting flow."""

from __future__ import annotations

import hashlib
import json
import time
from types import SimpleNamespace

import pytest

import loopmaster.mcp.runtime as rt
from loopmaster.core.context import Context
from loopmaster.core.state import apply_step_result
from loopmaster.core.types import Step, StepResult
from loopmaster.executors.human_input import HumanInputExecutor
from loopmaster.mcp.job_store import (
    JobStore,
    MessageData,
    get_job_store,
    parse_duration,
)
from loopmaster.mcp.tools_hitl import loop_respond
from loopmaster.mcp.worker import DetachedRunner
from loopmaster.spec.loader import load_loop_from_dict


@pytest.fixture()
def store(tmp_path):
    return JobStore(db_path=tmp_path / "jobs.db")


class TestParseDuration:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [("30s", 30.0), ("5m", 300.0), ("2h", 7200.0), ("1d", 86400.0), ("1h30m", 5400.0)],
    )
    def test_valid(self, text, expected):
        assert parse_duration(text) == expected

    @pytest.mark.parametrize("text", ["", "abc", "5x", "10m10m", "1h x"])
    def test_invalid(self, text):
        with pytest.raises(ValueError):
            parse_duration(text)


class TestQuestionLifecycle:
    def test_create_and_get(self, store):
        msg = store.create_question("j1", "loop:demo#confirm", text="Proceed?")
        assert isinstance(msg, MessageData)
        assert msg.status == "pending"
        assert msg.payload["text"] == "Proceed?"

    def test_create_is_idempotent(self, store):
        first = store.create_question("j1", "loop:demo#ask", text="Q?", options=["a"])
        second = store.create_question("j1", "loop:demo#ask", text="Q?", options=["a"])
        assert first.msg_id == second.msg_id

    def test_msg_id_deterministic_per_step(self, store):
        a = store.create_question("j1", "loop:d#step-a", text="A")
        b = store.create_question("j1", "loop:d#step-b", text="B")
        assert a.msg_id != b.msg_id

    def test_answer_ok(self, store):
        msg = store.create_question("j1", "loop:d#ask", text="Q?")
        answered = store.answer_question(msg.msg_id, "yes")
        assert answered.status == "answered"
        assert answered.answered == {"answer": "yes", "by": "agent"}

    def test_double_answer_raises(self, store):
        msg = store.create_question("j1", "loop:d#ask", text="Q?")
        store.answer_question(msg.msg_id, "yes")
        with pytest.raises(ValueError, match="already_answered"):
            store.answer_question(msg.msg_id, "no")

    def test_unknown_message(self, store):
        with pytest.raises(KeyError):
            store.answer_question("deadbeef", "x")

    def test_sweep_expired(self, store):
        msg = store.create_question("j1", "loop:d#ask", text="Q?", timeout_s=0.05)
        time.sleep(0.12)
        swept = store.sweep_expired_questions()
        assert swept == 1
        final = store.get_message(msg.msg_id)
        assert final is not None and final.status == "expired"
        with pytest.raises(ValueError, match="already_expired"):
            store.answer_question(msg.msg_id, "late")

    def test_list_questions_filters_status(self, store):
        store.create_question("j1", "loop:d#one", text="1")
        two = store.create_question("j2", "loop:d#two", text="2")
        store.answer_question(two.msg_id, "ok")
        pending = store.list_questions()
        assert [m.job_id for m in pending] == ["j1"]
        assert len(store.list_questions()) == 1
        answered = store.list_questions(job_id="j2", status="answered")
        assert len(answered) == 1

    def test_cancel_job_cancels_pending_questions(self, store):
        store.create_job(job_id="jc", loop_name="l", definition={})
        store.create_question("jc", "loop:d#ask", text="Q?")
        assert store.cancel_job("jc") is True
        msg = store.list_questions(status="cancelled")[0]
        assert msg.status == "cancelled"

    def test_poison_text_rejected(self, store):
        with pytest.raises(ValueError, match="placeholder"):
            store.create_question("j1", "loop:d#ask", text="Use {goal} please")

    def test_poison_default_answer_rejected(self, store):
        with pytest.raises(ValueError, match="placeholder"):
            store.create_question("j1", "loop:d#ask", text="ok", default_answer="{goal}")


class TestHumanInputExecutor:
    def _ctx(self, tmp_path, job_id="jx"):
        return {"__job_id__": job_id, "__loop_name__": "demo"}

    def test_answer_wakes_executor(self, store):
        store.create_job(job_id="jx", loop_name="demo", definition={})
        ex = HumanInputExecutor(
            step_name="confirm", question="Proceed?", db_path=str(store.db_path)
        )
        import threading

        timer = threading.Timer(
            0.3,
            lambda: store.answer_question(
                __import__("hashlib").sha256(b"jx|loop:demo#confirm").hexdigest(), "yes"
            ),
        )
        timer.start()
        result = ex.execute(self._ctx(store.db_path))
        timer.join()
        assert result.success is True
        assert result.resolved == "answered"
        assert result.answer == "yes"
        job = store.get_job("jx")
        assert job.status == "in_progress"

    def test_timeout_default_answer_auto(self, store):
        ex = HumanInputExecutor(
            step_name="confirm",
            question="Proceed?",
            timeout="1s",
            default_answer="auto-no",
            poll_s=0.05,
            db_path=str(store.db_path),
        )
        result = ex.execute(self._ctx(store.db_path))
        assert result.resolved == "auto"
        assert result.answer == "auto-no"
        msg = store.get_message(result.msg_id)
        assert msg is not None and msg.status == "answered"

    def test_timeout_skip_returns_failure(self, store):
        ex = HumanInputExecutor(
            step_name="ask",
            question="Q?",
            timeout="1s",
            on_timeout="skip",
            poll_s=0.05,
            db_path=str(store.db_path),
        )
        result = ex.execute({})
        assert result.success is False
        assert "timeout" in str(result.error)

    def test_poison_question_fails_step(self, store):
        ex = HumanInputExecutor(step_name="bad", question="Use {goal}", db_path=str(store.db_path))
        result = ex.execute({})
        assert result.success is False
        assert "placeholder" in str(result.error)

    def test_bad_on_timeout_rejected_at_construction(self):
        with pytest.raises(ValueError):
            HumanInputExecutor(step_name="x", question="q", on_timeout="yolo")


def _hitl_spec(**overrides):
    spec = {
        "loopmaster": "1.0",
        "name": "hitl-demo",
        "version": "1.0.0",
        "context": {"topic": "deploy"},
        "steps": [
            {
                "type": "human",
                "name": "confirm",
                "question": "Deploy {topic}? yes/no",
                "options": ["yes", "no"],
                **overrides,
            }
        ],
    }
    return spec


class TestDetachedWaitingFlow:
    @pytest.fixture()
    def runner_env(self, tmp_path, monkeypatch):
        monkeypatch.setattr("loopmaster.mcp.job_store._global_store", None)
        monkeypatch.setenv("LOOPMASTER_JOBS_DB", str(tmp_path / "jobs.db"))
        runner = DetachedRunner(get_job_store(), poll_s=0.05, heartbeat_s=60)
        yield runner, get_job_store()

    def _wait_terminal(self, store, job_id, timeout=15.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            job = store.get_job(job_id)
            if job and job.status in ("completed", "failed", "cancelled", "interrupted"):
                return job
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} never reached terminal state")

    def test_wait_then_answer_completes(self, runner_env):
        runner, store = runner_env
        loop_def, spec = load_loop_from_dict(_hitl_spec(timeout="1h", on_timeout="skip"))
        job_id = runner.submit(loop_def, initial_context=dict(spec.initial_context))
        deadline = time.time() + 10
        while time.time() < deadline:
            if store.get_job(job_id).status == "waiting_input":
                break
            time.sleep(0.05)
        assert store.get_job(job_id).status == "waiting_input"
        questions = store.list_questions(job_id=job_id)
        assert len(questions) == 1
        assert "deploy" in questions[0].payload["text"].lower()
        store.answer_question(questions[0].msg_id, "yes")
        job = self._wait_terminal(store, job_id)
        assert job.status == "completed"
        confirm = job.results.get("confirm")
        assert isinstance(confirm, dict) and confirm.get("answer") == "yes"

    def test_timeout_default_answers_automatically(self, runner_env):
        runner, store = runner_env
        loop_def, _spec = load_loop_from_dict(
            _hitl_spec(timeout="1s", default_answer="no", on_timeout="default_answer")
        )
        job_id = runner.submit(loop_def, initial_context=dict(_spec.initial_context))
        job = self._wait_terminal(store, job_id)
        assert job.status == "completed"
        confirm = job.results.get("confirm")
        assert isinstance(confirm, dict)
        payload = confirm.get("output") if isinstance(confirm.get("output"), dict) else confirm
        assert payload.get("resolved") == "auto" or payload.get("answer") == "no"

    def test_cancel_while_waiting_ends_cancelled(self, runner_env):
        runner, store = runner_env
        loop_def, spec = load_loop_from_dict(_hitl_spec(timeout="1h", on_timeout="skip"))
        job_id = runner.submit(loop_def, initial_context=dict(spec.initial_context))
        deadline = time.time() + 10
        while time.time() < deadline:
            if store.get_job(job_id).status == "waiting_input":
                break
            time.sleep(0.05)
        store.cancel_job(job_id)
        job = self._wait_terminal(store, job_id)
        assert job.status == "cancelled"
        assert store.list_questions(status="cancelled")

    def test_crash_between_pause_and_answer_is_safe(self, runner_env):
        runner, store = runner_env
        loop_def, spec = load_loop_from_dict(_hitl_spec(timeout="1h", on_timeout="skip"))
        job_id = runner.submit(loop_def, initial_context=dict(spec.initial_context))
        deadline = time.time() + 10
        while time.time() < deadline:
            if store.get_job(job_id).status == "waiting_input":
                break
            time.sleep(0.05)
        questions = store.list_questions(job_id=job_id)
        assert len(questions) == 1

        with store._lock:
            cur = store.conn.cursor()
            cur.execute(
                "UPDATE jobs SET metrics=? WHERE job_id=?",
                (json.dumps({"host_pid": 999999999}), job_id),
            )
            store.conn.commit()
            cur.close()

        crashed = JobStore(db_path=store.db_path)
        crashed.mark_interrupted_jobs_on_startup()
        job = self._wait_terminal(crashed, job_id)
        assert job.status == "interrupted"

        orphaned = crashed.list_questions(job_id=job_id)
        assert len(orphaned) == 1 and orphaned[0].status == "pending"

        sha = hashlib.sha256(f"{job_id}|loop:hitl-demo#confirm".encode()).hexdigest()
        assert orphaned[0].msg_id == sha

        answered = crashed.answer_question(orphaned[0].msg_id, "yes")
        assert answered.status == "answered"

        resubmitted = crashed.create_job(
            job_id=job_id,
            loop_name="hitl-demo",
            definition={"step_count": 1},
            status="running",
        )
        assert resubmitted.status == "running"

    def test_loader_builds_human_executor(self):
        loop_def, spec = load_loop_from_dict(_hitl_spec(default_answer="no"))
        steps = list(loop_def._collected_steps)
        executor = steps[0].executor
        assert type(executor).__name__ == "HumanInputExecutor"
        assert executor.step_name == "confirm"
        assert executor.options == ["yes", "no"]


class TestSkippedStepContext:
    def test_failed_step_yields_null_in_context(self):
        ctx = Context({})
        step = Step(name="confirm", prompt="p")
        res = StepResult(step_name="confirm", success=False, error="input timeout")
        apply_step_result(SimpleNamespace(name="t"), step, res, ctx, [], {})
        assert ctx._data["confirm"] is None

    def test_successful_step_still_merges(self):
        ctx = Context({})
        step = Step(name="ok", prompt="p")
        res = StepResult(step_name="ok", success=True, output={"v": 1})
        apply_step_result(SimpleNamespace(name="t"), step, res, ctx, [], {})
        assert ctx._data["v"] == 1


class TestRespondJobGuard:
    def test_respond_rejects_wrong_job_id(self, store, monkeypatch):
        monkeypatch.setattr(rt, "store", store)
        msg = store.create_question("j1", "loop:d#ask", text="Q?")
        out = loop_respond(job_id="other-job", msg_id=msg.msg_id, answer="x")
        assert "belongs to job 'j1'" in out
        assert store.get_message(msg.msg_id).status == "pending"

    def test_respond_accepts_matching_job_id(self, store, monkeypatch):
        monkeypatch.setattr(rt, "store", store)
        msg = store.create_question("j1", "loop:d#ask", text="Q?")
        out = loop_respond(
            job_id="j1",
            msg_id=msg.msg_id,
            answer="yes",
            nonce=msg.payload.get("nonce", ""),
        )
        assert '"responded": true' in out
        assert store.get_message(msg.msg_id).status == "answered"
