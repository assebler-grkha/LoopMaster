"""Scenario 9: MCPToolExecutor calling real MCP server (fake_mcp_server.py)."""

import sys
from pathlib import Path

from loopmaster import Loop, MCPToolExecutor, Step

MODEL = "stealth/ox-alpha"

SERVER = [
    sys.executable,
    str(Path(__file__).resolve().parent.parent / "tests" / "fake_mcp_server.py"),
]


@Loop(name="test_mcp_tool", version="1.0.0")
def test_mcp_tool(ctx):
    Step(
        "echo_step",
        executor=MCPToolExecutor(
            server_command=SERVER,
            tool_name="echo",
            arguments={"text": "Hello from LoopMaster"},
        ),
    )
    Step(
        "add_step",
        executor=MCPToolExecutor(
            server_command=SERVER,
            tool_name="add",
            arguments={"a": 17, "b": 25},
        ),
    )
    Step(
        "uppercase_step",
        executor=MCPToolExecutor(
            server_command=SERVER,
            tool_name="uppercase",
            arguments={"text": "{echo_step.text}"},
        ),
    )
    Step(
        "analyze",
        model=MODEL,
        prompt=(
            "MCP results:\n"
            "  echo: {echo_step.text}\n"
            "  add: {add_step.text}\n"
            "  uppercase: {uppercase_step.text}\n\n"
            "Describe what each tool returned in one sentence each."
        ),
    )
    return ctx
