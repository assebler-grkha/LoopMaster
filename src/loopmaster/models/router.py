"""Heuristic auto-routing engine for semantic model selection."""

from __future__ import annotations

from typing import Any

from .types import ModelRecommendation, ModelSpec


def auto_route_model(
    task_hint: str = "",
    prompt_tokens: int = 0,
    remaining_budget: float | None = None,
    registry: Any = None,
) -> ModelRecommendation:
    """Recommend or route to the optimal model based on complexity, tokens, and budget."""
    task_lower = task_hint.lower()
    is_complex = any(
        k in task_lower
        for k in ("complex", "reasoning", "architect", "critique", "refactor", "security")
    )
    is_coding = any(
        k in task_lower for k in ("code", "test", "python", "javascript", "rust", "sql", "patch")
    )
    is_large_context = prompt_tokens > 20000

    low_budget = remaining_budget is not None and remaining_budget < 0.15

    if registry is None:
        from .registry import get_default_registry

        registry = get_default_registry()

    if low_budget:
        target_alias = "@fast"
        reason = f"Low remaining budget (${remaining_budget:.2f} < $0.15); routing to fast tier"
    elif is_complex or is_large_context:
        target_alias = "@smart"
        reason = "Complex reasoning or large context requires smart tier model"
    elif is_coding:
        target_alias = "@coding"
        reason = "Code generation / review task matches coding tier model"
    else:
        target_alias = "@fast"
        reason = "Standard task routed to fast, cost-efficient model"

    try:
        spec: ModelSpec = registry.resolve(target_alias)
    except Exception:
        spec = registry.resolve("@default")

    est_cost = spec.calculate_cost(prompt_tokens, min(prompt_tokens // 2, 2048))
    return ModelRecommendation(model=spec, reason=reason, estimated_cost=est_cost)
