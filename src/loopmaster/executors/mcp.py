"""Model Context Protocol (MCP) stdio tool executor with NDJSON framing and OTel tracing."""

from __future__ import annotations

import contextlib
import json
import logging
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import BaseExecutor, build_minimal_env, resolve_template_value

logger = logging.getLogger("loopmaster.executors.mcp")


@dataclass
class MCPToolResult:
    """Structured result of an MCP tool invocation."""

    content: list[dict[str, Any]]
    text: str
    is_error: bool
    success: bool
    error: str | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "text": self.text,
            "is_error": self.is_error,
            "success": self.success,
            "error": self.error,
        }


def _extract_text_content(content_list: list[Any]) -> str:
    """Extract plain text concatenated from MCP content objects."""
    texts: list[str] = []
    for item in content_list:
        if isinstance(item, dict) and item.get("type") == "text":
            texts.append(str(item.get("text", "")))
        elif isinstance(item, str):
            texts.append(item)
    return "\n".join(texts)


def _readline_capped(stream: Any, limit: int = 1024 * 1024) -> str:
    """Read one NDJSON line with a hard character cap (unbounded readline = OOM risk)."""
    line = stream.readline(limit + 1)
    if not line:
        return ""
    if len(line) > limit:
        raise ValueError(f"MCP response line exceeded {limit} characters")
    return line


def _read_response(proc: subprocess.Popen[str], expected_id: int) -> dict[str, Any] | None:
    """Read the next JSON-RPC response whose id matches; skip notifications.

    Returns ``None`` on EOF. Malformed lines are skipped (bounded count).
    """
    assert proc.stdout
    for _ in range(1000):
        try:
            line = _readline_capped(proc.stdout)
        except ValueError as exc:
            logger.warning("MCP response too large: %s", exc)
            return None
        if not line:
            return None
        try:
            data = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("id") == expected_id:
            return data
    logger.warning("MCP server sent no matching response for id=%s", expected_id)
    return None


def _mcp_handshake(proc: subprocess.Popen[str]) -> str | None:
    """Perform MCP initialize handshake. Returns error message if failed."""
    assert proc.stdin and proc.stdout
    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "loopmaster", "version": "0.1.0"},
        },
    }
    proc.stdin.write(json.dumps(init_req) + "\n")
    proc.stdin.flush()

    resp = _read_response(proc, expected_id=1)
    if resp is None:
        return "MCP server exited before initialize response"

    init_notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    proc.stdin.write(json.dumps(init_notif) + "\n")
    proc.stdin.flush()
    return None


def _mcp_call_tool(
    proc: subprocess.Popen[str], tool_name: str, resolved_args: dict[str, Any]
) -> MCPToolResult:
    """Send tools/call request and parse MCP response."""
    assert proc.stdin and proc.stdout
    call_req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": resolved_args},
    }
    proc.stdin.write(json.dumps(call_req) + "\n")
    proc.stdin.flush()

    resp_data = _read_response(proc, expected_id=2)
    if resp_data is None:
        return MCPToolResult(
            content=[],
            text="",
            is_error=True,
            success=False,
            error="MCP server exited before tool response",
        )
    if "error" in resp_data:
        err_msg = resp_data["error"].get("message", str(resp_data["error"]))
        return MCPToolResult(content=[], text="", is_error=True, success=False, error=err_msg)

    res_obj = resp_data.get("result", {})
    content = res_obj.get("content", [])
    is_err = res_obj.get("isError", False)
    text_out = _extract_text_content(content)
    return MCPToolResult(
        content=content,
        text=text_out,
        is_error=is_err,
        success=not is_err,
        error=text_out if is_err else None,
    )


def _cleanup_proc(proc: subprocess.Popen[str]) -> None:
    """Gracefully terminate MCP subprocess."""
    try:
        if proc.stdin and not proc.stdin.closed:
            proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=1.0)
    except Exception:
        with contextlib.suppress(Exception):
            proc.kill()


class MCPToolExecutor(BaseExecutor):
    """Executes tools on an external MCP server process over stdio (NDJSON)."""

    def __init__(
        self,
        server_command: str | list[str],
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        timeout: float = 60.0,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.server_command = server_command
        self.tool_name = tool_name
        self.arguments = arguments or {}
        self.timeout = timeout
        self.cwd = str(cwd) if cwd is not None else None
        self.env = env

    def _build_command(self) -> list[str]:
        if isinstance(self.server_command, list):
            return self.server_command
        return shlex.split(self.server_command, posix=sys.platform != "win32")

    def _execute_mcp_exchange(self, resolved_args: dict[str, Any]) -> MCPToolResult:
        cmd_args = self._build_command()
        custom_env = build_minimal_env(allow=list(self.env or {}))
        if self.env:
            custom_env.update(self.env)

        proc = subprocess.Popen(
            cmd_args,
            cwd=self.cwd,
            env=custom_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        holder: dict[str, MCPToolResult] = {}

        def _run() -> None:
            err = _mcp_handshake(proc)
            if err:
                holder["result"] = MCPToolResult(
                    content=[], text="", is_error=True, success=False, error=err
                )
                return
            holder["result"] = _mcp_call_tool(proc, self.tool_name, resolved_args)

        # Daemon thread + explicit timeout: a server blocked mid-readline must
        # not pin the worker past the deadline. The finally-branch terminates
        # the process, which unblocks the reader and lets the thread exit.
        reader = threading.Thread(target=_run, daemon=True)
        reader.start()
        try:
            reader.join(self.timeout)
            if reader.is_alive():
                return MCPToolResult(
                    content=[],
                    text="",
                    is_error=True,
                    success=False,
                    error=f"MCP invocation timed out after {self.timeout}s",
                )
            result = holder.get("result")
            if result is None:
                return MCPToolResult(
                    content=[],
                    text="",
                    is_error=True,
                    success=False,
                    error=f"MCP invocation timed out after {self.timeout}s",
                )
            return result
        except Exception as exc:
            return MCPToolResult(
                content=[],
                text="",
                is_error=True,
                success=False,
                error=f"MCP invocation failed: {exc}",
            )
        finally:
            _cleanup_proc(proc)

    def execute(self, ctx_data: dict[str, Any]) -> MCPToolResult:
        """Execute the MCP tool within a CLIENT OTel span."""
        resolved_args = resolve_template_value(self.arguments, ctx_data)

        from ..telemetry import SpanKind, SpanStatusCode, get_tracer

        tracer = get_tracer()
        with tracer.start_as_current_span(
            "tool.mcp",
            kind=SpanKind.CLIENT,
            attributes={
                "rpc.system": "mcp",
                "rpc.method": "tools/call",
                "mcp.tool.name": self.tool_name,
            },
        ) as span:
            result = self._execute_mcp_exchange(resolved_args)
            if not result.success:
                span.set_status(SpanStatusCode.ERROR, result.error or "MCP tool returned error")
            return result

    def to_dict(self) -> dict[str, Any]:
        """Serialize configuration for export."""
        return {
            "type": "mcp",
            "server_command": self.server_command,
            "tool_name": self.tool_name,
            "arguments": self.arguments,
            "timeout": self.timeout,
        }
