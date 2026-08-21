"""Built-in loop templates."""

from __future__ import annotations

TEMPLATES: dict[str, str] = {
    "reflection": (
        "Self-improving loop: execute → evaluate → revise → repeat"
    ),
    "tool_use": (
        "Agent calls tools in a loop: decide → call tool → "
        "process result → decide again"
    ),
    "planning": (
        "Plan → execute steps → verify results → replan if needed"
    ),
    "multi_agent": (
        "Multiple agents work in parallel on subtasks, results merged"
    ),
    "critique": (
        "Generate → critique → revise → repeat "
        "until critique is satisfied"
    ),
    "escalation": (
        "Try cheap model first, escalate to expensive model on failure"
    ),
    "hybrid": (
        "Combination of reflection + tool_use + planning patterns"
    ),
}


def get_template(name: str) -> str:
    """Get a template by name."""
    if name not in TEMPLATES:
        available = ", ".join(TEMPLATES.keys())
        msg = f"Unknown template '{name}'. Available: {available}"
        raise ValueError(msg)
    return TEMPLATES[name]
