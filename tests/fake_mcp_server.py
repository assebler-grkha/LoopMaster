"""Minimal MCP server for testing — responds to initialize + tools/call over stdio."""

import json
import sys


def handle_request(line):
    req = json.loads(line.strip())
    method = req.get("method", "")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req["id"], "result": {"protocolVersion": "2024-11-05"}}
    if method == "notifications/initialized":
        return None
    if method == "tools/call":
        tool = req["params"]["name"]
        args = req["params"].get("arguments", {})
        if tool == "echo":
            text = args.get("text", "empty")
            return {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {
                    "content": [{"type": "text", "text": f"Echo: {text}"}],
                    "isError": False,
                },
            }
        if tool == "add":
            a = args.get("a", 0)
            b = args.get("b", 0)
            return {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {"content": [{"type": "text", "text": str(a + b)}], "isError": False},
            }
        if tool == "uppercase":
            text = args.get("text", "")
            return {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {"content": [{"type": "text", "text": text.upper()}], "isError": False},
            }
        return {
            "jsonrpc": "2.0",
            "id": req["id"],
            "result": {
                "content": [{"type": "text", "text": f"Unknown tool: {tool}"}],
                "isError": True,
            },
        }
    return {
        "jsonrpc": "2.0",
        "id": req.get("id", 0),
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


for line in sys.stdin:
    resp = handle_request(line)
    if resp is not None:
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
