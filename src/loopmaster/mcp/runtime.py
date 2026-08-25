"""Shared runtime state for the LoopMaster MCP server."""

from __future__ import annotations

import os
import threading

from fastmcp import FastMCP

from loopmaster import hooks as _hooks
from loopmaster.hooks_builtin import register_builtins
from loopmaster.mcp.job_store import get_job_store
from loopmaster.mcp.worker import DetachedRunner

mcp = FastMCP(
    name="loopmaster",
    instructions=(
        "LoopMaster provides AI agent loop definitions and execution. "
        "Use loop_list to discover loops, loop_run to execute loops via LoopEngine, "
        "or loop_get + loop_result for step-by-step agent execution."
    ),
)

store = get_job_store()
store.mark_interrupted_jobs_on_startup()

runner = DetachedRunner(store)

cancel_events: dict[str, threading.Event] = {}

register_builtins()

# User hooks execute arbitrary code from the workspace; loading them is an
# explicit opt-in so a cloned repo cannot gain code execution just by being
# opened in the agent's CWD.
_user_hooks_loaded = 0
if os.environ.get("LOOPMASTER_LOAD_HOOKS", "").strip().lower() in ("1", "true", "yes"):
    _user_hooks_loaded = _hooks.load_user_hooks()
    if _user_hooks_loaded:
        import logging

        logging.getLogger("loopmaster.mcp.runtime").warning(
            "Loaded %d user hook(s) from .loopmaster/hooks.py (LOOPMASTER_LOAD_HOOKS is enabled)",
            _user_hooks_loaded,
        )
