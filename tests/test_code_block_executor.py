"""Tests for CodeBlockExecutor (Phase 3): subprocess isolation + JSON contract."""

from __future__ import annotations

import hashlib
import json

import pytest

from loopmaster.executors.code_block import CodeBlockExecutor
from loopmaster.mcp.job_store import JobStore
from loopmaster.mcp.worker import DetachedRunner
from loopmaster.spec.loader import load_loop_from_dict, validate_loop_spec

ECHO_BLOCK = (
    "import json, sys\n"
    "msg = json.load(sys.stdin)\n"
    "json.dump({'ok': True, 'output': {'echo': msg['input'].get('text', '')},"
    " 'logs': ['ran']}, sys.stdout)\n"
)
FAIL_BLOCK = (
    "import json, sys\n"
    "json.load(sys.stdin)\n"
    "json.dump({'ok': False, 'error': 'nope', 'output': None}, sys.stdout)\n"
)


@pytest.fixture()
def store(tmp_path):
    s = JobStore(db_path=tmp_path / "jobs.db")
    s.save_code_block("echo-block", "1.0.0", "python", ECHO_BLOCK)
    s.save_code_block("fail-block", "1.0.0", "python", FAIL_BLOCK)
    return s


def _sha(src: str) -> str:
    return hashlib.sha256(src.encode()).hexdigest()


class TestExecutor:
    def test_success_and_output(self, store):
        ex = CodeBlockExecutor(
            ref="echo-block@1.0.0",
            input={"text": "hi"},
            db_path=store.db_path,
        )
        result = ex.execute({})
        assert result.success is True
        assert result.ok is True
        assert result.output == {"echo": "hi"}
        assert result.logs == ["ran"]
        assert result.returncode == 0

    def test_input_template_resolution(self, store):
        ex = CodeBlockExecutor(
            ref="echo-block@1.0.0",
            input={"text": "{goal}"},
            db_path=store.db_path,
        )
        assert ex.execute({"goal": "world"}).output == {"echo": "world"}

    def test_unknown_ref(self, store):
        ex = CodeBlockExecutor(ref="ghost@9.9.9", db_path=store.db_path)
        res = ex.execute({})
        assert res.success is False
        assert "unknown code block" in (res.error or "")

    def test_sha256_pin_mismatch(self, store):
        ex = CodeBlockExecutor(
            ref="echo-block@1.0.0",
            sha256="f" * 64,
            db_path=store.db_path,
        )
        res = ex.execute({})
        assert res.success is False
        assert "sha256 mismatch" in (res.error or "")

    def test_block_reports_failure(self, store):
        ex = CodeBlockExecutor(ref="fail-block@1.0.0", db_path=store.db_path)
        res = ex.execute({})
        assert res.success is False
        assert "nope" in (res.error or "")

    def test_denied_capability(self, store, tmp_path):
        store.save_code_block(
            "netty",
            "1.0.0",
            "python",
            ECHO_BLOCK,
            capabilities=["net"],
        )
        ex = CodeBlockExecutor(
            ref="netty@1.0.0",
            deny_capabilities=["net"],
            db_path=store.db_path,
        )
        res = ex.execute({})
        assert res.success is False
        assert "denied capabilities" in (res.error or "")


class TestSpecIntegration:
    def test_code_node_validates(self):
        data = {
            "loopmaster": "1.0",
            "name": "code-loop",
            "version": "1.0.0",
            "context": {"plan": "fast"},
            "steps": [
                {
                    "type": "code",
                    "name": "fix",
                    "ref": "test-fixer@1.4.2",
                    "sha256": "a" * 64,
                    "input": {"mode": "{plan}"},
                }
            ],
        }
        assert validate_loop_spec(data) == []
        loop_def, spec = load_loop_from_dict(data)
        step = loop_def._collected_steps[0]
        assert step.executor.ref == "test-fixer@1.4.2"
        assert step.executor.deny_capabilities == []

    @pytest.mark.parametrize(
        ("node", "fragment"),
        [
            ({"type": "code", "name": "blk"}, "'ref'"),
            ({"type": "code", "name": "blk", "ref": "BAD REF"}, "'ref'"),
            ({"type": "code", "name": "blk", "ref": "blk@1.0.0", "sha256": "xyz"}, "sha256"),
            ({"type": "code", "name": "blk", "ref": "blk@1.0.0", "input": [1]}, "'input'"),
        ],
    )
    def test_code_node_invalid_fields(self, node, fragment):
        data = {
            "loopmaster": "1.0",
            "name": "bad-code-loop",
            "version": "1.0.0",
            "steps": [node],
        }
        errors = validate_loop_spec(data)
        assert any(fragment in e for e in errors)

    def test_deny_capabilities_top_level(self):
        data = {
            "loopmaster": "1.0",
            "name": "deny-loop",
            "version": "1.0.0",
            "deny_capabilities": ["net"],
            "steps": [{"type": "llm", "name": "s1", "prompt": "go"}],
        }
        assert validate_loop_spec(data) == []


class TestDetachedEndToEnd:
    def test_detached_run_with_code_step(self, tmp_path, monkeypatch):
        monkeypatch.setattr("loopmaster.mcp.job_store._global_store", None)
        monkeypatch.setenv("LOOPMASTER_JOBS_DB", str(tmp_path / "jobs.db"))
        store = JobStore(db_path=tmp_path / "jobs.db")
        digest = hashlib.sha256(ECHO_BLOCK.encode()).hexdigest()
        store.save_code_block("e2e-echo", "1.0.0", "python", ECHO_BLOCK)
        spec = {
            "loopmaster": "1.0",
            "name": "code-e2e",
            "version": "1.0.0",
            "context": {"greeting": "unused-initial"},
            "steps": [
                {
                    "type": "code",
                    "name": "run-echo",
                    "ref": "e2e-echo@1.0.0",
                    "sha256": digest,
                    "input": {"text": "{greeting}"},
                }
            ],
        }
        loop_def, sp = load_loop_from_dict(spec)
        runner = DetachedRunner(store, poll_s=0.05, heartbeat_s=60)
        job_id = runner.submit(loop_def, initial_context={"greeting": "hello"})
        job = None
        for _ in range(200):
            job = store.get_job(job_id)
            assert job is not None
            if job.status in {"completed", "failed", "error"}:
                break
            import time

            time.sleep(0.05)
        assert job.status == "completed", job.error
        step_result = job.results["run-echo"]
        payload = json.dumps(step_result)
        assert '"echo"' in payload and "hello" in payload
