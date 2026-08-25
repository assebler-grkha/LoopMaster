#!/usr/bin/env python3
"""LoopMaster MCP Server — thin launcher for the packaged server.

The implementation lives in ``loopmaster.mcp`` (server.py + tools_* modules).
This shim keeps the historical launch command and import path working:

    python scripts/loopmaster_mcp.py
    from scripts.loopmaster_mcp import loop_run, loop_status, ...
"""

from __future__ import annotations

from loopmaster.mcp.runtime import mcp
from loopmaster.mcp.server import main
from loopmaster.mcp.tools_blocks import block_add, block_get, block_list
from loopmaster.mcp.tools_hitl import loop_questions, loop_respond
from loopmaster.mcp.tools_loops import (
    loop_cancel,
    loop_delete,
    loop_get,
    loop_list,
    loop_result,
    loop_save,
    loop_status,
)
from loopmaster.mcp.tools_run import loop_run

__all__ = [
    "main",
    "mcp",
    "block_add",
    "block_get",
    "block_list",
    "loop_cancel",
    "loop_delete",
    "loop_get",
    "loop_list",
    "loop_questions",
    "loop_result",
    "loop_respond",
    "loop_run",
    "loop_save",
    "loop_status",
]

if __name__ == "__main__":
    main()
