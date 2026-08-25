"""NotificationStore mixin — outbox notifications the agent polls via MCP."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from loopmaster.mcp.store_models import (
    MESSAGE_RETENTION_S,
    NOTIFICATION_PRIORITIES,
    NOTIFICATION_RETENTION_S,
    NotificationData,
    StoreHost,
    row_to_message,
    row_to_notification,
)

logger = logging.getLogger("loopmaster.mcp.notification_store")


class NotificationStoreMixin(StoreHost):
    """CRUD for the notifications table (outbox model, poll-based delivery)."""

    def _critical_fallback_path(self) -> Path | None:
        """Filesystem fallback for critical alerts (works even without MCP)."""
        db_path = getattr(self, "db_path", None)
        if not db_path or str(db_path) == ":memory:":
            return None
        return Path(db_path).parent / "inbox" / "critical.json"

    def _write_critical_fallback(self) -> None:
        """Rewrite .loopmaster/inbox/critical.json with unread critical items."""
        path = self._critical_fallback_path()
        if path is None:
            return
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT * FROM notifications "
                "WHERE priority = 'critical' AND read_by_agent = 0 "
                "ORDER BY created_at DESC LIMIT 50"
            )
            rows = cur.fetchall()
            cur.close()
        payload = [row_to_notification(row).to_dict() for row in rows]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("critical fallback write failed (%s): %s", path, exc)

    def create_notification(
        self,
        priority: str,
        event: str,
        summary: str,
        job_id: str = "",
        detail: dict[str, Any] | None = None,
    ) -> NotificationData:
        """Append a notification (idempotent per job+event+summary; anti-spam).

        Duplicate inserts collapse into the existing row so retried steps or
        repeated events cannot spam the inbox.
        """
        if priority not in NOTIFICATION_PRIORITIES:
            raise ValueError(f"priority must be one of {sorted(NOTIFICATION_PRIORITIES)}")
        if not isinstance(event, str) or not event.strip():
            raise ValueError("event must be a non-empty string")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("summary must be a non-empty string")
        notif_id = hashlib.sha256(f"{job_id}|{event}|{summary}".encode()).hexdigest()[:32]
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO notifications (
                    notif_id, job_id, priority, event, summary,
                    detail_json, read_by_agent, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(notif_id) DO NOTHING
                """,
                (
                    notif_id,
                    job_id,
                    priority,
                    event,
                    summary,
                    json.dumps(detail or {}, ensure_ascii=False),
                    time.time(),
                ),
            )
            self.conn.commit()
            cur.close()
        if priority == "critical":
            with contextlib.suppress(OSError):
                self._write_critical_fallback()
        stored = self.get_notification(notif_id)
        assert stored is not None
        return stored

    def get_notification(self, notif_id: str) -> NotificationData | None:
        """Fetch a single notification by id."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM notifications WHERE notif_id = ?", (notif_id,))
            row = cur.fetchone()
            cur.close()
        return row_to_notification(row) if row is not None else None

    def list_notifications(
        self,
        unread_only: bool = False,
        limit: int = 20,
    ) -> list[NotificationData]:
        """List notifications newest first (unread only when requested)."""
        query = "SELECT * FROM notifications"
        if unread_only:
            query += " WHERE read_by_agent = 0"
        query += " ORDER BY created_at DESC LIMIT ?"
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(query, (max(1, int(limit)),))
            rows = cur.fetchall()
            cur.close()
        return [row_to_notification(row) for row in rows]

    def mark_notifications_read(self, notif_ids: list[str] | None = None) -> int:
        """Mark specific notifications (or all unread) as read. Returns count."""
        with self._lock:
            cur = self.conn.cursor()
            if notif_ids:
                marks = 0
                for nid in notif_ids:
                    cur.execute(
                        "UPDATE notifications SET read_by_agent = 1 "
                        "WHERE notif_id = ? AND read_by_agent = 0",
                        (nid,),
                    )
                    marks += max(0, cur.rowcount)
            else:
                cur.execute("UPDATE notifications SET read_by_agent = 1 WHERE read_by_agent = 0")
                marks = max(0, cur.rowcount)
            self.conn.commit()
            cur.close()
        if marks:
            self._write_critical_fallback()
        return marks

    def mark_job_notifications_read(self, job_id: str, event: str | None = None) -> int:
        """Mark a job's unread notifications read (e.g. after loop_respond)."""
        query = "UPDATE notifications SET read_by_agent = 1 WHERE job_id = ? AND read_by_agent = 0"
        params: list[Any] = [job_id]
        if event is not None:
            query += " AND event = ?"
            params.append(event)
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(query, params)
            marked = max(0, cur.rowcount)
            self.conn.commit()
            cur.close()
        if marked:
            self._write_critical_fallback()
        return marked

    def pending_notification_counts(self) -> dict[str, int]:
        """Unread counts by priority — attached to every MCP tool response."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT priority, COUNT(*) AS n FROM notifications "
                "WHERE read_by_agent = 0 GROUP BY priority"
            )
            counts = {row["priority"]: int(row["n"]) for row in cur.fetchall()}
            cur.close()
        return {
            "info": counts.get("info", 0),
            "needs_input": counts.get("needs_input", 0),
            "critical": counts.get("critical", 0),
        }

    def sweep_old_notifications(self, retention_s: float = NOTIFICATION_RETENTION_S) -> int:
        """Delete read notifications older than retention. Returns count removed."""
        cutoff = time.time() - retention_s
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "DELETE FROM notifications WHERE read_by_agent = 1 AND created_at < ?",
                (cutoff,),
            )
            removed = max(0, cur.rowcount)
            self.conn.commit()
            cur.close()
        if removed:
            self._write_critical_fallback()
        return removed

    def sweep_old_messages(
        self, archive_dir: Path | None = None, retention_s: float = MESSAGE_RETENTION_S
    ) -> int:
        """Archive non-pending messages older than retention to JSONL, then delete.

        Archive lines go to ``<db_dir>/archive/messages-YYYYMM.jsonl``.
        """
        cutoff = time.time() - retention_s
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "SELECT * FROM messages WHERE status != 'pending' AND created_at < ? LIMIT 1000",
                (cutoff,),
            )
            rows = cur.fetchall()
            if rows:
                cur.execute(
                    f"DELETE FROM messages WHERE msg_id IN ({', '.join('?' for _ in rows)})",
                    [row["msg_id"] for row in rows],
                )
            self.conn.commit()
            cur.close()
        if not rows:
            return 0
        if archive_dir is None:
            db_path = getattr(self, "db_path", None)
            archive_dir = (
                Path(db_path).parent / "archive" if db_path and str(db_path) != ":memory:" else None
            )
        if archive_dir is not None:
            stamp = time.strftime("%Y%m")
            try:
                archive_dir.mkdir(parents=True, exist_ok=True)
                target = archive_dir / f"messages-{stamp}.jsonl"
                with open(target, "a", encoding="utf-8") as fh:
                    for row in rows:
                        fh.write(json.dumps(row_to_message(row).to_dict(), ensure_ascii=False))
                        fh.write("\n")
            except OSError as exc:
                logger.warning("message archive write failed (%s): %s", archive_dir, exc)
        return len(rows)
