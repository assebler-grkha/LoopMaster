"""Utils module — LLM provider abstraction and serialization helpers."""

from __future__ import annotations

import hashlib
import json
import textwrap
from abc import ABC, abstractmethod
from dataclasses import asdict, is_dataclass
from typing import Any


class LLMProvider(ABC):
    """Abstract base for LLM providers.

    Implementations should handle authentication, rate limiting,
    and response parsing for a specific LLM API.
    """

    @abstractmethod
    def complete(
        self,
        prompt: str,
        model: str,
        *,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> str:
        """Send a prompt and return the completion text."""
        ...

    @abstractmethod
    def count_tokens(self, text: str, model: str) -> int:
        """Estimate token count for a given text."""
        ...

    @abstractmethod
    def models(self) -> list[str]:
        """List available models."""
        ...


def serialize(obj: Any) -> Any:
    """Convert an object to a JSON-serializable form.

    Handles dataclasses, dicts, lists, and primitive types.
    """
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize(item) for item in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    if hasattr(obj, "__dict__"):
        return serialize(obj.__dict__)
    return str(obj)


def hash_data(data: Any) -> str:
    """Compute a deterministic SHA-256 hash of arbitrary data."""
    serialized = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def dedent(text: str) -> str:
    """Strip common leading whitespace from a multiline string."""
    return textwrap.dedent(text).strip()
