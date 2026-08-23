#!/usr/bin/env python3
"""CLI one-shot tool caller for LoopMaster ShellExecutor.

Bridges ShellExecutor → mcp_bridge.py functions.

Usage:
    python tests/mcp_tool.py search_graph --name_pattern ".*Order.*"
    python tests/mcp_tool.py get_snippet --qualified_name "pkg.Module"
    python tests/mcp_tool.py aislop_scan --path .
    python tests/mcp_tool.py aislop_fix --path .
    python tests/mcp_tool.py agentdb_store --doc_id "x" --domain "y" --content "z"
    python tests/mcp_tool.py agentdb_search --query "keyword"
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp_bridge import (
    tool_search_graph,
    tool_get_snippet,
    tool_aislop_scan,
    tool_aislop_fix,
    tool_agentdb_store,
    tool_agentdb_search,
)


def parse_args(argv: list[str]) -> dict:
    """Parse --key value pairs into a dict."""
    args = {}
    i = 0
    while i < len(argv):
        if argv[i].startswith("--") and i + 1 < len(argv):
            key = argv[i][2:]
            val = argv[i + 1]
            # try to parse as JSON (for dicts/lists)
            try:
                val = json.loads(val)
            except (json.JSONDecodeError, ValueError):
                pass
            args[key] = val
            i += 2
        else:
            i += 1
    return args


DISPATCH = {
    "search_graph": lambda a: tool_search_graph(
        a.get("name_pattern", ""),
        a.get("query", ""),
        a.get("project", ""),
        int(a.get("limit", 20)),
    ),
    "get_snippet": lambda a: tool_get_snippet(
        a["qualified_name"],
        a.get("project", ""),
    ),
    "aislop_scan": lambda a: tool_aislop_scan(a.get("path", ".")),
    "aislop_fix": lambda a: tool_aislop_fix(a.get("path", ".")),
    "agentdb_store": lambda a: tool_agentdb_store(
        a["doc_id"],
        a["domain"],
        a["content"],
        a.get("metadata", "{}"),
    ),
    "agentdb_search": lambda a: tool_agentdb_search(
        a.get("query", ""),
        a.get("domain", ""),
        int(a.get("limit", 10)),
    ),
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    tool_name = sys.argv[1]
    if tool_name not in DISPATCH:
        print(json.dumps({"error": f"Unknown tool: {tool_name}"}))
        sys.exit(1)

    args = parse_args(sys.argv[2:])
    try:
        result = DISPATCH[tool_name](args)
        sys.stdout.buffer.write(result.encode("utf-8") + b"\n")
    except Exception as exc:
        sys.stdout.buffer.write(json.dumps({"error": str(exc)}).encode("utf-8") + b"\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
