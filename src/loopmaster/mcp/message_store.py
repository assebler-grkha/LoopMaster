"""MessageStore mixin — HITL questions/answers (idempotent, poison-guarded)."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from loopmaster.mcp.store_models import (
    _POISON_REF_RE,
    MESSAGE_STATUSES,
    MessageData,
    StoreHost,
    row_to_message,
)


class MessageStoreMixin(StoreHost):
    """CRUD for the messages table."""

    @staticmethod
    def _guard_poison_refs(texts: list[str]) -> None:
        """Reject template-like placeholders that SKIP would leave as literals."""
        for text in texts:
            if text and _POISON_REF_RE.search(text):
                raise ValueError(
                    f"placeholder {text!r} is not allowed here: "
                    "on timeout it would stay a literal — pass plain text or use default_answer"
                )

    def create_question(
        self,
        job_id: str,
        from_addr: str,
        text: str,
        options: list[str] | None = None,
        timeout_s: float | None = None,
        default_answer: Any = None,
        to_addr: str = "agent",
    ) -> MessageData:
        """Register a HITL question (idempotent per job_id+from_addr)."""
        poison_candidates = [text]
        if default_answer is not None:
            poison_candidates.append(str(default_answer))
        self._guard_poison_refs(poison_candidates)
        for opt in options or []:
            self._guard_poison_refs([str(opt)])
        msg_id = hashlib.sha256(f"{job_id}|{from_addr}".encode()).hexdigest()
        now = time.time()
        expires_at = (now + timeout_s) if timeout_s else None
        payload = {"text": str(text), "options": list(options or [])}
        if default_answer is not None:
            payload["default_answer"] = default_answer
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                """
                INSERT INTO messages (
                    msg_id, job_id, from_addr, to_addr, type,
                    payload_json, status, created_at, expires_at
                ) VALUES (?, ?, ?, ?, 'question', ?, 'pending', ?, ?)
                ON CONFLICT(msg_id) DO NOTHING
                """,
                (
                    msg_id,
                    job_id,
                    from_addr,
                    to_addr,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    expires_at,
                ),
            )
            self.conn.commit()
            cur.close()
        message = self.get_message(msg_id)
        assert message is not None
        return message

    def answer_question(self, msg_id: str, answer: Any, by: str = "agent") -> MessageData:
        """Answer a pending question; raises ValueError on already-answered/expired."""
        answered = {"answer": answer, "by": by}
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "UPDATE messages SET status = 'answered', answered_json = ? "
                "WHERE msg_id = ? AND status = 'pending'",
                (json.dumps(answered, ensure_ascii=False), msg_id),
            )
            updated = cur.rowcount > 0
            self.conn.commit()
            cur.close()
        message = self.get_message(msg_id)
        if message is None:
            raise KeyError(f"message '{msg_id}' not found")
        if not updated:
            raise ValueError(
                f"already_{message.status}"
                if message.status != "pending"
                else f"message '{msg_id}' is still pending"
            )
        return message

    def get_message(self, msg_id: str) -> MessageData | None:
        """Fetch a single message by id."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute("SELECT * FROM messages WHERE msg_id = ?", (msg_id,))
            row = cur.fetchone()
            cur.close()
        return row_to_message(row) if row is not None else None

    def sweep_expired_questions(self, now: float | None = None) -> int:
        """Mark expired pending questions; returns how many were swept."""
        moment = time.time() if now is None else now
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "UPDATE messages SET status = 'expired' "
                "WHERE status = 'pending' AND expires_at IS NOT NULL AND expires_at < ?",
                (moment,),
            )
            swept = cur.rowcount
            self.conn.commit()
            cur.close()
            return swept

    def list_questions(
        self,
        job_id: str | None = None,
        status: str = "pending",
    ) -> list[MessageData]:
        """List messages of type question (pending by default), oldest first."""
        if status not in MESSAGE_STATUSES:
            raise ValueError(f"invalid message status {status!r}")
        query = "SELECT * FROM messages WHERE type = 'question' AND status = ?"
        params: list[Any] = [status]
        if job_id is not None:
            query += " AND job_id = ?"
            params.append(job_id)
        query += " ORDER BY created_at ASC LIMIT 200"
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(query, params)
            rows = cur.fetchall()
            cur.close()
        return [row_to_message(row) for row in rows]

    def cancel_pending_messages(self, job_id: str) -> int:
        """Cancel all pending messages of a job."""
        with self._lock:
            cur = self.conn.cursor()
            cur.execute(
                "UPDATE messages SET status = 'cancelled' WHERE job_id = ? AND status = 'pending'",
                (job_id,),
            )
            cancelled = cur.rowcount
            self.conn.commit()
            cur.close()
            return cancelled
