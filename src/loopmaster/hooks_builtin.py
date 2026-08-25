"""Built-in lifecycle hooks (H1-H7).

All hooks are passive unless configured: webhooks require ``LM_NOTIFY_WEBHOOK``,
the budget guard requires ``LM_REQUIRE_BUDGET=1``. Register them with
:func:`loopmaster.hooks.register_builtins`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import sys
import urllib.request
from typing import Any

from loopmaster import hooks
from loopmaster.hooks import HookVeto

logger = logging.getLogger("loopmaster.hooks.builtin")

_CODE_TYPES = {"code"}
_SHELL_TYPES = {"shell"}
_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


def _split_command(command: Any) -> list[str]:
    """Split exactly like ShellExecutor does (shlex parity, A1)."""
    if isinstance(command, list):
        return [str(item) for item in command]
    return shlex.split(str(command), posix=sys.platform != "win32")


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def _exe_basename(token: str) -> str:
    base = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if sys.platform == "win32" and base.endswith(".exe"):
        base = base[:-4]
    return base


def h_shell_allowlist(event: str, payload: dict) -> dict | None:
    """H8: coarse command policy for shell steps via LM_SHELL_ALLOWLIST / LM_SHELL_BLOCKLIST.

    This is a policy gate, not a sandbox: interpreters on the allowlist
    (``python -c`` etc.) remain code-execution-equivalent. All failure paths
    raise :class:`HookVeto` — the hook framework swallows ordinary exceptions,
    so an internal error must never degrade to "allow".
    """
    allow_raw = os.environ.get("LM_SHELL_ALLOWLIST", "")
    block_raw = os.environ.get("LM_SHELL_BLOCKLIST", "")
    allow = None
    if allow_raw.strip():
        allow = {entry.strip().lower() for entry in allow_raw.split(",") if entry.strip()}
        if not allow:
            raise HookVeto("LM_SHELL_ALLOWLIST is set but contains no valid entries")
    block = {entry.strip().lower() for entry in block_raw.split(",") if entry.strip()}
    if allow is None and not block:
        return None
    try:
        return _check_shell_policy(payload.get("spec"), allow, block)
    except HookVeto:
        raise
    except Exception as exc:  # noqa: BLE001 - fail-closed by design
        raise HookVeto(f"shell-allowlist internal error: {exc}") from exc


def _check_shell_policy(spec: Any, allow: set[str] | None, block: set[str]) -> dict:
    from loopmaster.spec.loader import _PLACEHOLDER_RE

    checked = 0
    for node in _walk_nodes((spec or {}).get("steps")):
        if node.get("type") not in _SHELL_TYPES:
            continue
        name = node.get("name")
        if node.get("shell") is True:
            raise HookVeto(
                f"shell step '{name}': shell=true is not permitted while command policy is active"
            )
        tokens = [_unquote(token) for token in _split_command(node.get("command"))]
        if not tokens:
            raise HookVeto(f"shell step '{name}': empty or unparseable command")
        exe = tokens[0]
        if _PLACEHOLDER_RE.search(exe):
            raise HookVeto(f"shell step '{name}': executable '{exe}' is not a static value")
        if allow is not None:
            if "/" in exe or "\\" in exe or _DRIVE_PREFIX_RE.match(exe) or exe.startswith("\\\\"):
                raise HookVeto(
                    f"shell step '{name}': path-separated executable '{exe}' is not permitted"
                )
            base = _exe_basename(exe)
            if base not in allow:
                raise HookVeto(f"shell step '{name}': '{base}' is not in LM_SHELL_ALLOWLIST")
        elif _exe_basename(exe) in block:
            raise HookVeto(f"shell step '{name}': '{_exe_basename(exe)}' is blocked")
        checked += 1
    return {"commands_checked": checked}


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
    """Register H1-H8 under their hook events (H6/H7 run via CLI maintenance)."""
    hooks.register(hooks.BEFORE_LOOP_SAVE, "validate-spec", h_validate_spec)
    hooks.register(hooks.BEFORE_LOOP_SAVE, "verify-blocks", h_verify_blocks)
    hooks.register(hooks.BEFORE_LOOP_SAVE, "shell-allowlist", h_shell_allowlist)
    hooks.register(hooks.BEFORE_LOOP_RUN, "validate-spec", h_validate_spec)
    hooks.register(hooks.BEFORE_LOOP_RUN, "verify-blocks", h_verify_blocks)
    hooks.register(hooks.BEFORE_LOOP_RUN, "shell-allowlist", h_shell_allowlist)
    hooks.register(hooks.BEFORE_LOOP_RUN, "budget-guard", h_budget_guard)
    hooks.register(hooks.NOTIFICATION_CREATED, "notify-dispatcher", h_notify_dispatcher)
    hooks.register(hooks.HITL_ESCALATION, "hitl-escalate", h_hitl_escalate)
