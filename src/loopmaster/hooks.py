"""Lifecycle hooks for the JSON loop engine.

A hook is any callable ``(event: str, payload: dict) -> dict | None`` registered
under an event name. Hooks never break the engine: exceptions are logged and
swallowed by :func:`trigger`. A hook may veto an operation by raising
:class:`HookVeto` — the caller turns that into a clean error for the user.

Built-ins live in :mod:`loopmaster.hooks_builtin` and are registered lazily via
:func:`register_builtins`. User hooks can be loaded from ``.loopmaster/hooks.py``
via :func:`load_user_hooks`.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger("loopmaster.hooks")

BEFORE_LOOP_SAVE = "before_loop_save"
BEFORE_LOOP_RUN = "before_loop_run"
AFTER_LOOP_RUN = "after_loop_run"
NOTIFICATION_CREATED = "notification_created"
HITL_ESCALATION = "hitl_escalation"

EVENTS = (
    BEFORE_LOOP_SAVE,
    BEFORE_LOOP_RUN,
    AFTER_LOOP_RUN,
    NOTIFICATION_CREATED,
    HITL_ESCALATION,
)


class HookVeto(Exception):  # noqa: N818 - veto is not an error condition
    """Raised by a hook to reject an operation; message surfaces to the user."""


HookFn = Callable[[str, dict], "dict | None"]

_registry: dict[str, list[tuple[str, HookFn]]] = {}
_lock = threading.RLock()


def register(event: str, name: str, fn: HookFn) -> None:
    """Register ``fn`` under ``event`` with a unique ``name`` (re-register replaces)."""
    if event not in EVENTS:
        raise ValueError(f"unknown hook event '{event}'; known: {', '.join(EVENTS)}")
    with _lock:
        bucket = _registry.setdefault(event, [])
        bucket[:] = [(n, f) for n, f in bucket if n != name]
        bucket.append((name, fn))


def unregister(event: str, name: str) -> bool:
    with _lock:
        bucket = _registry.get(event, [])
        remaining = [(n, f) for n, f in bucket if n != name]
        if len(remaining) == len(bucket):
            return False
        _registry[event] = remaining
        return True


def get_registry() -> dict[str, list[str]]:
    """Snapshot of ``event -> [hook names]`` for introspection/CLI."""
    with _lock:
        return {ev: [n for n, _f in hs] for ev, hs in sorted(_registry.items())}


def clear() -> None:
    with _lock:
        _registry.clear()


def trigger(event: str, payload: dict | None = None) -> list[dict]:
    """Run all hooks for ``event``; returns per-hook result dicts.

    Exceptions are logged and recorded as ``{"hook": name, "error": str(exc)}``;
    only :class:`HookVeto` propagates to the caller.
    """
    payload = payload or {}
    results: list[dict] = []
    with _lock:
        hooks = list(_registry.get(event, []))
    for name, fn in hooks:
        try:
            out = fn(event, dict(payload))
            results.append({"hook": name, **(out if isinstance(out, dict) else {})})
        except HookVeto as exc:
            raise HookVeto(f"[{name}] {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - hooks must not break the engine
            logger.warning("hook '%s' failed on %s: %s", name, event, exc)
            results.append({"hook": name, "error": str(exc)})
    return results


def load_user_hooks(path: str | Path = ".loopmaster/hooks.py") -> int:
    """Execute a user hooks module; it calls ``register()`` itself. Returns count loaded."""
    p = Path(path)
    if not p.is_file():
        return 0
    spec = importlib.util.spec_from_file_location(f"_lm_user_hooks_{p.stem}", p)
    if spec is None or spec.loader is None:
        logger.warning("cannot load user hooks from %s", p)
        return 0
    module = importlib.util.module_from_spec(spec)
    before = sum(len(v) for v in get_registry().values())
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        logger.warning("user hooks module %s failed: %s", p, exc)
        return 0
    after = sum(len(v) for v in get_registry().values())
    return max(0, after - before)
