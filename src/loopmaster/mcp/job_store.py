"""SQLite JobStore — persistent, thread-safe storage for MCP loop jobs.

Composition root: schema/ownership lives here, table CRUD is split into
mixins (job_ops, loop_store, code_store, message_store) and shared models
into store_models. This module re-exports the public surface so existing
``from loopmaster.mcp.job_store import ...`` imports keep working.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from loopmaster.mcp.code_store import CodeBlockStoreMixin
from loopmaster.mcp.job_ops import JobOpsMixin
from loopmaster.mcp.loop_store import LoopStoreMixin
from loopmaster.mcp.message_store import MessageStoreMixin
from loopmaster.mcp.notification_store import NotificationStoreMixin
from loopmaster.mcp.store_models import (  # noqa: F401  (re-exports)
    ACTIVE_STATUSES,
    BLOCK_LANGUAGES,
    MESSAGE_RETENTION_S,
    MESSAGE_STATUSES,
    NOTIFICATION_PRIORITIES,
    NOTIFICATION_RETENTION_S,
    SCHEMA_VERSION,
    TERMINAL_STATUSES,
    CodeBlockData,
    JobData,
    LoopData,
    MessageData,
    NotificationData,
    _split_block_ref,
    is_pid_alive,
    parse_duration,
)

__all__ = [
    "ACTIVE_STATUSES",
    "BLOCK_LANGUAGES",
    "CodeBlockData",
    "JobData",
    "JobStore",
    "LoopData",
    "MESSAGE_RETENTION_S",
    "MESSAGE_STATUSES",
    "MessageData",
    "NOTIFICATION_PRIORITIES",
    "NOTIFICATION_RETENTION_S",
    "NotificationData",
    "SCHEMA_VERSION",
    "TERMINAL_STATUSES",
    "_split_block_ref",
    "get_job_store",
    "is_pid_alive",
    "parse_duration",
]

SCHEMA_DDL = """
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
CREATE TABLE IF NOT EXISTS code_blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    language TEXT NOT NULL,
    entrypoint TEXT NOT NULL DEFAULT 'main',
    source TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    description TEXT,
    created_at REAL NOT NULL,
    UNIQUE(name, version)
);
CREATE INDEX IF NOT EXISTS idx_code_blocks_name ON code_blocks(name);
CREATE TABLE IF NOT EXISTS messages (
    msg_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    from_addr TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    reply_to TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL,
    expires_at REAL,
    answered_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_job ON messages(job_id);
CREATE INDEX IF NOT EXISTS idx_messages_inbox ON messages(to_addr, status);
CREATE TABLE IF NOT EXISTS notifications (
    notif_id TEXT PRIMARY KEY,
    job_id TEXT,
    priority TEXT NOT NULL,
    event TEXT NOT NULL,
    summary TEXT NOT NULL,
    detail_json TEXT,
    read_by_agent INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notif_unread ON notifications(read_by_agent, priority);
"""


class JobStore(
    CodeBlockStoreMixin,
    JobOpsMixin,
    LoopStoreMixin,
    MessageStoreMixin,
    NotificationStoreMixin,
):
    """Persistent SQLite job store with thread-safety and WAL concurrency."""

    def __init__(self, db_path: str | Path = ".loopmaster/jobs.db") -> None:
        self.db_path = Path(db_path) if str(db_path) != ":memory:" else db_path
        if isinstance(self.db_path, Path):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        with self._lock:
            self._conn = self._open_conn()
        self._init_schema()

    def _open_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        if str(self.db_path) != ":memory:":
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode = WAL;")
            cur.execute("PRAGMA synchronous = NORMAL;")
            cur.execute("PRAGMA busy_timeout = 5000;")
            cur.close()
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        """Get the thread-safe SQLite connection (opened eagerly in __init__)."""
        if self._conn is None:
            with self._lock:
                if self._conn is None:
                    self._conn = self._open_conn()
        return self._conn

    def _init_schema(self) -> None:
        """Initialize tables and indexes (gated by PRAGMA user_version)."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("PRAGMA user_version;")
            if int(cur.fetchone()[0]) >= SCHEMA_VERSION:
                cur.close()
                return
            cur.executescript(SCHEMA_DDL)
            cur.execute(f"PRAGMA user_version = {SCHEMA_VERSION};")
            self.conn.commit()
            cur.close()

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
