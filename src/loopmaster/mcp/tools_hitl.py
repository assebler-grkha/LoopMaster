"""HITL MCP tools: question listing and answering."""

from __future__ import annotations

import json
from typing import Any

import loopmaster.mcp.runtime as rt
from loopmaster.mcp.job_store import MessageData
from loopmaster.mcp.runtime import mcp


def _question_view(message: MessageData) -> dict[str, Any]:
    """Compact JSON view of a pending question for MCP responses."""
    return {
        "msg_id": message.msg_id,
        "job_id": message.job_id,
        "from_addr": message.from_addr,
        "text": (message.payload or {}).get("text", ""),
        "options": (message.payload or {}).get("options", []),
        "default_answer": (message.payload or {}).get("default_answer"),
        "created_at": message.created_at,
        "expires_at": message.expires_at,
    }


@mcp.tool()
def loop_questions(job_id: str | None = None) -> str:
    """List open HITL questions (pending), sweeping expired ones first."""
    try:
        rt.store.sweep_expired_questions()
        questions = [
            _question_view(m) for m in rt.store.list_questions(job_id=job_id if job_id else None)
        ]
        return json.dumps({"count": len(questions), "questions": questions}, indent=2)
    except Exception as exc:  # noqa: BLE001 - tool boundary returns errors as text
        return f"Error listing questions: {exc}"


@mcp.tool()
def loop_respond(job_id: str, msg_id: str, answer) -> str:
    """Answer a pending HITL question so the waiting loop can resume."""

    try:
        message = rt.store.answer_question(msg_id, answer, by="agent")
    except KeyError:
        return f"Error: Message '{msg_id}' not found."
    except ValueError as exc:
        text = str(exc)
        if "already_answered" in text:
            return f"Error: already_answered — question '{msg_id}' was answered earlier."
        if "already_expired" in text:
            return f"Error: already_expired — question '{msg_id}' timed out; see loop_status."
        if "already_cancelled" in text:
            return f"Error: already_cancelled — job '{job_id}' was cancelled."
        return f"Error: {text}"
    return json.dumps(
        {"responded": True, "msg_id": msg_id, "job_id": message.job_id, "status": "answered"},
        indent=2,
    )
