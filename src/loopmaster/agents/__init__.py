"""Agent adapters module.

Provides safe interaction with agent applications (OpenCode, Claude Code, etc.)
at the configuration level — reading/writing config files, system prompts, and
other agent-specific artifacts.
"""

from loopmaster.agents.adapter import (
    ClaudeCodeAdapter,
    CursorAdapter,
    CustomAdapter,
    OpenCodeAdapter,
)
from loopmaster.agents.base import AgentAdapter, AgentInfo
from loopmaster.agents.config_manager import ConfigError, ConfigManager
from loopmaster.agents.prompt_manager import PromptManager
from loopmaster.agents.registry import AgentRegistry

__all__ = [
    "AgentInfo",
    "AgentAdapter",
    "AgentRegistry",
    "ClaudeCodeAdapter",
    "CursorAdapter",
    "ConfigError",
    "ConfigManager",
    "CustomAdapter",
    "OpenCodeAdapter",
    "PromptManager",
]
