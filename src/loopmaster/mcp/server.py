"""LoopMaster MCP server (packaged entry point).

Importing this module registers all tools on the shared FastMCP app.
Run with `python -m loopmaster.mcp.server` or via scripts/loopmaster_mcp.py.
"""

from __future__ import annotations

import json
import logging

import loopmaster.mcp.tools_blocks  # noqa: F401  (side-effect: registers tools)
import loopmaster.mcp.tools_hitl  # noqa: F401
import loopmaster.mcp.tools_loops  # noqa: F401
import loopmaster.mcp.tools_run  # noqa: F401
from loopmaster.mcp.models_tools import handle_model_list, handle_model_recommend
from loopmaster.mcp.runtime import mcp


@mcp.tool()
def model_list() -> str:
    """List all registered and approved LLM models, semantic aliases, and pricing."""
    return json.dumps(handle_model_list(), indent=2)


@mcp.tool()
def model_recommend(
    task: str = "", prompt_tokens: int = 0, remaining_budget: float | None = None
) -> str:
    """Recommend the optimal approved model based on task complexity and budget constraints."""
    return json.dumps(handle_model_recommend(task, prompt_tokens, remaining_budget), indent=2)


def main() -> None:
    """Run the MCP server over stdio."""
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
