#!/usr/bin/env python3
"""MCP Bridge — stdio JSON-RPC server exposing codebase-memory, aislop, agentdb.

LoopMaster's MCPToolExecutor spawns this as a subprocess and speaks
line-delimited JSON-RPC. This bridge routes tool calls to:

  search_graph    → codebase-memory HTTP API (port 9749)
  get_snippet     → codebase-memory HTTP API
  aislop_scan     → npx aislop scan --json (subprocess)
  aislop_fix      → npx aislop fix (subprocess)
  agentdb_store   → direct SQLite write to pathfinder.db
  agentdb_search  → direct SQLite read from pathfinder.db

Usage:
    python tests/mcp_bridge.py          # stdio mode (for MCPToolExecutor)
    python tests/mcp_bridge.py --test   # self-test mode
"""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────
AGENTDB_PATH = Path("C:/Users/Gregory/.opencode-mcp/pathfinder-app/.agentdb/pathfinder.db")
_CM_BIN = str(
    Path("C:/Users/Gregory/AppData/Local/codebase-memory-mcp/0.10.5") / "codebase-memory-mcp.exe"
)
AISLOP_BIN = "npx.cmd" if sys.platform == "win32" else "npx"
AISLOP_TIMEOUT = 120


# ── codebase-memory helpers ─────────────────────────────────────────────
def _cm_stdio(method: str, params: dict) -> dict:
    """Spawn codebase-memory binary, speak JSON-RPC over stdio."""
    proc = subprocess.Popen(
        [_CM_BIN],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    init_msg = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "mcp-bridge", "version": "1.0.0"},
            },
        }
    )
    call_msg = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        }
    )
    notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
    payload = (init_msg + "\n" + notif + "\n" + call_msg + "\n").encode()
    stdout, _ = proc.communicate(input=payload, timeout=30)
    for line in reversed(stdout.decode(errors="replace").strip().splitlines()):
        try:
            obj = json.loads(line)
            if obj.get("id") == 1:
                return obj
        except json.JSONDecodeError:
            continue
    return {"error": "No valid response from codebase-memory"}


def tool_search_graph(
    name_pattern: str = "", query: str = "", project: str = "", limit: int = 20
) -> str:
    args = {"limit": limit}
    if project:
        args["project"] = project
    if name_pattern:
        args["name_pattern"] = name_pattern
    if query:
        args["query"] = query
    result = _cm_stdio("tools/call", {"name": "search_graph", "arguments": args})
    return json.dumps(result.get("result", result), ensure_ascii=False, indent=2)


def tool_get_snippet(qualified_name: str, project: str = "") -> str:
    args = {"qualified_name": qualified_name}
    if project:
        args["project"] = project
    result = _cm_stdio("tools/call", {"name": "get_code_snippet", "arguments": args})
    return json.dumps(result.get("result", result), ensure_ascii=False, indent=2)


# ── aislop helpers ──────────────────────────────────────────────────────
def tool_aislop_scan(path: str = ".") -> str:
    proc = subprocess.run(
        [AISLOP_BIN, "aislop", "scan", path, "--json"],
        capture_output=True,
        text=True,
        timeout=AISLOP_TIMEOUT,
    )
    try:
        return proc.stdout
    except Exception:
        return json.dumps({"error": proc.stderr[:2000]})


def tool_aislop_fix(path: str = ".") -> str:
    proc = subprocess.run(
        [AISLOP_BIN, "aislop", "fix", path],
        capture_output=True,
        text=True,
        timeout=AISLOP_TIMEOUT,
    )
    return json.dumps(
        {
            "stdout": proc.stdout[:2000],
            "stderr": proc.stderr[:500],
            "returncode": proc.returncode,
        }
    )


# ── agentdb helpers ─────────────────────────────────────────────────────
def _agentdb_conn():
    if not AGENTDB_PATH.exists():
        raise FileNotFoundError(f"AgentDB not found: {AGENTDB_PATH}")
    conn = sqlite3.connect(str(AGENTDB_PATH))
    conn.text_factory = lambda x: x.decode("utf-8", errors="replace")
    return conn


def tool_agentdb_store(doc_id: str, domain: str, content: str, metadata: str = "{}") -> str:
    conn = _agentdb_conn()
    now = datetime.now().isoformat()
    existing = conn.execute("SELECT id FROM documents WHERE id = ?", (doc_id,)).fetchone()
    if existing:
        conn.execute(
            "UPDATE documents SET content=?, metadata=?, created_at=? WHERE id=?",
            (content, metadata, now, doc_id),
        )
        msg = f"Updated: {doc_id}"
    else:
        conn.execute(
            "INSERT INTO documents (id,domain,content,metadata,created_at) VALUES (?,?,?,?,?)",
            (doc_id, domain, content, metadata, now),
        )
        msg = f"Saved: {doc_id}"
    conn.commit()
    conn.close()
    return msg


def tool_agentdb_search(query: str, domain: str = "", limit: int = 10) -> str:
    conn = _agentdb_conn()
    keywords = query.split()
    conditions = []
    params = []
    for kw in keywords:
        conditions.append("(content LIKE ? OR id LIKE ?)")
        params.extend([f"%{kw}%", f"%{kw}%"])
    sql = (
        "SELECT id, domain, content, metadata, created_at FROM documents "
        f"WHERE {' AND '.join(conditions)}"
    )
    if domain:
        sql += " AND domain = ?"
        params.append(domain)
    sql += f" ORDER BY created_at DESC LIMIT {limit}"
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    results = [
        {"id": r[0], "domain": r[1], "content": r[2][:500], "created_at": r[4]} for r in rows
    ]
    return json.dumps({"total": len(results), "results": results}, ensure_ascii=False, indent=2)


# ── Tool dispatch ───────────────────────────────────────────────────────
TOOLS = {
    "search_graph": lambda a: tool_search_graph(
        a.get("name_pattern", ""),
        a.get("query", ""),
        a.get("project", ""),
        a.get("limit", 20),
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
        a.get("limit", 10),
    ),
}


def handle_request(req: dict) -> dict:
    method = req.get("method", "")
    params = req.get("params", {})
    req_id = req.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mcp-bridge", "version": "1.0.0"},
            },
        }

    if method == "notifications/initialized":
        return None  # no response for notifications

    if method == "tools/list":
        tool_list = [
            {
                "name": n,
                "description": f"Bridge tool: {n}",
                "inputSchema": {"type": "object", "properties": {}},
            }
            for n in TOOLS
        ]
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": tool_list}}

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        if tool_name not in TOOLS:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
            }
        try:
            output = TOOLS[tool_name](arguments)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": output}],
                    "isError": False,
                },
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)[:2000]}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


# ── stdio loop ──────────────────────────────────────────────────────────
def main():
    if "--test" in sys.argv:
        print("Bridge self-test...")
        print(f"  agentdb: {AGENTDB_PATH} exists={AGENTDB_PATH.exists()}")
        try:
            r = tool_agentdb_search("test", limit=1)
            print(f"  agentdb_search: OK ({len(r)} bytes)")
        except Exception as e:
            print(f"  agentdb_search: FAIL ({e})")
        try:
            r = tool_aislop_scan(".")
            print(f"  aislop_scan: OK ({len(r)} bytes)")
        except Exception as e:
            print(f"  aislop_scan: FAIL ({e})")
        print("Done.")
        return

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
