"""Agent adapters module.

Provides safe interaction with agent applications (OpenCode, Claude Code, etc.)
at the configuration level — reading/writing config files, system prompts, and
other agent-specific artifacts.
"""

from loopmaster.agents.adapter import (
    AgentAdapter,
    AgentInfo,
    AgentRegistry,
    ClaudeCodeAdapter,
    CustomAdapter,
    OpenCodeAdapter,
)
from loopmaster.agents.config_manager import ConfigError, ConfigManager
from loopmaster.agents.prompt_manager import PromptManager

__all__ = [
    "AgentInfo",
    "AgentAdapter",
    "AgentRegistry",
    "ClaudeCodeAdapter",
    "ConfigError",
    "ConfigManager",
    "CustomAdapter",
    "OpenCodeAdapter",
    "PromptManager",
]
