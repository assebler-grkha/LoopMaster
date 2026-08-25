"""LoopStore mixin — persisted JSON loop specs (upsert by name)."""

from __future__ import annotations

import json
import time
from typing import Any

from loopmaster.mcp.store_models import LoopData, StoreHost, row_to_loop


class LoopStoreMixin(StoreHost):
    """CRUD for the loops table."""

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
        return row_to_loop(row) if row else None

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
        return [row_to_loop(row) for row in rows]

    def delete_loop(self, name: str) -> bool:
        """Delete a loop spec. Returns True when a row was removed."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("DELETE FROM loops WHERE name = ?", (name,))
            deleted = cur.rowcount > 0
            self.conn.commit()
            cur.close()
        return deleted
