"""Built-in lifecycle hooks (H1-H7).

All hooks are passive unless configured: webhooks require ``LM_NOTIFY_WEBHOOK``,
the budget guard requires ``LM_REQUIRE_BUDGET=1``. Register them with
:func:`loopmaster.hooks.register_builtins`.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any

from loopmaster import hooks
from loopmaster.hooks import HookVeto

logger = logging.getLogger("loopmaster.hooks.builtin")

_CODE_TYPES = {"code"}


def _walk_nodes(nodes: Any):
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, dict):
            continue
        yield node
        ntype = node.get("type")
        if ntype == "parallel":
            yield from _walk_nodes(node.get("steps"))
        elif ntype == "conditional":
            yield from _walk_nodes(node.get("then"))
            yield from _walk_nodes(node.get("else"))


def _webhook(payload: dict) -> None:
    url = os.environ.get("LM_NOTIFY_WEBHOOK", "").strip()
    if not url:
        return
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5).close()
    except Exception as exc:  # noqa: BLE001 - delivery is best-effort
        logger.warning("webhook delivery failed: %s", exc)


def h_validate_spec(event: str, payload: dict) -> dict | None:
    """H1: full LoopSpec validation before save/run."""
    from loopmaster.spec.loader import SPEC_VERSION, validate_loop_spec

    spec = payload.get("spec")
    if not isinstance(spec, dict):
        return None
    if spec.get("loopmaster") != SPEC_VERSION:
        raise HookVeto(f"'loopmaster' marker must be {SPEC_VERSION!r}")
    errors = validate_loop_spec(spec)
    if errors:
        raise HookVeto("invalid spec: " + "; ".join(errors[:10]))
    return {"checked": True}


def h_verify_blocks(event: str, payload: dict) -> dict | None:
    """H2: referenced code blocks exist, sha256 pin matches, capabilities allowed."""
    spec = payload.get("spec") or {}
    store = payload.get("store")
    deny = set(spec.get("deny_capabilities") or [])
    checked = 0
    for node in _walk_nodes(spec.get("steps")):
        if node.get("type") not in _CODE_TYPES:
            continue
        ref = node.get("ref", "")
        name, _, version = ref.partition("@")
        block = store.get_code_block(ref) if store is not None else None
        if block is None:
            raise HookVeto(f"code step '{node.get('name')}': unknown block '{ref}'")
        pinned = node.get("sha256")
        if pinned and pinned != block.sha256:
            raise HookVeto(f"code step '{node.get('name')}': sha256 mismatch for {name}@{version}")
        forbidden = deny.intersection(block.capabilities)
        if forbidden:
            raise HookVeto(
                f"block {name}@{version} needs forbidden capabilities: {sorted(forbidden)}"
            )
        checked += 1
    return {"blocks_checked": checked}


def h_budget_guard(event: str, payload: dict) -> dict | None:
    """H3: refuse to run specs without a budget when LM_REQUIRE_BUDGET=1."""
    if os.environ.get("LM_REQUIRE_BUDGET", "").strip().lower() not in ("1", "true", "yes"):
        return None
    spec = payload.get("spec") or {}
    if not isinstance(spec.get("budget"), dict):
        raise HookVeto("LM_REQUIRE_BUDGET is set but this spec has no 'budget' section")
    return {"budget_required": True}


def h_notify_dispatcher(event: str, payload: dict) -> dict | None:
    """H4: push critical notifications to LM_NOTIFY_WEBHOOK (inbox handles the rest)."""
    if payload.get("priority") != "critical":
        return None
    _webhook({k: v for k, v in payload.items() if k != "store"})
    return {"dispatched": True}


def h_hitl_escalate(event: str, payload: dict) -> dict | None:
    """H5: custom escalation channel for unanswered human inputs."""
    _webhook({"event": "hitl_escalation", **{k: v for k, v in payload.items() if k != "store"}})
    return {"dispatched": True}


def h_stale_reaper(store: Any) -> dict:
    """H6: mark jobs of dead hosts as interrupted (CLI maintenance / cron)."""
    reaped = store.mark_interrupted_jobs_on_startup()
    return {"reaped": len(reaped) if isinstance(reaped, list) else int(reaped or 0)}


def h_archive_sweeper(store: Any) -> dict:
    """H7: retention sweeps - read notifications >7d, messages >30d -> archive."""
    archived_notifications = store.sweep_old_notifications()
    db_dir = getattr(store, "db_path", None)
    archive_dir = None
    if db_path := getattr(db_dir, "parent", None):
        archive_dir = db_path / "archive"
    archived_messages = store.sweep_old_messages(archive_dir=archive_dir)
    return {
        "notifications_removed": archived_notifications,
        "messages_archived": archived_messages,
    }


def register_builtins() -> None:
    """Register H1-H5 under their hook events (H6/H7 run via CLI maintenance)."""
    hooks.register(hooks.BEFORE_LOOP_SAVE, "validate-spec", h_validate_spec)
    hooks.register(hooks.BEFORE_LOOP_SAVE, "verify-blocks", h_verify_blocks)
    hooks.register(hooks.BEFORE_LOOP_RUN, "validate-spec", h_validate_spec)
    hooks.register(hooks.BEFORE_LOOP_RUN, "verify-blocks", h_verify_blocks)
    hooks.register(hooks.BEFORE_LOOP_RUN, "budget-guard", h_budget_guard)
    hooks.register(hooks.NOTIFICATION_CREATED, "notify-dispatcher", h_notify_dispatcher)
    hooks.register(hooks.HITL_ESCALATION, "hitl-escalate", h_hitl_escalate)
