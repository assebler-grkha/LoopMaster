"""Tests for agent-mode execution (M4), stdout streaming limit (L2), and loader/compiler NITs."""

import json
import time

import pytest

import loopmaster.mcp.runtime as rt
from loopmaster.mcp.job_store import JobStore
from loopmaster.mcp.tools_loops import loop_status
from loopmaster.mcp.tools_run import loop_record, loop_run
from loopmaster.spec.compiler import compile_loop_spec
from loopmaster.spec.loader import load_loop_from_dict, validate_loop_spec

AGENT_SPEC = {
    "loopmaster": "1.0",
    "name": "agent-demo",
    "version": "1.0.0",
    "execution": "agent",
    "steps": [{"type": "llm", "name": "think", "prompt": "Do the thing."}],
}

COND_SPEC = {
    "loopmaster": "1.0",
    "name": "cond-agent",
    "version": "1.0.0",
    "execution": "agent",
    "context": {"flag": True},
    "steps": [
        {
            "type": "conditional",
            "name": "route",
            "condition": "{flag}",
            "then": [{"type": "llm", "name": "yes-step", "prompt": "yes"}],
            "else": [{"type": "llm", "name": "no-step", "prompt": "no"}],
        }
    ],
}


def _bind(tmp_path):
    store = JobStore(db_path=tmp_path / "jobs.db")
    old_store, old_runner = rt.store, rt.runner
    rt.store = store
    rt.runner = __import__("loopmaster.mcp.worker", fromlist=["DetachedRunner"]).DetachedRunner(
        store
    )
    return store, old_store, old_runner


def _start_agent_job(store, spec=AGENT_SPEC, context=None):
    kwargs = {"spec_json": json.dumps(spec), "mode": "agent"}
    if context is not None:
        kwargs["context"] = json.dumps(context)
    return json.loads(loop_run(**kwargs))


class TestAgentMode:
    def test_agent_job_is_ready_without_worker(self, tmp_path):
        store, old_store, old_runner = _bind(tmp_path)
        try:
            out = _start_agent_job(store)
            assert out["status"] == "ready"
            job = store.get_job(out["job_id"])
            assert job is not None
            assert job.status == "ready"
            assert job.metrics.get("execution") == "agent"
        finally:
            rt.store, rt.runner = old_store, old_runner

    def test_loop_record_progresses_to_completed(self, tmp_path):
        store, old_store, old_runner = _bind(tmp_path)
        try:
            out = _start_agent_job(store)
            job_id = out["job_id"]
            rec = json.loads(loop_record(job_id=job_id, step_name="think", output='{"done":true}'))
            assert rec["recorded"] is True
            job = store.get_job(job_id)
            assert job.status == "completed"
        finally:
            rt.store, rt.runner = old_store, old_runner

    def test_record_unknown_job_404(self, tmp_path):
        _bind(tmp_path)
        try:
            out = json.loads(loop_record(job_id="nope", step_name="x"))
            assert "error" in out
        finally:
            pass

    def test_context_is_persisted_and_echoed(self, tmp_path):
        store, old_store, old_runner = _bind(tmp_path)
        try:
            out = _start_agent_job(store, context={"goal": "ship"})
            assert out["context"] == {"goal": "ship"}
            job = store.get_job(out["job_id"])
            assert job.definition.get("initial_context") == {"goal": "ship"}
        finally:
            rt.store, rt.runner = old_store, old_runner

    def test_conditional_requires_finalize(self, tmp_path):
        store, old_store, old_runner = _bind(tmp_path)
        try:
            out = _start_agent_job(store, COND_SPEC)
            job_id = out["job_id"]
            rec = json.loads(loop_record(job_id=job_id, step_name="yes-step"))
            assert rec["recorded"] is True
            assert store.get_job(job_id).status == "in_progress"
            fin = json.loads(loop_record(job_id=job_id, step_name="yes-step", finalize=True))
            assert fin["recorded"] is True
            assert store.get_job(job_id).status == "completed"
        finally:
            rt.store, rt.runner = old_store, old_runner

    def test_record_on_terminal_job_rejected(self, tmp_path):
        store, old_store, old_runner = _bind(tmp_path)
        try:
            out = _start_agent_job(store)
            loop_record(job_id=out["job_id"], step_name="think")
            rec = json.loads(loop_record(job_id=out["job_id"], step_name="think"))
            assert "already terminal" in rec.get("error", "")
        finally:
            rt.store, rt.runner = old_store, old_runner

    def test_unknown_step_rejected_with_plan(self, tmp_path):
        store, old_store, old_runner = _bind(tmp_path)
        try:
            out = _start_agent_job(store)
            rec = json.loads(loop_record(job_id=out["job_id"], step_name="fabricated"))
            assert "Unknown step 'fabricated'" in rec.get("error", "")
            assert "think" in rec["error"]
        finally:
            rt.store, rt.runner = old_store, old_runner

    def test_agent_job_not_marked_stale(self, tmp_path):
        store, old_store, old_runner = _bind(tmp_path)
        try:
            out = _start_agent_job(store)
            job_id = out["job_id"]
            with store._lock:  # noqa: SLF001
                cur = store.conn.cursor()
                cur.execute(
                    "UPDATE jobs SET updated_at = ? WHERE job_id = ?",
                    (time.time() - 10_000, job_id),
                )
                store.conn.commit()
                cur.close()
            status = json.loads(loop_status(job_id))
            assert status.get("failed") is not True
            assert store.get_job(job_id).status == "ready"
        finally:
            rt.store, rt.runner = old_store, old_runner

    def test_orphaned_ready_job_swept_only_with_dead_pid(self, tmp_path):
        store, old_store, old_runner = _bind(tmp_path)
        try:
            out = _start_agent_job(store)
            job_id = out["job_id"]
            with store._lock:  # noqa: SLF001
                cur = store.conn.cursor()
                cur.execute(
                    "UPDATE jobs SET metrics = ? WHERE job_id = ?",
                    (json.dumps({"host_pid": 999_999_999, "execution": "agent"}), job_id),
                )
                store.conn.commit()
                cur.close()
            assert store.mark_interrupted_jobs_on_startup() >= 1
            assert store.get_job(job_id).status == "interrupted"

            out2 = _start_agent_job(store)
            with store._lock:  # noqa: SLF001
                cur = store.conn.cursor()
                cur.execute("UPDATE jobs SET metrics = NULL WHERE job_id = ?", (out2["job_id"],))
                store.conn.commit()
                cur.close()
            store.mark_interrupted_jobs_on_startup()
            assert store.get_job(out2["job_id"]).status == "ready"
        finally:
            rt.store, rt.runner = old_store, old_runner


class TestStreamingLimit:
    BIG = (
        '{"loopmaster":"1.0","name":"big","version":"1.0.0",'
        '"steps":[{"type":"code","name":"flood","ref":"flood@1.0.0"}]}'
    )

    def test_stdout_overflow_is_rejected(self, tmp_path):
        store = JobStore(db_path=tmp_path / "j.db")
        src = "print('x' * 2_000_000)"
        store.save_code_block("flood", "1.0.0", "python", src)
        from loopmaster.executors.code_block import CodeBlockExecutor

        ex = CodeBlockExecutor(ref="flood@1.0.0", db_path=str(tmp_path / "j.db"))
        res = ex.execute({})
        assert res.success is False
        assert "output limit" in (res.error or "")

    def test_normal_output_still_works(self, tmp_path):
        from loopmaster.executors.code_block import CodeBlockExecutor

        payload = json.dumps({"ok": True, "output": {"message": "hello"}, "logs": []})
        JobStore(db_path=tmp_path / "j.db").save_code_block(
            "tiny", "1.0.0", "python", f"print({payload!r})"
        )
        ex2 = CodeBlockExecutor(ref="tiny@1.0.0", db_path=str(tmp_path / "j.db"))
        res = ex2.execute({})
        assert res.success is True
        assert res.output == {"message": "hello"}

    def test_timeout_still_kills(self, tmp_path):
        from loopmaster.executors.code_block import CodeBlockExecutor

        store = JobStore(db_path=tmp_path / "t.db")
        store.save_code_block("sleeper", "1.0.0", "python", "import time; time.sleep(30)")
        ex = CodeBlockExecutor(ref="sleeper@1.0.0", timeout=1.0, db_path=str(tmp_path / "t.db"))
        res = ex.execute({})
        assert res.success is False
        assert res.returncode == -1


class TestDupNameValidate:
    def test_validate_flags_duplicate_names(self):
        spec = {
            "loopmaster": "1.0",
            "name": "dups",
            "version": "1.0.0",
            "steps": [
                {"type": "shell", "name": "same", "command": "echo a"},
                {"type": "shell", "name": "same", "command": "echo b"},
            ],
        }
        errors = validate_loop_spec(spec)
        assert any("duplicate step name 'same'" in e for e in errors)

    def test_parse_still_raises(self):
        spec = {
            "loopmaster": "1.0",
            "name": "dups2",
            "version": "1.0.0",
            "steps": [
                {"type": "shell", "name": "dup", "command": "echo a"},
                {"type": "shell", "name": "dup", "command": "echo b"},
            ],
        }
        with pytest.raises(Exception, match="duplicate"):
            load_loop_from_dict(spec)


class TestCompilerTimeout:
    def test_llm_timeout_passthrough(self):
        from loopmaster.core.types import LoopDef, Step

        ld = LoopDef(name="tt", version="1.0.0", body=lambda ctx: ctx)
        ld._collected_steps = [Step(name="slow-llm", prompt="go", model="@fast", timeout=42)]  # noqa: SLF001
        compiled = compile_loop_spec(ld)
        node = compiled["steps"][0]
        assert node["type"] == "llm"
        assert node["timeout"] == 42


class TestLlmTimeoutValidation:
    @pytest.mark.parametrize("bad", ["banana", -5, 0])
    def test_invalid_llm_timeout_rejected(self, bad):
        spec = {
            "loopmaster": "1.0",
            "name": "bad-timeout",
            "version": "1.0.0",
            "steps": [{"type": "llm", "name": "a", "prompt": "go", "timeout": bad}],
        }
        errors = validate_loop_spec(spec)
        assert any("'timeout'" in e for e in errors)
