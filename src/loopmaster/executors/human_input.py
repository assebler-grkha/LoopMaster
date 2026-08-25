"""Human-in-the-Loop executor: blocks the step until an answer arrives.

The executor registers a question row in the messages table, flips the job to
``waiting_input`` and polls for an answer (idempotent per job+step). Timeout
policies mirror the HITL design: default_answer | skip | fail | escalate.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from typing import Any

from loopmaster.executors.base import BaseExecutor, resolve_template_value

TIMEOUT_POLICIES = ("default_answer", "skip", "fail", "escalate")


@dataclass
class HumanInputResult:
    """Outcome of a human-input step."""

    msg_id: str = ""
    resolved: str = "answered"
    answer: Any = None
    success: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "msg_id": self.msg_id,
            "resolved": self.resolved,
            "answer": self.answer,
            "success": self.success,
            "error": self.error,
        }


def _get_store(db_path: str | None):
    from loopmaster.mcp.job_store import JobStore, get_job_store

    if db_path:
        return JobStore(db_path=db_path)
    return get_job_store()


class HumanInputExecutor(BaseExecutor):
    """Asks a human/agent a question and waits for loop_respond."""

    def __init__(
        self,
        step_name: str,
        question: str = "",
        ask_to: str = "agent",
        options: list[str] | None = None,
        timeout: str | None = None,
        default_answer: Any = None,
        on_timeout: str = "default_answer",
        poll_s: float = 0.5,
        db_path: str | None = None,
    ) -> None:
        if on_timeout not in TIMEOUT_POLICIES:
            raise ValueError(f"on_timeout must be one of {TIMEOUT_POLICIES}, got {on_timeout!r}")
        self.step_name = step_name
        self.question = question
        self.ask_to = ask_to
        self.options = list(options or [])
        self.timeout = timeout
        self.default_answer = default_answer
        self.on_timeout = on_timeout
        self.poll_s = poll_s
        self.db_path = db_path

    def _set_status(self, store: Any, job_id: str, status: str) -> None:
        with contextlib.suppress(Exception):
            store.update_job(job_id, status=status)

    def execute(self, ctx_data: dict[str, Any]) -> HumanInputResult:
        """Register the question and block until answered or timeout."""
        from loopmaster.mcp.job_store import parse_duration

        store = _get_store(self.db_path)
        job_id = str(ctx_data.get("__job_id__") or "")
        loop_name = str(ctx_data.get("__loop_name__") or "loop")
        text = str(resolve_template_value(self.question, ctx_data))
        from_addr = f"loop:{loop_name}#{self.step_name}"
        try:
            message = store.create_question(
                job_id=job_id,
                from_addr=from_addr,
                text=text,
                options=self.options,
                timeout_s=parse_duration(self.timeout) if self.timeout else None,
                default_answer=self.default_answer,
                to_addr=self.ask_to,
            )
        except ValueError as exc:
            return HumanInputResult(success=False, error=str(exc))
        if job_id:
            self._set_status(store, job_id, "waiting_input")

        deadline = time.time() + parse_duration(self.timeout) if self.timeout else None
        while True:
            current = store.get_message(message.msg_id)
            if current is not None and current.status == "answered":
                answer = (current.answered or {}).get("answer")
                if job_id:
                    self._set_status(store, job_id, "in_progress")
                return HumanInputResult(msg_id=message.msg_id, answer=answer)
            if current is not None and current.status in ("cancelled",):
                if job_id:
                    self._set_status(store, job_id, "in_progress")
                return HumanInputResult(
                    success=False,
                    msg_id=message.msg_id,
                    error="input cancelled",
                )
            if deadline is not None and time.time() >= deadline:
                break
            time.sleep(self.poll_s)

        final = store.get_message(message.msg_id)
        if final is not None and final.status == "answered":
            answer = (final.answered or {}).get("answer")
            if job_id:
                self._set_status(store, job_id, "in_progress")
            return HumanInputResult(msg_id=message.msg_id, answer=answer)

        error_msg = f"input timeout after {self.timeout} ({self.on_timeout})"
        if self.on_timeout == "escalate":
            return HumanInputResult(
                success=False,
                msg_id=message.msg_id,
                error="escalation requires Phase 5 notifications; treat as timeout",
            )
        if self.on_timeout == "default_answer" and self.default_answer is not None:
            with contextlib.suppress(ValueError):
                store.answer_question(message.msg_id, self.default_answer, by="auto")
            if job_id:
                self._set_status(store, job_id, "in_progress")
            return HumanInputResult(
                msg_id=message.msg_id,
                resolved="auto",
                answer=self.default_answer,
            )
        store.sweep_expired_questions()
        return HumanInputResult(success=False, msg_id=message.msg_id, error=error_msg)

    def to_dict(self) -> dict[str, Any]:
        """Serialize configuration."""
        return {
            "executor": "human_input",
            "step_name": self.step_name,
            "question": self.question,
            "ask_to": self.ask_to,
            "options": self.options,
            "timeout": self.timeout,
            "default_answer": self.default_answer,
            "on_timeout": self.on_timeout,
        }
