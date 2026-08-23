"""LLM client for LoopMaster — supports multiple providers with typed exceptions."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
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


@dataclass
class StreamChunk:
    """Incremental chunk emitted during LLM streaming."""

    delta: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    is_final: bool = False
    model: str = ""


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

    def stream_complete(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
        config: LLMConfig | None = None,
    ) -> Iterator[StreamChunk]:
        """Stream completion chunks from LLM API."""
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

        try:
            if cfg.provider == "anthropic":
                yield from self._stream_anthropic(cfg, prompt, system)
            elif cfg.provider == "google":
                yield from self._stream_google(cfg, prompt, system)
            else:
                yield from self._stream_openai_compatible(cfg, prompt, system)
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
            raise LLMError(f"LLM streaming failed: {exc}") from exc

    def _stream_openai_compatible(
        self,
        config: LLMConfig,
        prompt: str,
        system: str | None = None,
    ) -> Iterator[StreamChunk]:
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
                "stream": True,
                "stream_options": {"include_usage": True},
            }
        ).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        }
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        resp = urllib.request.urlopen(req, timeout=config.timeout)
        try:
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0
            model_used = config.model

            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line or line.startswith(":"):
                    continue
                if line == "data: [DONE]":
                    break
                if line.startswith("data: "):
                    data_str = line[6:]
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    model_used = data.get("model", model_used)
                    choices = data.get("choices", [])
                    delta = ""
                    finish_reason = None
                    if choices and isinstance(choices[0], dict):
                        delta_dict = choices[0].get("delta")
                        if isinstance(delta_dict, dict):
                            delta = delta_dict.get("content", "") or ""
                        finish_reason = choices[0].get("finish_reason")

                    usage = data.get("usage")
                    if usage:
                        prompt_tokens = usage.get("prompt_tokens", prompt_tokens)
                        completion_tokens = usage.get("completion_tokens", completion_tokens)
                        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

                    is_final = bool(
                        usage is not None or (finish_reason is not None and finish_reason != "null")
                    )

                    if delta or is_final:
                        yield StreamChunk(
                            delta=delta,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=total_tokens,
                            is_final=is_final,
                            model=model_used,
                        )
        finally:
            resp.close()

    def _stream_anthropic(
        self,
        config: LLMConfig,
        prompt: str,
        system: str | None = None,
    ) -> Iterator[StreamChunk]:
        url = f"{config.base_url.rstrip('/')}/v1/messages"
        body: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
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
        resp = urllib.request.urlopen(req, timeout=config.timeout)
        try:
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0
            model_used = config.model
            current_event = ""

            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("event: "):
                    current_event = line[7:].strip()
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if current_event == "message_start":
                        msg = data.get("message", {})
                        model_used = msg.get("model", model_used)
                        usage = msg.get("usage", {})
                        prompt_tokens = usage.get("input_tokens", prompt_tokens)
                    elif current_event == "content_block_delta":
                        delta_dict = data.get("delta")
                        delta_text = (
                            delta_dict.get("text", "") if isinstance(delta_dict, dict) else ""
                        )
                        if delta_text:
                            yield StreamChunk(
                                delta=delta_text,
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                total_tokens=prompt_tokens + completion_tokens,
                                is_final=False,
                                model=model_used,
                            )
                    elif current_event == "message_delta":
                        usage = data.get("usage", {})
                        completion_tokens = usage.get("output_tokens", completion_tokens)
                        total_tokens = prompt_tokens + completion_tokens
                    elif current_event == "message_stop":
                        yield StreamChunk(
                            delta="",
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=total_tokens or (prompt_tokens + completion_tokens),
                            is_final=True,
                            model=model_used,
                        )
        finally:
            resp.close()

    def _stream_google(
        self,
        config: LLMConfig,
        prompt: str,
        system: str | None = None,
    ) -> Iterator[StreamChunk]:
        model = config.model
        base = config.base_url.rstrip("/")
        url = f"{base}/models/{model}:streamGenerateContent?alt=sse&key={config.api_key}"
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
        resp = urllib.request.urlopen(req, timeout=config.timeout)
        try:
            prompt_tokens = 0
            completion_tokens = 0
            total_tokens = 0

            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    candidates = data.get("candidates", [])
                    delta = ""
                    finish_reason = None
                    if candidates:
                        content = candidates[0].get("content", {})
                        parts_list = content.get("parts", [])
                        if parts_list:
                            delta = parts_list[0].get("text", "")
                        finish_reason = candidates[0].get("finishReason")

                    usage = data.get("usageMetadata", {})
                    if usage:
                        prompt_tokens = usage.get("promptTokenCount", prompt_tokens)
                        completion_tokens = usage.get("candidatesTokenCount", completion_tokens)
                        total_tokens = usage.get(
                            "totalTokenCount", prompt_tokens + completion_tokens
                        )

                    is_final = bool(finish_reason is not None and finish_reason != "null")
                    if delta or is_final:
                        yield StreamChunk(
                            delta=delta,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=total_tokens or (prompt_tokens + completion_tokens),
                            is_final=is_final,
                            model=config.model,
                        )
        finally:
            resp.close()


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
