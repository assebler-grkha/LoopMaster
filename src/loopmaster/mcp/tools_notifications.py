"""Notification MCP tools: agent-facing inbox polling (outbox model)."""

from __future__ import annotations

import contextlib
import json
from typing import Any

import loopmaster.mcp.runtime as rt
from loopmaster.mcp.runtime import mcp


def pending_notifications() -> dict[str, int]:
    """Unread notification counts by priority (attached to tool responses)."""
    try:
        return rt.store.pending_notification_counts()
    except Exception:  # noqa: BLE001 - marker must never break a tool response
        return {"info": 0, "needs_input": 0, "critical": 0}


def with_pending(payload: dict[str, Any]) -> dict[str, Any]:
    """Attach the pending_notifications marker to a JSON payload."""
    payload["pending_notifications"] = pending_notifications()
    return payload


@mcp.tool()
def loop_inbox(unread_only: bool = True, limit: int = 20, mark_read: bool = True) -> str:
    """Read outbox notifications (poll-based; never blocks your work).

    Priorities: info (ignore until convenient), needs_input (answer open
    questions via loop_respond), critical (handle in the current session).
    """
    try:
        with contextlib.suppress(Exception):
            rt.store.sweep_old_notifications()
        items = rt.store.list_notifications(unread_only=unread_only, limit=limit)
        notifications = [n.to_dict() for n in items]
        if mark_read and notifications:
            rt.store.mark_notifications_read([n["notif_id"] for n in notifications])
        return json.dumps(
            with_pending({"count": len(notifications), "notifications": notifications}),
            indent=2,
        )
    except Exception as exc:  # noqa: BLE001 - tool boundary returns errors as text
        return f"Error listing notifications: {exc}"
