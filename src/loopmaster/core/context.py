"""Immutable context passing between steps."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Context:
    """Mutable context that provides immutable snapshots.

    The engine creates a frozen snapshot before each step, passes it,
    then merges the step's output diff back into the live context.
    """

    _data: dict[str, Any] = field(default_factory=dict)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            return super().__getattribute__(name)
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(f"Context has no attribute '{name}'") from None

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            super().__setattr__(name, value)
        else:
            self._data[name] = value

    def __contains__(self, name: str) -> bool:
        return name in self._data

    def get(self, name: str, default: Any = None) -> Any:
        return self._data.get(name, default)

    def snapshot(self) -> dict[str, Any]:
        """Return a deep-frozen copy of the context data."""
        return copy.deepcopy(self._data)

    def merge(self, updates: dict[str, Any]) -> None:
        """Merge step output diff into the context."""
        self._data.update(updates)

    def to_dict(self) -> dict[str, Any]:
        """Serialize context to a plain dict (for checkpointing)."""
        return copy.deepcopy(self._data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Context:
        """Restore context from a serialized dict."""
        ctx = cls()
        ctx._data = copy.deepcopy(data)
        return ctx

    def summary(self) -> str:
        """Human-readable summary of context keys."""
        keys = list(self._data.keys())
        if not keys:
            return "Context(empty)"
        return f"Context({', '.join(keys)})"
