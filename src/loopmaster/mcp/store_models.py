"""Shared models, constants, and helpers for the SQLite-backed stores."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = 3
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
ACTIVE_STATUSES = frozenset({"ready", "running", "in_progress", "waiting_input"})
BLOCK_LANGUAGES = frozenset({"python", "shell"})
MESSAGE_STATUSES = frozenset({"pending", "answered", "expired", "cancelled"})

_CAPABILITY_RE = re.compile(r"^net$|^fs:(read|write):.+$")
_CODE_REF_RE = re.compile(r"^[a-z][a-z0-9-]*@\d+\.\d+\.\d+$")
_CODE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_POISON_REF_RE = re.compile(r"\{[a-zA-Z_]\w*\}")
_DURATION_RE = re.compile(r"(\d+)([smhd])")
_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class StoreHost:
    """Contract required by the store mixins (provided by JobStore)."""

    _lock: threading.RLock

    @property
    def conn(self) -> sqlite3.Connection:
        raise NotImplementedError


def _split_block_ref(ref: str) -> tuple[str, str | None]:
    """Split a 'name@X.Y.Z' ref into (name, version); plain name -> version None."""
    if isinstance(ref, str) and _CODE_REF_RE.match(ref):
        name, _, version = ref.partition("@")
        return name, version
    return ref, None


def parse_duration(text: str) -> float:
    """Parse a compound duration string like '1h30m' into seconds."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("duration must be a non-empty string like '30s', '24h' or '1h30m'")
    matches = list(_DURATION_RE.finditer(text))
    if not matches:
        raise ValueError(f"invalid duration {text!r}: expected chunks like 10s/5m/2h/1d")
    covered = sum(len(m.group(0)) for m in matches)
    if covered != len(text.replace(" ", "")):
        raise ValueError(f"invalid duration {text!r}: unexpected characters")
    total = 0.0
    seen_units: set[str] = set()
    for match in matches:
        unit = match.group(2)
        if unit in seen_units:
            raise ValueError(f"invalid duration {text!r}: repeated unit {unit!r}")
        seen_units.add(unit)
        total += int(match.group(1)) * _DURATION_UNITS[unit]
    return float(total)


def is_pid_alive(pid: int | None) -> bool:
    """Check whether a process ID belongs to a live process."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, 0, pid)
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


@dataclass
class CodeBlockData:
    """Represents an immutable code block (name, version) with pinned sha256."""

    name: str
    version: str
    language: str
    source: str
    sha256: str = ""
    entrypoint: str = "main"
    capabilities: list[str] = field(default_factory=list)
    description: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self, include_source: bool = True) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary (source optional)."""
        data: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "language": self.language,
            "entrypoint": self.entrypoint,
            "sha256": self.sha256,
            "capabilities": list(self.capabilities),
            "description": self.description,
            "created_at": self.created_at,
        }
        if include_source:
            data["source"] = self.source
        return data


@dataclass
class MessageData:
    """A HITL protocol message (question/answer) routed between loop and agent."""

    msg_id: str
    job_id: str
    from_addr: str
    to_addr: str
    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    answered: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "msg_id": self.msg_id,
            "job_id": self.job_id,
            "from_addr": self.from_addr,
            "to_addr": self.to_addr,
            "type": self.type,
            "payload": self.payload,
            "status": self.status,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "answered": self.answered,
        }


def row_to_loop(row: sqlite3.Row) -> LoopData:
    """Parse SQLite Row into LoopData dataclass."""
    return LoopData(
        name=row["name"],
        version=row["version"],
        spec=json.loads(row["spec_json"]) if row["spec_json"] else {},
        source_hash=row["source_hash"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def row_to_code_block(row: sqlite3.Row) -> CodeBlockData:
    """Parse SQLite Row into CodeBlockData dataclass."""
    try:
        caps = json.loads(row["capabilities_json"]) if row["capabilities_json"] else []
    except (json.JSONDecodeError, TypeError):
        caps = []
    return CodeBlockData(
        name=row["name"],
        version=row["version"],
        language=row["language"],
        source=row["source"] or "",
        sha256=row["sha256"] or "",
        entrypoint=row["entrypoint"] or "main",
        capabilities=[str(c) for c in caps],
        description=row["description"] or "",
        created_at=row["created_at"],
    )


def row_to_message(row: sqlite3.Row) -> MessageData:
    """Parse SQLite Row into MessageData dataclass."""
    try:
        payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    answered: dict[str, Any] | None
    if row["answered_json"]:
        try:
            answered = json.loads(row["answered_json"])
        except (json.JSONDecodeError, TypeError):
            answered = None
    else:
        answered = None
    return MessageData(
        msg_id=row["msg_id"],
        job_id=row["job_id"],
        from_addr=row["from_addr"],
        to_addr=row["to_addr"],
        type=row["type"],
        payload=payload,
        status=row["status"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        answered=answered,
    )
