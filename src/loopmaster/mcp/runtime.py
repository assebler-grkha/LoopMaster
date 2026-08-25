"""Shared runtime state for the LoopMaster MCP server."""

from __future__ import annotations

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
_user_hooks_loaded = _hooks.load_user_hooks()
