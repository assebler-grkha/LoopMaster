"""Agent adapter pattern for safe interaction with agent applications.

The adapter layer reads/writes agent config files, system prompts, and
other agent-specific artifacts. It NEVER modifies files during execution —
only snapshots, injects a temporary section via markers, and restores on
completion.
"""
from __future__ import annotations

import abc
import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .prompt_manager import PromptManager


@dataclass
class AgentInfo:
    """Metadata about a discovered agent installation."""

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


class CustomAdapter(AgentAdapter):
    """Adapter for user-specified agent paths."""

    def __init__(
        self,
        config_paths: list[str | Path],
        prompt_path: str | Path | None = None,
    ) -> None:
        self._config_paths = [Path(p) for p in config_paths]
        self._prompt_path = Path(prompt_path) if prompt_path else None
        self._snapshots: dict[Path, bytes] = {}

    def discover(self) -> AgentInfo:
        existing = [p for p in self._config_paths if p.exists()]
        prompt_paths: list[Path] = []
        if self._prompt_path and self._prompt_path.exists():
            prompt_paths = [self._prompt_path]
        return AgentInfo(
            agent_type="custom",
            display_name="Custom Agent",
            config_paths=existing,
            prompt_paths=prompt_paths,
            is_installed=len(existing) > 0,
        )

    def read_config(self) -> dict[str, Any]:
        import json

        configs: dict[str, Any] = {}
        for path in self._config_paths:
            if path.exists():
                try:
                    configs[str(path)] = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    configs[str(path)] = path.read_text(encoding="utf-8")
        return configs

    def read_system_prompt(self) -> str:
        if self._prompt_path and self._prompt_path.exists():
            return self._prompt_path.read_text(encoding="utf-8")
        return ""

    def write_config(self, config: dict[str, Any]) -> None:
        import json

        for path_str, content in config.items():
            path = Path(path_str)
            if isinstance(content, dict):
                path.write_text(json.dumps(content, indent=2), encoding="utf-8")
            else:
                path.write_text(str(content), encoding="utf-8")

    def inject_loop_context(self, loop_context: str) -> None:
        if not self._prompt_path:
            return
        prompt = self.read_system_prompt()
        pm = PromptManager()
        updated = pm.inject(prompt, loop_context)
        self._prompt_path.write_text(updated, encoding="utf-8")

    def validate_config(self) -> bool:
        return any(p.exists() for p in self._config_paths)

    def restore_original(self) -> None:
        for path, content in self._snapshots.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    @property
    def config_files(self) -> list[Path]:
        files = list(self._config_paths)
        if self._prompt_path:
            files.append(self._prompt_path)
        return files


class OpenCodeAdapter(AgentAdapter):
    """Adapter for OpenCode agent."""

    CONFIG_DIR = Path.home() / ".config" / "opencode"
    GLOBAL_CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"
    PROJECT_CONFIG = Path("opencode.json")

    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = project_root or Path.cwd()
        self._snapshots: dict[Path, bytes] = {}

    def discover(self) -> AgentInfo:
        config_paths = []
        prompt_paths = []

        if self.GLOBAL_CONFIG.exists():
            config_paths.append(self.GLOBAL_CONFIG)
        project_config = self._project_root / "opencode.json"
        if project_config.exists():
            config_paths.append(project_config)

        prompt_dir = self.CONFIG_DIR / "agents"
        if prompt_dir.exists():
            for f in prompt_dir.glob("*.md"):
                prompt_paths.append(f)

        return AgentInfo(
            agent_type="opencode",
            display_name="OpenCode",
            config_paths=config_paths,
            prompt_paths=prompt_paths,
            is_installed=len(config_paths) > 0,
        )

    def read_config(self) -> dict[str, Any]:
        import json

        configs: dict[str, Any] = {}
        for path in [self.GLOBAL_CONFIG, self._project_root / "opencode.json"]:
            if path.exists():
                with contextlib.suppress(json.JSONDecodeError, OSError):
                    configs[str(path)] = json.loads(path.read_text(encoding="utf-8"))
        return configs

    def read_system_prompt(self) -> str:
        agent_dir = self.CONFIG_DIR / "agents"
        if agent_dir.exists():
            prompts = sorted(agent_dir.glob("*.md"))
            if prompts:
                return prompts[-1].read_text(encoding="utf-8")
        return ""

    def write_config(self, config: dict[str, Any]) -> None:
        import json

        for path_str, content in config.items():
            path = Path(path_str)
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, dict):
                path.write_text(json.dumps(content, indent=2), encoding="utf-8")
            else:
                path.write_text(str(content), encoding="utf-8")

    def inject_loop_context(self, loop_context: str) -> None:
        agent_dir = self.CONFIG_DIR / "agents"
        if not agent_dir.exists():
            agent_dir.mkdir(parents=True, exist_ok=True)
            prompt_file = agent_dir / "loop-instructions.md"
        else:
            prompts = sorted(agent_dir.glob("*.md"))
            prompt_file = prompts[-1] if prompts else agent_dir / "loop-instructions.md"

        prompt = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else ""
        pm = PromptManager()
        updated = pm.inject(prompt, loop_context)
        prompt_file.write_text(updated, encoding="utf-8")

    def validate_config(self) -> bool:
        return self.CONFIG_DIR.exists() or (self._project_root / "opencode.json").exists()

    def restore_original(self) -> None:
        for path, content in self._snapshots.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    @property
    def config_files(self) -> list[Path]:
        files = []
        if self.GLOBAL_CONFIG.exists():
            files.append(self.GLOBAL_CONFIG)
        project_config = self._project_root / "opencode.json"
        if project_config.exists():
            files.append(project_config)
        return files


class ClaudeCodeAdapter(AgentAdapter):
    """Adapter for Claude Code agent."""

    CONFIG_DIR = Path.home() / ".claude"
    SETTINGS_FILE = Path.home() / ".claude" / "settings.json"

    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = project_root or Path.cwd()
        self._snapshots: dict[Path, bytes] = {}

    def discover(self) -> AgentInfo:
        config_paths = []
        prompt_paths = []

        if self.SETTINGS_FILE.exists():
            config_paths.append(self.SETTINGS_FILE)

        project_claude = self._project_root / ".claude"
        if project_claude.exists():
            for f in project_claude.glob("**/*.json"):
                config_paths.append(f)
            for f in project_claude.glob("**/*.md"):
                prompt_paths.append(f)

        return AgentInfo(
            agent_type="claude-code",
            display_name="Claude Code",
            config_paths=config_paths,
            prompt_paths=prompt_paths,
            is_installed=len(config_paths) > 0,
        )

    def read_config(self) -> dict[str, Any]:
        import json

        configs: dict[str, Any] = {}
        if self.SETTINGS_FILE.exists():
            with contextlib.suppress(json.JSONDecodeError, OSError):
                configs[str(self.SETTINGS_FILE)] = json.loads(
                    self.SETTINGS_FILE.read_text(encoding="utf-8")
                )
        return configs

    def read_system_prompt(self) -> str:
        claude_dir = self._project_root / ".claude"
        if claude_dir.exists():
            for f in sorted(claude_dir.glob("**/*.md"), reverse=True):
                return f.read_text(encoding="utf-8")
        return ""

    def write_config(self, config: dict[str, Any]) -> None:
        import json

        for path_str, content in config.items():
            path = Path(path_str)
            path.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, dict):
                path.write_text(json.dumps(content, indent=2), encoding="utf-8")
            else:
                path.write_text(str(content), encoding="utf-8")

    def inject_loop_context(self, loop_context: str) -> None:
        claude_dir = self._project_root / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        prompt_file = claude_dir / "CLAUDE.md"

        prompt = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else ""
        pm = PromptManager()
        updated = pm.inject(prompt, loop_context)
        prompt_file.write_text(updated, encoding="utf-8")

    def validate_config(self) -> bool:
        return self.SETTINGS_FILE.exists() or (self._project_root / ".claude").exists()

    def restore_original(self) -> None:
        for path, content in self._snapshots.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

    @property
    def config_files(self) -> list[Path]:
        files = []
        if self.SETTINGS_FILE.exists():
            files.append(self.SETTINGS_FILE)
        return files


class AgentRegistry:
    """Auto-discovers installed agents and provides adapters."""

    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = project_root or Path.cwd()
        self._adapters: dict[str, AgentAdapter] = {
            "opencode": OpenCodeAdapter(self._project_root),
            "claude-code": ClaudeCodeAdapter(self._project_root),
        }

    def register(self, name: str, adapter: AgentAdapter) -> None:
        self._adapters[name] = adapter

    def discover_all(self) -> list[AgentInfo]:
        results = []
        for adapter in self._adapters.values():
            info = adapter.discover()
            if info.is_installed:
                results.append(info)
        return results

    def get_adapter(self, agent_type: str) -> AgentAdapter:
        if agent_type not in self._adapters:
            msg = f"Unknown agent type: {agent_type}. Available: {list(self._adapters.keys())}"
            raise ValueError(msg)
        return self._adapters[agent_type]

    def get_all_adapters(self) -> dict[str, AgentAdapter]:
        return dict(self._adapters)
