"""Data types, configuration, and exceptions for LoopMaster LLM client."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("loopmaster.llm.types")


# ── Exceptions ───────────────────────────────────────────────────────────────


class LLMError(Exception):
    """Base exception for LLM client errors."""


class RateLimitError(LLMError):
    """Raised when the LLM provider returns a rate limit (HTTP 429)."""


class TimeoutError(LLMError):
    """Raised when an LLM request times out."""


class AuthenticationError(LLMError):
    """Raised when authentication fails (HTTP 401/403)."""


class ProviderAPIError(LLMError):
    """Raised when the provider API returns a non-2xx status code."""


# ── Data Models ──────────────────────────────────────────────────────────────


@dataclass
class LLMConfig:
    """Configuration for LLM API connection."""

    provider: str
    api_key: str
    base_url: str
    model: str
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: float = 60.0
    extra_headers: dict[str, str] = field(default_factory=dict)


@dataclass
class LLMResponse:
    """Structured response from LLM complete() call."""

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    duration_ms: float = 0.0
    raw_response: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamChunk:
    """A streaming token chunk emitted during real-time generation."""

    delta: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    is_final: bool = False
    model: str = ""


# ── Defaults & Configuration Helpers ─────────────────────────────────────────

DEFAULT_PROVIDER_BASE_URLS: dict[str, str] = {
    "openai": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "anthropic": os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
    "google": os.getenv("GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"),
    "openrouter": os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
}

DEFAULT_PROVIDER_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet-20241022",
    "google": "gemini-1.5-pro",
    "openrouter": "openai/gpt-4o",
}


def get_base_url(provider: str) -> str:
    """Get base URL for provider, with environment override support."""
    custom = os.environ.get(f"LOOPMASTER_{provider.upper()}_BASE_URL")
    if custom:
        return custom
    return DEFAULT_PROVIDER_BASE_URLS.get(provider, "")


def get_default_model(provider: str) -> str:
    """Get default model for provider."""
    return DEFAULT_PROVIDER_MODELS.get(provider, "gpt-4o")


def get_llm_config(
    model_override: str | None = None,
    provider_override: str | None = None,
) -> LLMConfig | None:
    """Resolve LLMConfig from environment variables or overrides."""
    provider = provider_override or os.environ.get("LOOPMASTER_LLM_PROVIDER", "openai").lower()
    api_key = (
        os.environ.get(f"LOOPMASTER_{provider.upper()}_API_KEY")
        or os.environ.get(f"{provider.upper()}_API_KEY")
        or os.environ.get("LOOPMASTER_LLM_API_KEY")
    )
    if not api_key:
        logger.warning(
            "No API key found for provider '%s' (checked LOOPMASTER_%s_API_KEY, %s_API_KEY)",
            provider,
            provider.upper(),
            provider.upper(),
        )
        return None

    base_url = get_base_url(provider)
    model = (
        model_override
        or os.environ.get(f"LOOPMASTER_{provider.upper()}_MODEL")
        or os.environ.get("LOOPMASTER_LLM_MODEL")
        or get_default_model(provider)
    )

    return LLMConfig(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
    )
