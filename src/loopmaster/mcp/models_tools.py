"""MCP tool handlers for Model Registry discovery and recommendation."""

from __future__ import annotations

from typing import Any

from ..models import get_default_registry


def handle_model_list(registry: Any = None) -> dict[str, Any]:
    """Return catalog of available models, approval status, aliases, and pricing."""
    reg = registry or get_default_registry()
    models_data = [spec.to_dict(include_availability=True) for spec in reg._models.values()]
    return {
        "count": len(models_data),
        "approved_count": len([m for m in models_data if m.get("approved")]),
        "models": models_data,
    }


def handle_model_recommend(
    task: str = "",
    prompt_tokens: int = 0,
    remaining_budget: float | None = None,
    registry: Any = None,
) -> dict[str, Any]:
    """Recommend optimal approved model for a given task and constraints."""
    reg = registry or get_default_registry()
    rec = reg.recommend(
        task_hint=task, prompt_tokens=prompt_tokens, remaining_budget=remaining_budget
    )
    return {
        "recommended_model": rec.model.name,
        "aliases": rec.model.aliases,
        "provider": rec.model.provider,
        "reason": rec.reason,
        "estimated_cost": rec.estimated_cost,
        "context_window": rec.model.context_window,
    }
