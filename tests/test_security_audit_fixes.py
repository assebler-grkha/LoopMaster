"""Tests for security-audit fixes (R1-R16): AST discovery, env, entrypoint,
HTTP hardening, HITL nonce, atomic store transitions, escalation cap."""

import json
import textwrap
import types

import pytest

import loopmaster.mcp.runtime as rt
from loopmaster.executors.http import MAX_RESPONSE_BYTES, _read_capped, _validate_url
from loopmaster.executors.shell import ShellExecutor
from loopmaster.mcp.discovery import find_loop_files, inspect_loop_file
from loopmaster.mcp.job_store import JobStore
from loopmaster.mcp.tools_loops import loop_status
from loopmaster.mcp.tools_run import loop_run

LOOP_FILE = textwrap.dedent(
    """
    from loopmaster.core.types import Loop, Step

    @Loop(name="audit-loop", version="1.2.0")
    def audit_loop(ctx):
        return Step(name="only", model="m", prompt="p")
    """
)

GARBAGE_FILE = textwrap.dedent(
    """
    # this comment mentions "from loopmaster" and "@Loop" but defines nothing
    import os
    raise RuntimeError("this module must never be executed by discovery")
    """
)


@pytest.fixture()
def store(tmp_path, monkeypatch):
    store = JobStore(db_path=tmp_path / "jobs.db")
    old_store, old_runner = rt.store, rt.runner
    rt.store = store
    yield store
    rt.store, rt.runner = old_store, old_runner


class TestAstDiscovery:
    def test_inspect_loop_file_reads_metadata(self, tmp_path):
        f = tmp_path / "loop.py"
        f.write_text(LOOP_FILE, encoding="utf-8")
        meta = inspect_loop_file(f)
        assert meta is not None
        assert meta["name"] == "audit-loop"
        assert meta["version"] == "1.2.0"

    def test_find_loop_files_skips_non_loop_garbage(self, tmp_path):
        (tmp_path / "good.py").write_text(LOOP_FILE, encoding="utf-8")
        (tmp_path / "garbage.py").write_text(GARBAGE_FILE, encoding="utf-8")
        found = {f.name for f in find_loop_files(tmp_path)}
        assert "good.py" in found
        assert "garbage.py" not in found

    def test_inspect_returns_none_without_decorator(self, tmp_path):
        f = tmp_path / "plain.py"
        f.write_text("x = 1\n", encoding="utf-8")
        assert inspect_loop_file(f) is None


class TestHooksOptIn:
    def test_user_hooks_not_loaded_by_default(self):
        import loopmaster.mcp.runtime as runtime_mod

        assert runtime_mod._user_hooks_loaded == 0


class TestLegacyPathHookVeto:
    def test_legacy_loop_name_run_fires_policy_hook(self, store, tmp_path, monkeypatch):
        (tmp_path / "veto_loop.py").write_text(LOOP_FILE.replace("audit-loop", "veto-loop"))
        monkeypatch.setenv("LOOPMASTER_LLM_API_KEY", "test-key")

        class FakeVeto(Exception):
            pass

        calls: list[tuple[str, dict]] = []

        def trigger(event, payload):
            calls.append((event, payload))
            raise FakeVeto("no-shell-for-you")

        fake = types.SimpleNamespace(
            BEFORE_LOOP_RUN="before_loop_run",
            HookVeto=FakeVeto,
            trigger=trigger,
        )
        import loopmaster.mcp.tools_run as tools_run_mod

        monkeypatch.setattr(tools_run_mod, "hooks", fake)
        out = loop_run(loop_name="veto-loop", search_dir=str(tmp_path))
        parsed = json.loads(out)
        assert "rejected by hook" in parsed.get("error", "")
        assert calls and calls[0][0] == "before_loop_run"


class TestMinimalEnv:
    def test_shell_default_deny_env(self, monkeypatch):
        monkeypatch.setenv("LM_SECRET_PROBE", "leak-me")
        executor = ShellExecutor(
            ["python", "-c", "import os; print(os.environ.get('LM_SECRET_PROBE', 'ABSENT'))"],
            timeout=30.0,
        )
        result = executor.execute({})
        assert result.success
        assert result.stdout.strip() == "ABSENT"

    def test_shell_env_inherit_opt_in(self, monkeypatch):
        monkeypatch.setenv("LM_SECRET_PROBE", "visible")
        executor = ShellExecutor(
            ["python", "-c", "import os; print(os.environ.get('LM_SECRET_PROBE', 'ABSENT'))"],
            timeout=30.0,
            env_inherit=True,
        )
        result = executor.execute({})
        assert result.success
        assert result.stdout.strip() == "visible"


class TestEntrypointGuard:
    def test_save_rejects_traversal_entrypoint(self, store):
        with pytest.raises(ValueError, match="entrypoint"):
            store.save_code_block(
                "evil-block",
                "1.0.0",
                "python",
                "print('x')",
                entrypoint="../../evil/main",
            )

    def test_tampered_entrypoint_cannot_escape_cache(self, store, tmp_path):
        from loopmaster.executors.code_block import CodeBlockExecutor

        store.save_code_block("tame", "1.0.0", "python", "print('ok')")
        with store._lock:
            cur = store.conn.cursor()
            cur.execute(
                "UPDATE code_blocks SET entrypoint = ? WHERE name = 'tame'",
                ("../escaped",),
            )
            store.conn.commit()
            cur.close()
        executor = CodeBlockExecutor(ref="tame@1.0.0", db_path=str(tmp_path / "jobs.db"))
        result = executor.execute({})
        assert not result.success
        assert "cache directory" in (result.error or "")


class TestHttpHardening:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/x",
            "javascript:alert(1)",
        ],
    )
    def test_disallowed_schemes_rejected(self, url):
        assert _validate_url(url) is not None

    def test_https_allowed(self):
        assert _validate_url("https://example.com/api") is None

    def test_read_capped_stops_at_limit(self):
        class Flood:
            def __init__(self):
                self.sent = 0

            def read(self, n):
                if self.sent >= MAX_RESPONSE_BYTES * 3:
                    return b""
                self.sent += n
                return b"a" * n

        data, overflow = _read_capped(Flood())
        assert overflow
        assert len(data) <= MAX_RESPONSE_BYTES


class TestHitlNonce:
    def test_nonce_generated_and_required(self, store, monkeypatch):
        from loopmaster.mcp.tools_hitl import loop_respond

        msg = store.create_question("j-nonce", "loop:d#ask", text="Q?")
        assert msg.payload.get("nonce")
        out = loop_respond(job_id="j-nonce", msg_id=msg.msg_id, answer="yes")
        assert "nonce" in out.lower()
        out = loop_respond(job_id="j-nonce", msg_id=msg.msg_id, answer="yes", nonce="wrong")
        assert "invalid or missing nonce" in out
        out = loop_respond(
            job_id="j-nonce",
            msg_id=msg.msg_id,
            answer="yes",
            nonce=msg.payload["nonce"],
        )
        assert '"responded": true' in out

    def test_answer_size_cap(self, store, monkeypatch):
        from loopmaster.mcp.tools_hitl import loop_respond

        msg = store.create_question("j-big", "loop:d#ask", text="Q?")
        big = "x" * (65 * 1024)
        out = loop_respond(
            job_id="j-big", msg_id=msg.msg_id, answer=big, nonce=msg.payload["nonce"]
        )
        assert "byte limit" in out
        assert store.get_message(msg.msg_id).status == "pending"


class TestAtomicTransitions:
    def test_upsert_does_not_reset_active_job(self, store):
        store.create_job("live-1", "loop-x", {"step_count": 1}, status="running")
        store.record_step_result("live-1", "s1", success=True, auto_complete=False)
        before = store.get_job("live-1")
        store.create_job("live-1", "loop-x", {"step_count": 9}, status="running")
        after = store.get_job("live-1")
        assert after.results == before.results
        assert after.status != "ready"
        assert after.current_step == before.current_step

    def test_upsert_resets_terminal_job(self, store):
        store.create_job("done-1", "loop-x", {"step_count": 1}, status="running")
        store.update_job("done-1", status="failed", error="boom", completed=True)
        job = store.create_job("done-1", "loop-y", {"step_count": 2}, status="running")
        assert job.status == "running"
        assert store.get_job("done-1").total_steps == 2

    def test_cancel_completed_is_noop(self, store):
        store.create_job("fin-1", "loop-x", {"step_count": 1}, status="running")
        store.update_job("fin-1", status="completed", completed=True)
        assert store.cancel_job("fin-1") is False
        assert store.get_job("fin-1").status == "completed"


class TestEscalationCap:
    def test_escalation_eventually_fails(self, store, tmp_path, monkeypatch):
        from loopmaster.executors.human_input import HumanInputExecutor

        monkeypatch.setattr("loopmaster.mcp.job_store._global_store", None, raising=False)
        executor = HumanInputExecutor(
            step_name="confirm",
            question="Proceed?",
            timeout="1s",
            on_timeout="escalate",
            poll_s=0.05,
            db_path=str(tmp_path / "jobs.db"),
        )
        result = executor.execute({"__job_id__": "", "__loop_name__": ""})
        assert not result.success
        assert "escalation limit" in (result.error or "")

    def test_poison_guard_accepts_double_brace(self, store):
        with pytest.raises(ValueError, match="placeholder"):
            store.create_question(
                "j-poison",
                "loop:d#ask",
                text="Q?",
                default_answer="{{step1.stdout}}",
            )


class TestStatusSanitization:
    def test_host_pid_hidden_from_status(self, store):
        import os as _os

        store.create_job(
            "sanitized",
            "loop-x",
            {"step_count": 1},
            status="running",
            metrics={"host_pid": _os.getpid(), "detached": True},
        )
        payload = json.loads(loop_status(job_id="sanitized"))
        assert "host_pid" not in payload.get("metrics", {})
