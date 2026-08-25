"""JobOps mixin — persistent loop-execution jobs lifecycle."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

from loopmaster.mcp.store_models import TERMINAL_STATUSES, JobData, StoreHost, is_pid_alive

logger = logging.getLogger("loopmaster.mcp.job_store")


class JobOpsMixin(StoreHost):
    """CRUD for the jobs table."""

    def create_job(
        self,
        job_id: str,
        loop_name: str,
        definition: dict[str, Any],
        status: str = "ready",
        total_steps: int | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> JobData:
        """Create a new persistent job (upsert: resets an existing row)."""
        now = time.time()
        steps_count = (
            total_steps
            if total_steps is not None
            else definition.get("step_count", len(definition.get("steps", [])))
        )
        metrics = metrics or {}
        job = JobData(
            job_id=job_id,
            loop_name=loop_name,
            status=status,
            current_step=0,
            total_steps=steps_count,
            definition=definition,
            results={},
            created_at=now,
            updated_at=now,
            metrics=metrics,
        )

        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO jobs (
                    job_id, loop_name, status, current_step, total_steps,
                    definition, results, created_at, updated_at,
                    completed_at, error, metrics
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    loop_name = excluded.loop_name,
                    status = excluded.status,
                    current_step = 0,
                    total_steps = excluded.total_steps,
                    definition = excluded.definition,
                    results = '{}',
                    updated_at = excluded.updated_at,
                    completed_at = NULL,
                    error = NULL,
                    metrics = excluded.metrics
                """,
                (
                    job.job_id,
                    job.loop_name,
                    job.status,
                    job.current_step,
                    job.total_steps,
                    json.dumps(job.definition, default=str),
                    json.dumps(job.results, default=str),
                    job.created_at,
                    job.updated_at,
                    json.dumps(metrics, default=str),
                ),
            )
            self.conn.commit()
            cur.close()
        return job

    def get_job(self, job_id: str) -> JobData | None:
        """Retrieve a job by ID."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
            row = cur.fetchone()
            cur.close()
            return self._row_to_job(row) if row else None

    def update_job(
        self,
        job_id: str,
        status: str | None = None,
        current_step: int | None = None,
        results: dict[str, Any] | None = None,
        error: str | None = None,
        metrics: dict[str, Any] | None = None,
        completed: bool = False,
    ) -> JobData | None:
        """Update job fields."""
        with self._lock:
            job = self.get_job(job_id)
            if not job:
                return None
            if job.status in TERMINAL_STATUSES and not (
                job.status == "completed" and status == "completed"
            ):
                logger.debug("Job %s already terminal (%s); update skipped", job_id, job.status)
                return job

            now = time.time()
            if status is not None:
                job.status = status
            if current_step is not None:
                job.current_step = current_step
            if results is not None:
                job.results = results
            if error is not None:
                job.error = error
            if metrics is not None:
                job.metrics = metrics
            if completed:
                if status is None or status == "completed":
                    job.status = "completed"
                job.completed_at = now
            job.updated_at = now

            cur = self.conn.cursor()
            cur.execute(
                """
                UPDATE jobs SET
                    status = ?, current_step = ?, total_steps = ?, definition = ?,
                    results = ?, completed_at = ?, error = ?, metrics = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    job.status,
                    job.current_step,
                    job.total_steps,
                    json.dumps(job.definition, default=str),
                    json.dumps(job.results, default=str),
                    job.completed_at,
                    job.error,
                    json.dumps(job.metrics, default=str) if job.metrics else None,
                    job.updated_at,
                    job.job_id,
                ),
            )
            self.conn.commit()
            cur.close()
            return job

    def record_step_result(
        self,
        job_id: str,
        step_name: str,
        success: bool,
        output: Any = None,
        error: str | None = None,
        auto_complete: bool = True,
    ) -> JobData | None:
        """Record the result of a single step.

        auto_complete marks the job completed once every recorded leaf is in;
        detached workers pass False because their finalize step owns terminal
        status (root-vs-leaf counting misreports conditional/parallel trees).
        """
        with self._lock:
            job = self.get_job(job_id)
            if not job:
                return None
            if job.status in TERMINAL_STATUSES:
                logger.debug(
                    "Job %s already terminal (%s); step result skipped", job_id, job.status
                )
                return job

            now = time.time()
            job.results[step_name] = {
                "success": success,
                "output": output,
                "error": error,
                "timestamp": now,
            }
            job.current_step = len(job.results)
            job.updated_at = now

            failed = sum(1 for r in job.results.values() if not r["success"])
            if failed > 0 and not (
                auto_complete and job.total_steps > 0 and len(job.results) >= job.total_steps
            ):
                # A step failure does not own the lifecycle status: with skip/
                # retry policies the loop keeps running and heartbeats must
                # continue; finalize writes the terminal verdict.
                job.error = error or job.error
                job.status = "in_progress"
            elif failed > 0:
                job.status = "error"
                job.error = error or job.error
            elif auto_complete and job.total_steps > 0 and len(job.results) >= job.total_steps:
                job.status = "completed"
                job.completed_at = now
            else:
                job.status = "in_progress"

            cur = self.conn.cursor()
            cur.execute(
                """
                UPDATE jobs SET
                    status = ?, current_step = ?, results = ?,
                    error = ?, completed_at = ?, updated_at = ?
                WHERE job_id = ?
                """,
                (
                    job.status,
                    job.current_step,
                    json.dumps(job.results, default=str),
                    job.error,
                    job.completed_at,
                    job.updated_at,
                    job.job_id,
                ),
            )
            self.conn.commit()
            cur.close()
            return job

    def list_jobs(
        self,
        loop_name: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[JobData]:
        """List jobs with optional filtering."""
        with self._lock:
            sql = "SELECT * FROM jobs WHERE 1=1"
            params: list[Any] = []
            if loop_name:
                sql += " AND loop_name = ?"
                params.append(loop_name)
            if status:
                sql += " AND status = ?"
                params.append(status)
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cur = self.conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            cur.close()
            return [self._row_to_job(row) for row in rows]

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running or ready job (and its pending HITL questions)."""
        with self._lock:
            job = self.get_job(job_id)
            if not job or job.status in ("completed", "failed", "cancelled"):
                return False
            now = time.time()
            cur = self.conn.cursor()
            cur.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                ("cancelled", now, job_id),
            )
            cancelled = cur.rowcount > 0
            cur.execute(
                "UPDATE messages SET status = 'cancelled' WHERE job_id = ? AND status = 'pending'",
                (job_id,),
            )
            self.conn.commit()
            cur.close()
            return cancelled

    def delete_job(self, job_id: str) -> bool:
        """Delete a job record."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
            deleted = cur.rowcount > 0
            self.conn.commit()
            cur.close()
            return deleted

    def touch_job(self, job_id: str) -> bool:
        """Refresh updated_at for a non-terminal, non-waiting job (heartbeat)."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "UPDATE jobs SET updated_at = ? WHERE job_id = ? "
                "AND status IN ('ready', 'running', 'in_progress')",
                (time.time(), job_id),
            )
            touched = cur.rowcount > 0
            self.conn.commit()
            cur.close()
            return touched

    def _host_pid_alive(self, metrics_json: str | None) -> bool:
        """Check whether the job's owning host process is still alive."""
        if not metrics_json:
            return False
        try:
            pid = json.loads(metrics_json).get("host_pid")
        except (json.JSONDecodeError, AttributeError):
            return False
        return is_pid_alive(pid)

    def mark_interrupted_jobs_on_startup(self) -> int:
        """Mark orphan active jobs as 'interrupted' on startup.

        Jobs whose recorded host_pid is still alive are skipped: duplicate MCP
        server instances share this DB and must not kill each other's live jobs.
        'ready' jobs are only reaped when they carry an explicitly dead
        host_pid (agent-mode jobs); bare 'ready' rows without metrics belong to
        legacy flows and stay untouched.
        """
        interrupted = 0
        with self._lock:
            now = time.time()
            cur = self.conn.cursor()
            cur.execute(
                "SELECT job_id, status, metrics FROM jobs "
                "WHERE status IN ('ready', 'running', 'in_progress', 'waiting_input')"
            )
            rows = cur.fetchall()
            for row in rows:
                if row["status"] == "ready":
                    try:
                        metrics = json.loads(row["metrics"]) if row["metrics"] else {}
                    except json.JSONDecodeError:
                        metrics = {}
                    pid = metrics.get("host_pid")
                    if pid is None or is_pid_alive(pid):
                        continue
                    cur.execute(
                        "UPDATE jobs SET status = 'interrupted', updated_at = ? WHERE job_id = ?",
                        (now, row["job_id"]),
                    )
                    interrupted += cur.rowcount
                    continue
                if self._host_pid_alive(row["metrics"]):
                    continue
                cur.execute(
                    "UPDATE jobs SET status = 'interrupted', updated_at = ? WHERE job_id = ?",
                    (now, row["job_id"]),
                )
                interrupted += cur.rowcount
            self.conn.commit()
            cur.close()
            return interrupted

    def _row_to_job(self, row: sqlite3.Row) -> JobData:
        """Parse SQLite Row into JobData dataclass."""
        definition = json.loads(row["definition"]) if row["definition"] else {}
        results = json.loads(row["results"]) if row["results"] else {}
        metrics = json.loads(row["metrics"]) if row["metrics"] else None
        return JobData(
            job_id=row["job_id"],
            loop_name=row["loop_name"],
            status=row["status"],
            current_step=row["current_step"],
            total_steps=row["total_steps"],
            definition=definition,
            results=results,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            error=row["error"],
            metrics=metrics,
        )
