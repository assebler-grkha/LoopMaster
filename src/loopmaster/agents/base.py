"""Base classes for agent adapters."""

from __future__ import annotations

import abc
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AgentInfo:
    """Metadata about a discovered agent installation.

    Attributes:
        agent_type: Identifier for the agent type (e.g., 'opencode', 'cursor').
        display_name: Human-readable name for the agent.
        config_paths: Paths to configuration files found.
        prompt_paths: Paths to system prompt files found.
        is_installed: Whether the agent was detected on the system.
    """

    agent_type: str
    display_name: str
    config_paths: list[Path]
    prompt_paths: list[Path]
    is_installed: bool = False


class AgentAdapter(abc.ABC):
    """Base class for agent-specific adapters.

    Each concrete adapter knows how to discover, read, write, and restore
    the configuration files for a specific agent application.
    """

    def __init__(self) -> None:
        self._snapshots: dict[Path, bytes] = {}
        self._created_files: set[Path] = set()

    def snapshot(self, path: Path) -> None:
        """Snapshot file content before modification. Idempotent per path."""
        if path not in self._snapshots and path.exists():
            try:
                self._snapshots[path] = path.read_bytes()
            except OSError as exc:
                logger.warning("Could not snapshot %s: %s", path, exc)

    @abc.abstractmethod
    def discover(self) -> AgentInfo:
        """Discover if this agent is installed and where."""
        ...

    @abc.abstractmethod
    def read_config(self) -> dict[str, Any]:
        """Read agent configuration files."""
        ...

    @abc.abstractmethod
    def read_system_prompt(self) -> str:
        """Read the agent's system prompt."""
        ...

    @abc.abstractmethod
    def write_config(self, config: dict[str, Any]) -> None:
        """Write agent configuration (with safety checks)."""
        ...

    @abc.abstractmethod
    def inject_loop_context(self, loop_context: str) -> None:
        """Inject loop instructions into agent's system prompt."""
        ...

    @abc.abstractmethod
    def validate_config(self) -> bool:
        """Validate that agent config is in expected state."""
        ...

    @abc.abstractmethod
    def restore_original(self) -> None:
        """Restore all files to pre-modification state."""
        ...

    @property
    @abc.abstractmethod
    def config_files(self) -> list[Path]:
        """All files that this adapter manages."""
        ...
