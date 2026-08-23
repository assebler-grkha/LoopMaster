"""Cost tracking middleware for loop execution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CostRecord:
    """A single cost record for an LLM API call.

    Attributes:
        model: Model name used for the call.
        input_tokens: Number of input tokens consumed.
        output_tokens: Number of output tokens generated.
        cost: Computed cost in dollars.
        timestamp: Unix timestamp of the call.
        step_name: Name of the step that triggered the call.
        metadata: Additional call metadata.
    """

    model: str
    input_tokens: int
    output_tokens: int
    cost: float
    timestamp: float
    step_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class CostTracker:
    """Tracks costs across loop executions.

    Pricing is configurable per model. Default estimates:
    - gpt-4o: $2.50/1M input, $10.00/1M output
    - gpt-4o-mini: $0.15/1M input, $0.60/1M output
    - claude-3-5-sonnet: $3.00/1M input, $15.00/1M output
    """

    DEFAULT_PRICING: dict[str, dict[str, float]] = {
        "gpt-4o": {"input": 2.50 / 1_000_000, "output": 10.00 / 1_000_000},
        "gpt-4o-mini": {"input": 0.15 / 1_000_000, "output": 0.60 / 1_000_000},
        "gpt-4-turbo": {"input": 10.00 / 1_000_000, "output": 30.00 / 1_000_000},
        "claude-3-5-sonnet": {"input": 3.00 / 1_000_000, "output": 15.00 / 1_000_000},
        "claude-3-haiku": {"input": 0.25 / 1_000_000, "output": 1.25 / 1_000_000},
    }

    def __init__(self, pricing: dict[str, dict[str, float]] | None = None) -> None:
        self._pricing = {**self.DEFAULT_PRICING, **(pricing or {})}
        self._records: list[CostRecord] = []
        self._budget_limit: float | None = None

    def set_budget(self, max_cost: float) -> None:
        """Set budget limit in dollars."""
        self._budget_limit = max_cost

    def calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost for a model call using ModelRegistry or custom pricing."""
        if model in self._pricing:
            rates = self._pricing[model]
            return input_tokens * rates["input"] + output_tokens * rates["output"]
        from ..models import get_default_registry

        return get_default_registry().calculate_cost(model, input_tokens, output_tokens)

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        step_name: str = "",
        **metadata: Any,
    ) -> float:
        """Record a cost and return the amount."""
        import time

        cost = self.calculate_cost(model, input_tokens, output_tokens)
        self._records.append(
            CostRecord(
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=cost,
                timestamp=time.time(),
                step_name=step_name,
                metadata=metadata,
            )
        )
        return cost

    @property
    def total_cost(self) -> float:
        """Total cost across all recorded calls."""
        return sum(r.cost for r in self._records)

    @property
    def total_input_tokens(self) -> int:
        """Total input tokens across all recorded calls."""
        return sum(r.input_tokens for r in self._records)

    @property
    def total_output_tokens(self) -> int:
        """Total output tokens across all recorded calls."""
        return sum(r.output_tokens for r in self._records)

    @property
    def is_over_budget(self) -> bool:
        """Whether the budget limit has been exceeded."""
        if self._budget_limit is None:
            return False
        return self.total_cost > self._budget_limit

    @property
    def remaining_budget(self) -> float | None:
        """Remaining budget in dollars, or None if no limit set."""
        if self._budget_limit is None:
            return None
        return max(0.0, self._budget_limit - self.total_cost)

    def cost_by_model(self) -> dict[str, float]:
        """Get cost breakdown by model."""
        result: dict[str, float] = {}
        for r in self._records:
            result[r.model] = result.get(r.model, 0.0) + r.cost
        return result

    def cost_by_step(self) -> dict[str, float]:
        """Get cost breakdown by step."""
        result: dict[str, float] = {}
        for r in self._records:
            key = r.step_name or "_unknown"
            result[key] = result.get(key, 0.0) + r.cost
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cost": self.total_cost,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "budget_limit": self._budget_limit,
            "is_over_budget": self.is_over_budget,
            "cost_by_model": self.cost_by_model(),
            "cost_by_step": self.cost_by_step(),
        }

    def save(self, filepath: str | Path) -> None:
        """Save cost records to JSON."""
        data = {
            "records": [
                {
                    "model": r.model,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "cost": r.cost,
                    "timestamp": r.timestamp,
                    "step_name": r.step_name,
                    "metadata": r.metadata,
                }
                for r in self._records
            ],
            "summary": self.to_dict(),
        }
        Path(filepath).write_text(json.dumps(data, indent=2), encoding="utf-8")
