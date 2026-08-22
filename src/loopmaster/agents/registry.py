"""Agent registry for auto-discovery and adapter management."""

from __future__ import annotations

from pathlib import Path

from .adapter import (
    ClaudeCodeAdapter,
    CursorAdapter,
    OpenCodeAdapter,
)
from .base import AgentAdapter, AgentInfo


class AgentRegistry:
    """Auto-discovers installed agents and provides adapters.

    Maintains a registry of AgentAdapter instances keyed by agent type.
    Use register() to add custom adapters, or rely on built-in ones.
    """

    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = project_root or Path.cwd()
        self._adapters: dict[str, AgentAdapter] = {
            "opencode": OpenCodeAdapter(self._project_root),
            "claude-code": ClaudeCodeAdapter(self._project_root),
            "cursor": CursorAdapter(self._project_root),
        }

    def register(self, name: str, adapter: AgentAdapter) -> None:
        """Register a custom adapter by name."""
        self._adapters[name] = adapter

    def discover_all(self) -> list[AgentInfo]:
        """Discover all installed agents and return their info."""
        results = []
        for adapter in self._adapters.values():
            info = adapter.discover()
            if info.is_installed:
                results.append(info)
        return results

    def get_adapter(self, agent_type: str) -> AgentAdapter:
        """Get an adapter by agent type name.

        Raises:
            ValueError: If agent_type is not registered.
        """
        if agent_type not in self._adapters:
            msg = f"Unknown agent type: {agent_type}. Available: {list(self._adapters.keys())}"
            raise ValueError(msg)
        return self._adapters[agent_type]

    def get_all_adapters(self) -> dict[str, AgentAdapter]:
        """Return a copy of all registered adapters."""
        return dict(self._adapters)
