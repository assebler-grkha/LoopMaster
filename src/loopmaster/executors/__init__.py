"""Tool Execution Bridge — deterministic tool executors for AI agent loops."""

from __future__ import annotations

from .base import BaseExecutor, resolve_path_value, resolve_template_value
from .http import HTTPExecutor, HTTPResult
from .mcp import MCPToolExecutor, MCPToolResult
from .shell import ShellExecutor, ShellResult

__all__ = [
    "BaseExecutor",
    "resolve_path_value",
    "resolve_template_value",
    "ShellExecutor",
    "ShellResult",
    "HTTPExecutor",
    "HTTPResult",
    "MCPToolExecutor",
    "MCPToolResult",
]
