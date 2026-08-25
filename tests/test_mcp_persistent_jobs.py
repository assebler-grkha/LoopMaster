"""Tests for MCP tools integration with persistent JobStore."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

from loopmaster.llm import LLMResponse
from loopmaster.mcp import runtime
from loopmaster.mcp.job_store import JobStore
from loopmaster.mcp.worker import DetachedRunner
from scripts.loopmaster_mcp import (
    loop_cancel,
    loop_get,
    loop_result,
    loop_run,
    loop_status,
)


def _bind_store(tmp_path: Path) -> JobStore:
    store = JobStore(db_path=tmp_path / "mcp_jobs.db")
    runtime.store = store
    runtime.runner = DetachedRunner(store)
    return store


class TestMCPPersistentJobs:
    def test_step_by_step_execution_with_restart(self, tmp_path: Path):
        store = _bind_store(tmp_path)

        # 1. loop_get registers job
        get_res_str = loop_get("simple_test", search_dir="loops")
        get_res = json.loads(get_res_str)
        assert "job_id" in get_res
        job_id = get_res["job_id"]

        # Verify job is in DB
        job = store.get_job(job_id)
        assert job is not None
        assert job.status == "ready"

        # 2. Simulate server restart by creating fresh JobStore instance
        fresh = JobStore(db_path=tmp_path / "mcp_jobs.db")
        runtime.store = fresh

        # 3. Report Step 1
        res1_str = loop_result(job_id, "greet", success=True, output="Hello Alice!")
        res1 = json.loads(res1_str)
        assert res1["status"] == "in_progress"
        assert res1["next_step"]["name"] == "task"

        # 4. Report Step 2
        res2_str = loop_result(job_id, "task", success=True, output="Task completed.")
        res2 = json.loads(res2_str)
        assert res2["status"] == "in_progress"

        # 5. Check loop_status
        status_str = loop_status(job_id)
        status_json = json.loads(status_str)
        assert status_json["status"] == "in_progress"
        assert status_json["progress"] == "2/3"

        # 6. Report Step 3
        res3_str = loop_result(job_id, "summary", success=True, output="Summary done.")
        res3 = json.loads(res3_str)
        assert res3["status"] == "completed"

        # Verify final status in SQLite
        final_job = fresh.get_job(job_id)
        assert final_job is not None
        assert final_job.status == "completed"
        assert len(final_job.results) == 3

    def test_loop_run_persistent_tracking(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("LOOPMASTER_LLM_PROVIDER", "openai")
        monkeypatch.setenv("LOOPMASTER_OPENAI_API_KEY", "sk-test-key")

        store = _bind_store(tmp_path)

        mock_resp = LLMResponse(
            content="Mocked output",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            model="gpt-4o",
        )

        with patch("loopmaster.llm.client.LLMClient.complete", return_value=mock_resp):
            res_str = loop_run("simple_test", context='{"name": "Alice"}', search_dir="loops")
            res = json.loads(res_str)

            assert "job_id" in res
            job_id = res["job_id"]

            deadline = time.time() + 10
            job = None
            while time.time() < deadline:
                job = store.get_job(job_id)
                if job is not None and job.status in ("completed", "failed"):
                    break
                time.sleep(0.05)

            assert job is not None
            assert job.status == "completed"
            assert job.metrics["total_tokens"] > 0
            assert "greet" in job.results

    def test_loop_cancel_persisted(self, tmp_path: Path):
        store = _bind_store(tmp_path)

        get_res = json.loads(loop_get("simple_test", search_dir="loops"))
        job_id = get_res["job_id"]

        cancel_msg = loop_cancel(job_id)
        assert "cancelled" in cancel_msg

        job = store.get_job(job_id)
        assert job.status == "cancelled"
