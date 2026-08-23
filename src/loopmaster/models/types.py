"""Data models and policy types for Model Registry and auto-routing."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ModelPolicyMode(Enum):
    """Enforcement mode for model resolution."""

    PERMISSIVE = "permissive"
    STRICT = "strict"
    ALIAS_ONLY = "alias_only"
    AUTO_ROUTE = "auto_route"


@dataclass
class ModelSpec:
    """Specification and pricing metadata for an LLM model."""

    name: str
    provider: str
    aliases: list[str] = field(default_factory=list)
    cost_input_1m: float = 0.0
    cost_output_1m: float = 0.0
    context_window: int = 128000
    max_output_tokens: int = 4096
    tags: list[str] = field(default_factory=list)
    approved: bool = True
    api_key_env: str | None = None
    base_url: str | None = None
    requires_api_key: bool = True

    @property
    def is_available(self) -> bool:
        """Check if required API key is present in environment."""
        if not self.requires_api_key:
            return True
        if not self.api_key_env:
            return True
        return bool(os.getenv(self.api_key_env))

    def calculate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Calculate dollar cost for token usage."""
        in_cost = (input_tokens / 1_000_000.0) * self.cost_input_1m
        out_cost = (output_tokens / 1_000_000.0) * self.cost_output_1m
        return in_cost + out_cost

    def to_dict(self, include_availability: bool = True) -> dict[str, Any]:
        """Serialize metadata for export and MCP tools without leaking secrets."""
        d: dict[str, Any] = {
            "name": self.name,
            "provider": self.provider,
            "aliases": self.aliases,
            "cost_input_1m": self.cost_input_1m,
            "cost_output_1m": self.cost_output_1m,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "tags": self.tags,
            "approved": self.approved,
            "requires_api_key": self.requires_api_key,
        }
        if include_availability:
            d["is_available"] = self.is_available
            d["api_key_configured"] = (
                bool(os.getenv(self.api_key_env)) if self.api_key_env else True
            )
        return d


@dataclass
class ModelPolicy:
    """Security, cost guardrails, and alias routing policies for model execution."""

    mode: ModelPolicyMode = ModelPolicyMode.PERMISSIVE
    max_cost_per_step: float | None = None
    allow_auto_downgrade_on_budget: bool = True
    default_alias: str = "@fast"

    def to_dict(self) -> dict[str, Any]:
        """Serialize policy configuration."""
        d: dict[str, Any] = {
            "mode": self.mode.value,
            "allow_auto_downgrade_on_budget": self.allow_auto_downgrade_on_budget,
            "default_alias": self.default_alias,
        }
        if self.max_cost_per_step is not None:
            d["max_cost_per_step"] = self.max_cost_per_step
        return d


@dataclass
class ModelRecommendation:
    """Result of intelligent model selection recommendation."""

    model: ModelSpec
    reason: str
    estimated_cost: float
