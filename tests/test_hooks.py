"""Tests for the hooks registry, built-in hooks, and their wiring into MCP tools."""

import threading

import pytest

from loopmaster import hooks
from loopmaster.hooks_builtin import (
    h_archive_sweeper,
    h_budget_guard,
    h_stale_reaper,
    h_validate_spec,
    h_verify_blocks,
    register_builtins,
)
from loopmaster.mcp import job_store as job_store_mod
from loopmaster.mcp import runtime as rt
from loopmaster.spec.loader import SPEC_VERSION


@pytest.fixture(autouse=True)
def _clean_registry():
    hooks.clear()
    yield
    hooks.clear()


GOOD_SPEC = {
    "loopmaster": SPEC_VERSION,
    "name": "hook-ok",
    "version": "1.0.0",
    "steps": [{"type": "shell", "name": "one", "command": ["python", "-c", "print('ok')"]}],
}


class TestRegistry:
    def test_register_and_get_registry(self):
        hooks.register("before_loop_save", "demo", lambda event, payload: None)
        assert hooks.get_registry() == {"before_loop_save": ["demo"]}

    def test_reregister_replaces(self):
        hooks.register("before_loop_save", "demo", lambda e, p: None)
        hooks.register("before_loop_save", "demo", lambda e, p: {"v": 2})
        results = hooks.trigger("before_loop_save", {})
        assert results == [{"hook": "demo", "v": 2}]

    def test_unregister(self):
        fn = lambda e, p: None  # noqa: E731
        hooks.register("before_loop_run", "gone", fn)
        assert hooks.unregister("before_loop_run", "gone") is True
        assert hooks.unregister("before_loop_run", "gone") is False

    def test_unknown_event_raises(self):
        with pytest.raises(ValueError, match="unknown hook event"):
            hooks.register("nope", "x", lambda e, p: None)

    def test_trigger_swallows_exceptions(self):
        def boom(event, payload):
            raise RuntimeError("kaboom")

        hooks.register("after_loop_run", "bad", boom)
        hooks.register("after_loop_run", "good", lambda e, p: {"ok": True})
        results = hooks.trigger("after_loop_run", {})
        assert any(r.get("error") for r in results)
        assert any(r.get("ok") for r in results)

    def test_hook_veto_propagates_with_name(self):
        def veto(event, payload):
            raise hooks.HookVeto("not allowed")

        hooks.register("before_loop_run", "guard", veto)
        with pytest.raises(hooks.HookVeto, match=r"\[guard\] not allowed"):
            hooks.trigger("before_loop_run", {})

    def test_load_user_hooks_from_file(self, tmp_path):
        path = tmp_path / "hooks.py"
        path.write_text(
            "from loopmaster import hooks\n"
            "def my_hook(event, payload):\n"
            "    return {'seen': True}\n"
            "hooks.register('notification_created', 'my_hook', my_hook)\n"
        )
        added = hooks.load_user_hooks(path)
        assert added == 1
        results = hooks.trigger("notification_created", {})
        assert results == [{"hook": "my_hook", "seen": True}]

    def test_load_user_hooks_missing_file_returns_zero(self, tmp_path):
        assert hooks.load_user_hooks(tmp_path / "nope.py") == 0


class TestBuiltins:
    def test_validate_spec_passes_good_spec(self):
        assert h_validate_spec(hooks.BEFORE_LOOP_SAVE, {"spec": GOOD_SPEC}) == {"checked": True}

    def test_validate_spec_vetoes_bad_spec(self):
        bad = {
            **GOOD_SPEC,
            "steps": [
                {"type": "shell", "name": "a", "command": "x"},
                {"type": "shell", "name": "a", "command": "y"},
            ],
        }
        with pytest.raises(hooks.HookVeto, match="invalid spec"):
            h_validate_spec(hooks.BEFORE_LOOP_RUN, {"spec": bad})

    def test_verify_blocks_vetoes_unknown_block(self):
        spec = {
            **GOOD_SPEC,
            "steps": [
                {"type": "code", "name": "blk", "ref": "missing-block@1.0.0"},
            ],
        }
        store = job_store_mod.JobStore(db_path=":memory:")
        with pytest.raises(hooks.HookVeto, match="[Uu]nknown block"):
            h_verify_blocks(hooks.BEFORE_LOOP_SAVE, {"spec": spec, "store": store})

    def test_budget_guard_env_gate(self, monkeypatch):
        monkeypatch.setenv("LM_REQUIRE_BUDGET", "1")
        no_budget = {k: v for k, v in GOOD_SPEC.items() if k != "budget"}
        with pytest.raises(hooks.HookVeto, match="budget"):
            h_budget_guard(hooks.BEFORE_LOOP_RUN, {"spec": no_budget})
        budgeted = {**GOOD_SPEC, "budget": {"max_cost": 1.0}}
        assert h_budget_guard(hooks.BEFORE_LOOP_RUN, {"spec": budgeted}) == {
            "budget_required": True
        }

    def test_register_builtins_fills_registry(self):
        register_builtins()
        reg = hooks.get_registry()
        assert "validate-spec" in reg["before_loop_save"]
        assert "verify-blocks" in reg["before_loop_save"]
        assert "validate-spec" in reg["before_loop_run"]
        assert "budget-guard" in reg["before_loop_run"]
        assert "notify-dispatcher" in reg["notification_created"]
        assert "hitl-escalate" in reg["hitl_escalation"]

    def test_reaper_and_sweeper_on_store(self, tmp_path):
        store = job_store_mod.JobStore(db_path=tmp_path / "jobs.db")
        reaped = h_stale_reaper(store)
        assert isinstance(reaped, dict) and "reaped" in reaped
        swept = h_archive_sweeper(store)
        assert isinstance(swept, dict) and "notifications_removed" in swept


class TestToolWiring:
    @pytest.fixture()
    def bound(self, tmp_path, monkeypatch):
        old_store, old_runner = rt.store, rt.runner
        store = job_store_mod.JobStore(db_path=tmp_path / "wiring.db")
        monkeypatch.setattr(rt, "store", store)
        try:
            yield store
        finally:
            rt.store, rt.runner = old_store, old_runner

    def test_loop_save_honors_veto(self, bound):
        from loopmaster.mcp import tools_loops

        def veto(event, payload):
            raise hooks.HookVeto("spec forbidden")

        hooks.register("before_loop_save", "veto", veto)
        spec_json = '{"loopmaster":"1.0","name":"blocked-loop","version":"1.0.0","steps":[{"type":"shell","name":"one","command":["python","-c","print(1)"]}]}'
        out = tools_loops.loop_save(loop_name="blocked-loop", spec_json=spec_json)
        assert "rejected by hook" in str(out)

    def test_loop_run_honors_veto(self, bound):
        from loopmaster.mcp import tools_run

        def veto(event, payload):
            raise hooks.HookVeto("runs disabled")

        hooks.register("before_loop_run", "veto", veto)
        spec_json = '{"loopmaster":"1.0","name":"veto-run","version":"1.0.0","steps":[{"type":"shell","name":"one","command":["python","-c","print(1)"]}]}'
        out = tools_run._run_spec_json(spec_json, context="{}", mode="detached")
        assert "rejected by hook" in str(out)

    def test_concurrent_triggers_are_thread_safe(self):
        seen = []
        lock = threading.Lock()

        def collector(event, payload):
            with lock:
                seen.append(payload["i"])
            return None

        hooks.register("after_loop_run", "collect", collector)
        threads = [
            threading.Thread(target=hooks.trigger, args=("after_loop_run", {"i": i}))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(seen) == list(range(8))
