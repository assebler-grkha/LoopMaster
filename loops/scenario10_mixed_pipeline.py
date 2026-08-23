"""Scenario 10: Mixed pipeline — Shell + HTTP + MCP + LLM combined."""

import sys
from pathlib import Path

from loopmaster import (
    HTTPExecutor,
    Loop,
    MCPToolExecutor,
    ShellExecutor,
    Step,
)

MODEL = "stealth/ox-alpha"

MCP_SERVER = [
    sys.executable,
    str(Path(__file__).resolve().parent.parent / "tests" / "fake_mcp_server.py"),
]


@Loop(name="test_mixed_pipeline", version="1.0.0")
def test_mixed_pipeline(ctx):
    Step(
        "shell_step",
        executor=ShellExecutor(
            command=["python", "-c", "import time; print(f'timestamp={time.time():.0f}')"]
        ),
    )
    Step(
        "http_step",
        executor=HTTPExecutor(
            url="https://api.github.com/zen",
            method="GET",
            json_output=False,
            headers={"User-Agent": "LoopMaster/0.1.0"},
        ),
    )
    Step(
        "mcp_step",
        executor=MCPToolExecutor(
            server_command=MCP_SERVER,
            tool_name="echo",
            arguments={"text": "mixed-pipeline-test"},
        ),
    )
    Step(
        "combine",
        model=MODEL,
        prompt=(
            "Three tools returned:\n"
            "1. Shell (timestamp): {shell_step.stdout}\n"
            "2. HTTP (GitHub Zen): {http_step.body}\n"
            "3. MCP echo: {mcp_step.text}\n\n"
            "Write a short creative summary combining all three results."
        ),
    )
    return ctx
