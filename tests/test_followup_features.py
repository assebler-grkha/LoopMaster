"""Tests for agent-mode execution (M4), stdout streaming limit (L2), and loader/compiler NITs."""

import json

import pytest

import loopmaster.mcp.runtime as rt
from loopmaster.mcp.job_store import JobStore
from loopmaster.mcp.tools_run import loop_run
from loopmaster.spec.compiler import compile_loop_spec
from loopmaster.spec.loader import load_loop_from_dict, validate_loop_spec

AGENT_SPEC = {
    "loopmaster": "1.0",
    "name": "agent-demo",
    "version": "1.0.0",
    "execution": "agent",
    "steps": [{"type": "llm", "name": "think", "prompt": "Do the thing."}],
}


def _bind(tmp_path):
    store = JobStore(db_path=tmp_path / "jobs.db")
    old_store, old_runner = rt.store, rt.runner
    rt.store = store
    rt.runner = __import__("loopmaster.mcp.worker", fromlist=["DetachedRunner"]).DetachedRunner(
        store
    )
    return store, old_store, old_runner


class TestAgentMode:
    def test_agent_job_is_ready_without_worker(self, tmp_path):
        store, old_store, old_runner = _bind(tmp_path)
        try:
            out = json.loads(loop_run(spec_json=json.dumps(AGENT_SPEC), mode="agent"))
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
            out = json.loads(loop_run(spec_json=json.dumps(AGENT_SPEC), mode="agent"))
            job_id = out["job_id"]
            rec = json.loads(
                __import__("loopmaster.mcp.tools_run", fromlist=["loop_record"]).loop_record(
                    job_id=job_id, step_name="think", output=json.dumps({"done": True})
                )
            )
            assert rec["recorded"] is True
            job = store.get_job(job_id)
            assert job.status == "completed"
        finally:
            rt.store, rt.runner = old_store, old_runner

    def test_record_unknown_job_404(self, tmp_path):
        _bind(tmp_path)
        try:
            tools_run = __import__("loopmaster.mcp.tools_run", fromlist=["loop_record"])
            out = json.loads(tools_run.loop_record(job_id="nope", step_name="x"))
            assert "error" in out
        finally:
            pass


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
