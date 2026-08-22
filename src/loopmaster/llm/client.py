"""LLM client for LoopMaster — supports multiple providers with typed exceptions."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("loopmaster.llm")


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
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout: float = 120.0


@dataclass
class LLMResponse:
    """Structured response from LLM API including token metrics."""

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    duration_ms: float = 0.0


# ── Configuration Helpers ───────────────────────────────────────────────────


def _get_base_url(provider: str) -> str:
    custom = os.environ.get(f"LOOPMASTER_{provider.upper()}_BASE_URL")
    if custom:
        return custom
    defaults = {
        "openai": "https://api.openai.com/v1",
        "anthropic": "https://api.anthropic.com",
        "google": "https://generativelanguage.googleapis.com/v1beta",
        "openrouter": "https://openrouter.ai/api/v1",
    }
    return defaults.get(provider, "")


def _default_model(provider: str) -> str:
    defaults = {
        "openai": "gpt-4o",
        "anthropic": "claude-3-5-sonnet-20241022",
        "google": "gemini-1.5-pro",
        "openrouter": "openai/gpt-4o",
    }
    return defaults.get(provider, "gpt-4o")


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
            "No LLM API key found for provider '%s'. Set LOOPMASTER_%s_API_KEY or %s_API_KEY",
            provider,
            provider.upper(),
            provider.upper(),
        )
        return None
    base_url = _get_base_url(provider)
    model = model_override or os.environ.get("LOOPMASTER_LLM_MODEL") or _default_model(provider)
    return LLMConfig(provider=provider, api_key=api_key, base_url=base_url, model=model)


# ── LLM Client ───────────────────────────────────────────────────────────────


class LLMClient:
    """Client for executing synchronous LLM API calls."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        config: LLMConfig | None = None,
    ) -> LLMResponse:
        """Call LLM API and return structured response."""
        cfg = config or self.config or get_llm_config(model_override=model)
        if not cfg:
            raise AuthenticationError(
                "No LLM configuration found. Set environment variables for your LLM provider."
            )

        if model and model != cfg.model:
            cfg = LLMConfig(
                provider=cfg.provider,
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                model=model,
                max_tokens=cfg.max_tokens,
                temperature=cfg.temperature,
                timeout=cfg.timeout,
            )

        start = time.monotonic()
        try:
            if cfg.provider == "anthropic":
                resp = self._complete_anthropic(cfg, prompt, system)
            elif cfg.provider == "google":
                resp = self._complete_google(cfg, prompt, system)
            else:
                resp = self._complete_openai_compatible(cfg, prompt, system)
            resp.duration_ms = (time.monotonic() - start) * 1000
            return resp
        except urllib.error.HTTPError as exc:
            body = ""
            with contextlib.suppress(Exception):
                body = exc.read().decode("utf-8")
            if exc.code == 429:
                raise RateLimitError(
                    f"Rate limit exceeded (HTTP 429): {body or exc.reason}"
                ) from exc
            if exc.code in (401, 403):
                raise AuthenticationError(
                    f"Authentication failed (HTTP {exc.code}): {body or exc.reason}"
                ) from exc
            if exc.code in (408, 504):
                raise TimeoutError(
                    f"Request timed out (HTTP {exc.code}): {body or exc.reason}"
                ) from exc
            raise ProviderAPIError(
                f"API request failed with HTTP {exc.code}: {body or exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise TimeoutError(f"LLM request timed out: {exc}") from exc
        except urllib.error.URLError as exc:
            if "timed out" in str(exc).lower():
                raise TimeoutError(f"LLM connection timed out: {exc}") from exc
            raise LLMError(f"LLM connection error: {exc}") from exc
        except Exception as exc:
            if isinstance(exc, LLMError):
                raise
            raise LLMError(f"LLM call failed: {exc}") from exc

    def _complete_openai_compatible(
        self,
        config: LLMConfig,
        prompt: str,
        system: str | None = None,
    ) -> LLMResponse:
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps(
            {
                "model": config.model,
                "messages": messages,
                "max_tokens": config.max_tokens,
                "temperature": config.temperature,
            }
        ).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        }
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=config.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model=data.get("model", config.model),
        )

    def _complete_anthropic(
        self,
        config: LLMConfig,
        prompt: str,
        system: str | None = None,
    ) -> LLMResponse:
        url = f"{config.base_url.rstrip('/')}/v1/messages"
        body: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system

        payload = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01",
        }
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=config.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        content = data["content"][0]["text"]
        usage = data.get("usage", {})
        prompt_tokens = usage.get("input_tokens", 0)
        completion_tokens = usage.get("output_tokens", 0)
        total_tokens = prompt_tokens + completion_tokens

        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model=data.get("model", config.model),
        )

    def _complete_google(
        self,
        config: LLMConfig,
        prompt: str,
        system: str | None = None,
    ) -> LLMResponse:
        model = config.model
        url = f"{config.base_url.rstrip('/')}/models/{model}:generateContent?key={config.api_key}"
        parts = [{"text": prompt}]
        body: dict[str, Any] = {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "temperature": config.temperature,
                "maxOutputTokens": config.max_tokens,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        payload = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=config.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        content = data["candidates"][0]["content"]["parts"][0]["text"]
        usage = data.get("usageMetadata", {})
        prompt_tokens = usage.get("promptTokenCount", 0)
        completion_tokens = usage.get("candidatesTokenCount", 0)
        total_tokens = usage.get("totalTokenCount", prompt_tokens + completion_tokens)

        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model=config.model,
        )


# Global default complete function
def complete(
    config: LLMConfig,
    prompt: str,
    system: str | None = None,
) -> str:
    """Convenience helper for string completion."""
    client = LLMClient(config=config)
    resp = client.complete(prompt=prompt, system=system)
    return resp.content
