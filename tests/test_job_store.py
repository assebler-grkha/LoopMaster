"""Tests for JobStore SQLite persistence and thread safety."""

from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path

import pytest

from loopmaster.mcp.job_store import JobData, JobStore


class TestJobStoreBasics:
    def test_create_and_get_job(self, tmp_path: Path):
        db_file = tmp_path / "test_jobs.db"
        store = JobStore(db_path=db_file)

        job = store.create_job(
            job_id="job-101",
            loop_name="audit_loop",
            definition={"name": "audit_loop", "step_count": 3, "steps": [{"name": "s1"}]},
            status="ready",
        )

        assert job.job_id == "job-101"
        assert job.loop_name == "audit_loop"
        assert job.status == "ready"
        assert job.total_steps == 3

        fetched = store.get_job("job-101")
        assert fetched is not None
        assert fetched.job_id == "job-101"
        assert fetched.definition["name"] == "audit_loop"
        assert fetched.results == {}

    def test_record_step_result_in_progress_and_completion(self, tmp_path: Path):
        db_file = tmp_path / "test_jobs.db"
        store = JobStore(db_path=db_file)

        store.create_job(
            job_id="job-step-test",
            loop_name="multi_step",
            definition={"step_count": 2},
            status="ready",
        )

        # Step 1
        j1 = store.record_step_result(
            job_id="job-step-test",
            step_name="step_1",
            success=True,
            output="Step 1 complete",
        )
        assert j1 is not None
        assert j1.status == "in_progress"
        assert j1.current_step == 1
        assert "step_1" in j1.results

        # Step 2
        j2 = store.record_step_result(
            job_id="job-step-test",
            step_name="step_2",
            success=True,
            output="Step 2 complete",
        )
        assert j2 is not None
        assert j2.status == "completed"
        assert j2.completed_at is not None
        assert j2.current_step == 2

    def test_record_step_result_failure(self, tmp_path: Path):
        db_file = tmp_path / "test_jobs.db"
        store = JobStore(db_path=db_file)

        store.create_job(
            job_id="job-fail-test",
            loop_name="fail_loop",
            definition={"step_count": 2},
            status="ready",
        )

        j = store.record_step_result(
            job_id="job-fail-test",
            step_name="step_1",
            success=False,
            error="Rate limit exceeded",
        )
        assert j is not None
        assert j.status == "error"
        assert j.error == "Rate limit exceeded"

    def test_persistence_across_restarts(self, tmp_path: Path):
        db_file = tmp_path / "persistent.db"

        # Instance 1
        store1 = JobStore(db_path=db_file)
        store1.create_job(
            job_id="persist-1",
            loop_name="persist_loop",
            definition={"step_count": 3},
            status="ready",
        )
        store1.record_step_result(
            job_id="persist-1",
            step_name="s1",
            success=True,
            output="result 1",
        )
        store1.close()

        # Instance 2 (simulating restart)
        store2 = JobStore(db_path=db_file)
        job = store2.get_job("persist-1")
        assert job is not None
        assert job.status == "in_progress"
        assert job.results["s1"]["output"] == "result 1"

        # Continue execution on new instance
        store2.record_step_result("persist-1", "s2", True, "result 2")
        store2.record_step_result("persist-1", "s3", True, "result 3")

        completed_job = store2.get_job("persist-1")
        assert completed_job is not None
        assert completed_job.status == "completed"
        store2.close()

    def test_cancel_and_delete_job(self, tmp_path: Path):
        db_file = tmp_path / "cancel_test.db"
        store = JobStore(db_path=db_file)

        store.create_job(
            job_id="job-cancel",
            loop_name="cancellable",
            definition={"step_count": 1},
        )
        assert store.cancel_job("job-cancel") is True
        job = store.get_job("job-cancel")
        assert job is not None
        assert job.status == "cancelled"

        assert store.delete_job("job-cancel") is True
        assert store.get_job("job-cancel") is None

    def test_mark_interrupted_jobs_on_startup(self, tmp_path: Path):
        db_file = tmp_path / "startup_test.db"
        store = JobStore(db_path=db_file)

        store.create_job("j-run", "loop1", {"step_count": 2}, status="running")
        store.create_job("j-prog", "loop2", {"step_count": 2}, status="in_progress")
        store.create_job("j-done", "loop3", {"step_count": 2}, status="completed")

        interrupted_count = store.mark_interrupted_jobs_on_startup()
        assert interrupted_count == 2

        assert store.get_job("j-run").status == "interrupted"
        assert store.get_job("j-prog").status == "interrupted"
        assert store.get_job("j-done").status == "completed"


class TestJobStoreConcurrency:
    def test_concurrent_step_writes(self, tmp_path: Path):
        db_file = tmp_path / "concurrent.db"
        store = JobStore(db_path=db_file)

        total_steps = 20
        store.create_job(
            job_id="concurrent-job",
            loop_name="heavy_loop",
            definition={"step_count": total_steps},
            status="ready",
        )

        def worker(step_idx: int):
            # Each worker uses store to record step
            store.record_step_result(
                job_id="concurrent-job",
                step_name=f"step_{step_idx}",
                success=True,
                output=f"Output from worker {step_idx}",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(total_steps)]
            concurrent.futures.wait(futures)

        job = store.get_job("concurrent-job")
        assert job is not None
        assert len(job.results) == total_steps
        assert job.status == "completed"
