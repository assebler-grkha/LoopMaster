"""Base class and template resolution helpers for tool executors."""

from __future__ import annotations

import os
import re
import sys
from abc import ABC, abstractmethod
from typing import Any

_TEMPLATE_PATTERN = re.compile(r"\{\{?([a-zA-Z_][\w\.]*)\}?\}")


def build_minimal_env(allow: list[str] | None = None) -> dict[str, str]:
    """Minimal child-process environment: OS plumbing plus explicitly allowed names.

    Prevents wholesale host-env inheritance (API keys, cloud credentials) from
    leaking into every subprocess spawned by shell/mcp/code executors.
    """
    keys = ["PATH", "PYTHONIOENCODING"]
    if sys.platform == "win32":
        keys += ["SYSTEMROOT", "COMSPEC", "TEMP", "TMP", "PATHEXT"]
    else:
        keys += ["HOME", "TMPDIR", "LANG"]
    env = {k: os.environ[k] for k in keys if k in os.environ}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    for name in allow or []:
        if name in os.environ:
            env[name] = os.environ[name]
    return env


def resolve_path_value(path: str, ctx_data: dict[str, Any]) -> Any:
    """Resolve a dot-separated variable path (e.g. 'step1.stdout') from context data."""
    parts = path.split(".")
    val: Any = ctx_data
    for part in parts:
        if isinstance(val, dict):
            val = val.get(part)
        elif hasattr(val, part):
            val = getattr(val, part)
        else:
            return None
        if val is None:
            return None
    return val


def resolve_template_value(template: Any, ctx_data: dict[str, Any]) -> Any:
    """Recursively resolve {variable.path} placeholders inside strings, lists, or dicts."""
    if isinstance(template, str):
        if not template:
            return template

        # Check if the entire string is a single variable placeholder
        exact_match = _TEMPLATE_PATTERN.fullmatch(template.strip())
        if exact_match:
            resolved = resolve_path_value(exact_match.group(1), ctx_data)
            if resolved is not None:
                return resolved

        def _repl(match: re.Match[str]) -> str:
            resolved = resolve_path_value(match.group(1), ctx_data)
            return str(resolved) if resolved is not None else match.group(0)

        return _TEMPLATE_PATTERN.sub(_repl, template)

    if isinstance(template, list):
        return [resolve_template_value(item, ctx_data) for item in template]

    if isinstance(template, dict):
        return {k: resolve_template_value(v, ctx_data) for k, v in template.items()}

    return template


class BaseExecutor(ABC):
    """Abstract base class for all deterministic tool executors."""

    @abstractmethod
    def execute(self, ctx_data: dict[str, Any]) -> Any:
        """Execute the tool action against context data and return structured result."""

    @abstractmethod
    def to_dict(self) -> dict[str, Any]:
        """Serialize configuration for YAML or JSON export."""
