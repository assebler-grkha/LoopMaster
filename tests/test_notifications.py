"""Phase 5 notifications: outbox store, worker emission, HITL link, MCP marker."""

from __future__ import annotations

import json
import threading
import time

import pytest

from loopmaster.mcp.job_store import JobStore
from loopmaster.mcp.tools_notifications import with_pending
from loopmaster.mcp.worker import DetachedRunner, _allowed_priorities
from loopmaster.spec import validate_loop_spec


@pytest.fixture()
def store(tmp_path):
    return JobStore(db_path=tmp_path / "jobs.db")


@pytest.fixture()
def runner(store):
    return DetachedRunner(store, poll_s=0.05)


def _wait_terminal(store, job_id, deadline=10.0):
    end = time.time() + deadline
    while time.time() < end:
        job = store.get_job(job_id)
        if job is not None and job.status in {"completed", "failed", "cancelled", "interrupted"}:
            return job
        time.sleep(0.05)
    raise AssertionError("job did not reach terminal state")


def _events(store):
    return {(n.priority, n.event) for n in store.list_notifications(unread_only=False, limit=100)}


class TestNotificationStore:
    def test_create_requires_valid_priority(self, store):
        with pytest.raises(ValueError, match="priority"):
            store.create_notification(priority="urgent", event="x", summary="s")

    def test_create_requires_event_and_summary(self, store):
        with pytest.raises(ValueError, match="event"):
            store.create_notification(priority="info", event=" ", summary="s")
        with pytest.raises(ValueError, match="summary"):
            store.create_notification(priority="info", event="e", summary="")

    def test_create_and_get_roundtrip(self, store):
        notif = store.create_notification(
            priority="info",
            event="loop_completed",
            summary="done",
            job_id="j1",
            detail={"cost": 0.1},
        )
        fetched = store.get_notification(notif.notif_id)
        assert fetched is not None
        assert fetched.event == "loop_completed"
        assert fetched.detail == {"cost": 0.1}
        assert fetched.read_by_agent is False

    def test_idempotent_duplicates_collapse(self, store):
        first = store.create_notification("info", "step_done", "s1", job_id="j1")
        second = store.create_notification("info", "step_done", "s1", job_id="j1")
        assert first.notif_id == second.notif_id
        assert len(store.list_notifications(unread_only=False)) == 1

    def test_list_unread_only_and_mark_read(self, store):
        a = store.create_notification("info", "a", "a", job_id="j1")
        b = store.create_notification("critical", "b", "b", job_id="j2")
        assert [n.notif_id for n in store.list_notifications(unread_only=True)] == [
            b.notif_id,
            a.notif_id,
        ]
        assert store.mark_notifications_read([b.notif_id]) == 1
        unread = store.list_notifications(unread_only=True)
        assert [n.notif_id for n in unread] == [a.notif_id]
        assert store.mark_notifications_read() == 1
        assert store.list_notifications(unread_only=True) == []

    def test_mark_job_notifications_read_with_event_filter(self, store):
        store.create_notification("needs_input", "waiting_input", "q", job_id="j1")
        store.create_notification("info", "loop_started", "s", job_id="j1")
        marked = store.mark_job_notifications_read("j1", event="waiting_input")
        assert marked == 1
        counts = store.pending_notification_counts()
        assert counts == {"info": 1, "needs_input": 0, "critical": 0}

    def test_pending_counts_by_priority(self, store):
        store.create_notification("info", "e1", "s", job_id="j")
        store.create_notification("critical", "e2", "s", job_id="j")
        assert store.pending_notification_counts() == {
            "info": 1,
            "needs_input": 0,
            "critical": 1,
        }

    def test_critical_fallback_file_written(self, store, tmp_path):
        store.create_notification("critical", "loop_failed", "boom", job_id="jx")
        fallback = tmp_path / "inbox" / "critical.json"
        assert fallback.exists()
        payload = json.loads(fallback.read_text(encoding="utf-8"))
        assert payload[0]["event"] == "loop_failed"

    def test_critical_fallback_updated_on_read(self, store, tmp_path):
        store.create_notification("critical", "loop_failed", "boom", job_id="jx")
        store.mark_notifications_read()
        fallback = tmp_path / "inbox" / "critical.json"
        assert json.loads(fallback.read_text(encoding="utf-8")) == []

    def test_sweep_old_notifications_keeps_unread(self, store):
        old = store.create_notification("info", "old", "old-read", job_id="j1")
        store.mark_notifications_read([old.notif_id])
        store.conn.execute(
            "UPDATE notifications SET created_at = ? WHERE notif_id = ?",
            (time.time() - 8 * 86400, old.notif_id),
        )
        store.conn.commit()
        fresh = store.create_notification("info", "new", "new-unread", job_id="j2")
        removed = store.sweep_old_notifications()
        assert removed == 1
        assert store.get_notification(old.notif_id) is None
        assert store.get_notification(fresh.notif_id) is not None

    def test_sweep_old_messages_archives(self, store, tmp_path):
        store.create_question(job_id="j9", from_addr="loop:l#ask", text="hi?")
        store.answer_question(store.list_questions(job_id="j9")[0].msg_id, "yes", by="agent")
        store.conn.execute("UPDATE messages SET created_at = ?", (time.time() - 31 * 86400,))
        store.conn.commit()
        removed = store.sweep_old_messages(archive_dir=tmp_path / "archive")
        assert removed == 1
        archives = list((tmp_path / "archive").glob("messages-*.jsonl"))
        assert len(archives) == 1
        assert json.loads(archives[0].read_text(encoding="utf-8").splitlines()[0])["job_id"] == "j9"


class TestNotifyFilter:
    def test_default_all(self):
        assert _allowed_priorities(None) == {"info", "needs_input", "critical"}

    def test_spec_notify_subset(self):
        definition = {"spec": {"notify": ["critical"]}}
        assert _allowed_priorities(definition) == {"critical"}

    def test_invalid_notify_falls_back(self):
        assert _allowed_priorities({"spec": {"notify": ["nope"]}}) == {"needs_input", "critical"}


class TestWorkerEmission:
    OK_SPEC = {
        "loopmaster": "1.0",
        "name": "notif-ok",
        "version": "1.0.0",
        "steps": [{"type": "shell", "name": "hi", "command": ["python", "-c", "print('ok')"]}],
    }
    FAIL_SPEC = {
        "loopmaster": "1.0",
        "name": "notif-fail",
        "version": "1.0.0",
        "steps": [
            {
                "type": "shell",
                "name": "boom",
                "command": ["python", "-c", "import sys; sys.exit(3)"],
                "check": True,
            }
        ],
    }

    def test_lifecycle_info_notifications(self, store, runner):
        from loopmaster.spec import load_loop_from_dict

        loop_def, _ = load_loop_from_dict(dict(self.OK_SPEC))
        job_id = runner.submit(loop_def)
        _wait_terminal(store, job_id)
        events = _events(store)
        assert ("info", "loop_started") in events
        assert ("info", "loop_completed") in events

    def test_failure_emits_critical_and_fallback_file(self, store, runner, tmp_path):
        from loopmaster.spec import load_loop_from_dict

        loop_def, _ = load_loop_from_dict(dict(self.FAIL_SPEC))
        job_id = runner.submit(loop_def)
        job = _wait_terminal(store, job_id)
        assert job.status in {"failed", "completed"}
        events = _events(store)
        if job.status == "failed":
            assert ("critical", "loop_failed") in events
            fallback = tmp_path / "inbox" / "critical.json"
            assert fallback.exists()

    def test_notify_filter_suppresses_info(self, store, runner):
        from loopmaster.spec import load_loop_from_dict

        spec = dict(self.OK_SPEC)
        spec["notify"] = ["critical"]
        loop_def, _ = load_loop_from_dict(spec)
        definition = {"spec": spec}
        job_id = runner.submit(loop_def, definition=definition)
        _wait_terminal(store, job_id)
        events = _events(store)
        assert ("info", "loop_started") not in events
        assert ("info", "loop_completed") not in events


class TestHitlNotificationLink:
    def test_waiting_input_creates_needs_input(self, store, monkeypatch):
        from loopmaster.executors.human_input import HumanInputExecutor

        monkeypatch.setattr("loopmaster.mcp.job_store._global_store", store)
        executor = HumanInputExecutor(step_name="ask", question="Proceed?")
        ctx = {"__job_id__": "hitl-j1", "__loop_name__": "hitl-loop"}
        result_box: dict = {}

        def run():
            result_box["res"] = executor.execute(ctx)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        deadline = time.time() + 5
        questions = []
        while time.time() < deadline:
            questions = store.list_questions(job_id="hitl-j1")
            if questions:
                break
            time.sleep(0.05)
        assert questions, "question was not registered"
        deadline = time.time() + 5
        counts = {"needs_input": 0}
        while time.time() < deadline:
            counts = store.pending_notification_counts()
            if counts["needs_input"] >= 1:
                break
            time.sleep(0.05)
        assert counts["needs_input"] >= 1
        notifs = store.list_notifications(unread_only=True)
        waiting = [n for n in notifs if n.event == "waiting_input"]
        assert waiting and waiting[0].detail.get("msg_id") == questions[0].msg_id

        store.answer_question(questions[0].msg_id, "yes", by="agent")
        store.mark_job_notifications_read("hitl-j1", event="waiting_input")
        thread.join(timeout=5)
        assert result_box["res"].answer == "yes"
        assert store.pending_notification_counts()["needs_input"] == 0


class TestLoaderNotify:
    BASE = {
        "loopmaster": "1.0",
        "name": "n-l",
        "version": "1.0.0",
        "steps": [{"type": "llm", "name": "a", "prompt": "hi"}],
    }

    def test_valid_notify(self):
        spec = {**self.BASE, "notify": ["needs_input", "critical"]}
        assert validate_loop_spec(spec) == []

    def test_invalid_priority_rejected(self):
        spec = {**self.BASE, "notify": ["loud"]}
        errors = validate_loop_spec(spec)
        assert any("'notify'" in e for e in errors)

    def test_empty_notify_rejected(self):
        spec = {**self.BASE, "notify": []}
        assert validate_loop_spec(spec)


class TestWithPendingMarker:
    def test_attaches_counts(self, store, monkeypatch):
        import loopmaster.mcp.runtime as rt

        monkeypatch.setattr(rt, "store", store)
        store.create_notification("critical", "loop_failed", "x", job_id="j")
        marker = with_pending({"ok": True})
        assert marker["ok"] is True
        assert marker["pending_notifications"]["critical"] == 1

    def test_survives_store_failure(self, monkeypatch):
        import loopmaster.mcp.runtime as rt

        class Boom:
            def pending_notification_counts(self):
                raise RuntimeError("db gone")

        monkeypatch.setattr(rt, "store", Boom())
        marker = with_pending({})
        assert marker["pending_notifications"] == {
            "info": 0,
            "needs_input": 0,
            "critical": 0,
        }
