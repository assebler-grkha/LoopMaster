"""SQLite JobStore — persistent, thread-safe storage for MCP loop jobs."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("loopmaster.mcp.job_store")

SCHEMA_VERSION = 1
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
ACTIVE_STATUSES = frozenset({"ready", "running", "in_progress"})


def is_pid_alive(pid: int | None) -> bool:
    """Check whether a process ID belongs to a live process."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@dataclass
class JobData:
    """Represents a persistent loop execution job."""

    job_id: str
    loop_name: str
    status: str = "ready"  # ready, running, in_progress, completed, error, failed, cancelled
    current_step: int = 0
    total_steps: int = 0
    definition: dict[str, Any] = field(default_factory=dict)
    results: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    completed_at: float | None = None
    error: str | None = None
    metrics: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "job_id": self.job_id,
            "loop_name": self.loop_name,
            "status": self.status,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "definition": self.definition,
            "results": self.results,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "metrics": self.metrics,
        }


@dataclass
class LoopData:
    """Represents a persisted JSON loop spec (LoopStore)."""

    name: str
    version: str
    spec: dict[str, Any] = field(default_factory=dict)
    source_hash: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "spec": self.spec,
            "source_hash": self.source_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class JobStore:
    """Persistent SQLite job store with thread-safety and WAL concurrency."""

    def __init__(self, db_path: str | Path = ".loopmaster/jobs.db") -> None:
        self.db_path = Path(db_path) if str(db_path) != ":memory:" else db_path
        if isinstance(self.db_path, Path):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._init_schema()

    @property
    def conn(self) -> sqlite3.Connection:
        """Get or initialize the thread-safe SQLite connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            if str(self.db_path) != ":memory:":
                cur = self._conn.cursor()
                cur.execute("PRAGMA journal_mode = WAL;")
                cur.execute("PRAGMA synchronous = NORMAL;")
                cur.execute("PRAGMA busy_timeout = 5000;")
                cur.close()
        return self._conn

    def _init_schema(self) -> None:
        """Initialize jobs table and indexes (gated by PRAGMA user_version)."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("PRAGMA user_version;")
            if int(cur.fetchone()[0]) >= SCHEMA_VERSION:
                cur.close()
                return
            cur.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    loop_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_step INTEGER NOT NULL DEFAULT 0,
                    total_steps INTEGER NOT NULL DEFAULT 0,
                    definition TEXT NOT NULL,
                    results TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    error TEXT,
                    metrics TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_loop_name ON jobs(loop_name);
                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
                CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);
                CREATE TABLE IF NOT EXISTS loops (
                    name TEXT PRIMARY KEY,
                    version TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    source_hash TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )
            cur.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
            self.conn.commit()
            cur.close()

    def save_loop(
        self, name: str, version: str, spec: dict[str, Any], source_hash: str = ""
    ) -> LoopData:
        """Insert or replace a loop spec (upsert by name)."""
        now = time.time()
        existing = self.get_loop(name)
        created_at = existing.created_at if existing else now
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO loops (name, version, spec_json, source_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    version = excluded.version,
                    spec_json = excluded.spec_json,
                    source_hash = excluded.source_hash,
                    updated_at = excluded.updated_at
                """,
                (name, version, json.dumps(spec, default=str), source_hash, created_at, now),
            )
            self.conn.commit()
            cur.close()
        return LoopData(
            name=name,
            version=version,
            spec=spec,
            source_hash=source_hash,
            created_at=created_at,
            updated_at=now,
        )

    def get_loop(self, name: str) -> LoopData | None:
        """Fetch a loop spec by name, or None."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM loops WHERE name = ?", (name,))
            row = cur.fetchone()
            cur.close()
        return _row_to_loop(row) if row else None

    def list_loops(self, limit: int = 100) -> list[LoopData]:
        """List persisted loops, newest first."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT * FROM loops ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            )
            rows = cur.fetchall()
            cur.close()
        return [_row_to_loop(r) for r in rows]

    def delete_loop(self, name: str) -> bool:
        """Delete a loop spec. Returns True when a row was removed."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM loops WHERE name = ?", (name,))
            deleted = cur.rowcount > 0
            self.conn.commit()
            cur.close()
        return deleted

    def create_job(
        self,
        job_id: str,
        loop_name: str,
        definition: dict[str, Any],
        status: str = "ready",
        total_steps: int | None = None,
    ) -> JobData:
        """Create a new persistent job."""
        now = time.time()
        steps_count = (
            total_steps
            if total_steps is not None
            else definition.get("step_count", len(definition.get("steps", [])))
        )
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
        )

        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO jobs (
                    job_id, loop_name, status, current_step, total_steps,
                    definition, results, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
    ) -> JobData | None:
        """Record the result of a single step."""
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
            if failed > 0:
                job.status = "error"
                job.error = error or job.error
            elif job.total_steps > 0 and len(job.results) >= job.total_steps:
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
            return [self._row_to_job(r) for r in rows]

    def cancel_job(self, job_id: str) -> bool:
        """Cancel a running or ready job."""
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
            self.conn.commit()
            cur.close()
            return True

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
        """Refresh updated_at for a non-terminal job (worker heartbeat)."""
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
        """Mark orphan 'running'/'in_progress' jobs as 'interrupted' on startup.

        Jobs whose recorded host_pid is still alive are skipped: duplicate MCP
        server instances share this DB and must not kill each other's live jobs.
        """
        interrupted = 0
        with self._lock:
            now = time.time()
            cur = self.conn.cursor()
            cur.execute(
                "SELECT job_id, metrics FROM jobs WHERE status IN ('running', 'in_progress')"
            )
            rows = cur.fetchall()
            for row in rows:
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

    def close(self) -> None:
        """Close the SQLite connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __enter__(self) -> JobStore:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

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


def _row_to_loop(row: sqlite3.Row) -> LoopData:
    """Parse SQLite Row into LoopData dataclass."""
    return LoopData(
        name=row["name"],
        version=row["version"],
        spec=json.loads(row["spec_json"]) if row["spec_json"] else {},
        source_hash=row["source_hash"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_global_store: JobStore | None = None
_store_lock = threading.Lock()


def get_job_store(db_path: str | Path | None = None) -> JobStore:
    """Get or create singleton JobStore instance."""
    global _global_store
    with _store_lock:
        if db_path is not None:
            return JobStore(db_path=db_path)
        if _global_store is None:
            path = os.environ.get("LOOPMASTER_JOBS_DB", ".loopmaster/jobs.db")
            _global_store = JobStore(db_path=path)
        return _global_store
