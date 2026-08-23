"""LLM client for LoopMaster — synchronous and streaming completions."""

from __future__ import annotations

import contextlib
import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

from .streaming import stream_anthropic, stream_google, stream_openai_compatible
from .types import (
    AuthenticationError,
    LLMConfig,
    LLMError,
    LLMResponse,
    ProviderAPIError,
    RateLimitError,
    StreamChunk,
    TimeoutError,
    get_llm_config,
)

logger = logging.getLogger("loopmaster.llm")


class LLMClient:
    """Client for calling LLM APIs with unified interface and error handling."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or get_llm_config()

    def complete(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """Call LLM synchronously and return structured response."""
        config = self._resolve_config(model)
        start_time = time.monotonic()
        try:
            if config.provider in ("openai", "openrouter", "custom"):
                resp = self._complete_openai_compatible(config, prompt, system)
            elif config.provider == "anthropic":
                resp = self._complete_anthropic(config, prompt, system)
            elif config.provider == "google":
                resp = self._complete_google(config, prompt, system)
            else:
                resp = self._complete_openai_compatible(config, prompt, system)

            resp.duration_ms = (time.monotonic() - start_time) * 1000
            return resp
        except urllib.error.HTTPError as exc:
            self._handle_http_error(exc, config.provider)
            raise
        except (TimeoutError, urllib.error.URLError) as exc:
            if isinstance(exc, urllib.error.URLError) and "timed out" in str(exc.reason).lower():
                raise TimeoutError(f"Request to {config.provider} timed out: {exc}") from exc
            if isinstance(exc, TimeoutError):
                raise
            raise ProviderAPIError(f"Network error calling {config.provider}: {exc}") from exc
        except Exception as exc:
            if isinstance(exc, LLMError):
                raise
            raise ProviderAPIError(f"Unexpected error calling {config.provider}: {exc}") from exc

    def stream_complete(
        self,
        prompt: str,
        system: str | None = None,
        model: str | None = None,
    ) -> Iterator[StreamChunk]:
        """Stream token chunks in real-time from LLM API."""
        config = self._resolve_config(model)
        try:
            if config.provider in ("openai", "openrouter", "custom"):
                yield from stream_openai_compatible(config, prompt, system)
            elif config.provider == "anthropic":
                yield from stream_anthropic(config, prompt, system)
            elif config.provider == "google":
                yield from stream_google(config, prompt, system)
            else:
                yield from stream_openai_compatible(config, prompt, system)
        except urllib.error.HTTPError as exc:
            self._handle_http_error(exc, config.provider)
        except (TimeoutError, urllib.error.URLError) as exc:
            if isinstance(exc, urllib.error.URLError) and "timed out" in str(exc.reason).lower():
                raise TimeoutError(f"Request to {config.provider} timed out: {exc}") from exc
            if isinstance(exc, TimeoutError):
                raise
            raise ProviderAPIError(f"Network error streaming {config.provider}: {exc}") from exc
        except Exception as exc:
            if isinstance(exc, LLMError):
                raise
            raise ProviderAPIError(f"Unexpected error streaming {config.provider}: {exc}") from exc

    def _resolve_config(self, model_override: str | None = None) -> LLMConfig:
        if not self.config:
            self.config = get_llm_config()
        if not self.config:
            raise AuthenticationError(
                "No LLM API key configured. Set LOOPMASTER_LLM_API_KEY or provider-specific key."
            )
        if model_override and model_override != self.config.model:
            return LLMConfig(
                provider=self.config.provider,
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                model=model_override,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                timeout=self.config.timeout,
                extra_headers=self.config.extra_headers,
            )
        return self.config

    def _complete_openai_compatible(
        self,
        config: LLMConfig,
        prompt: str,
        system: str | None = None,
    ) -> LLMResponse:
        url = f"{config.base_url.rstrip('/')}/chat/completions"
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        body: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        data = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
            **config.extra_headers,
        }
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=config.timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        content = ""
        choices = result.get("choices", [])
        if choices and isinstance(choices[0], dict):
            msg = choices[0].get("message")
            if isinstance(msg, dict):
                content = msg.get("content", "") or ""

        usage = result.get("usage", {})
        return LLMResponse(
            content=content,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
            model=result.get("model", config.model),
            raw_response=result,
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

        data = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": config.api_key,
            "anthropic-version": "2023-06-01",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=config.timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        content = ""
        for block in result.get("content", []):
            if isinstance(block, dict) and block.get("type") == "text":
                content += block.get("text", "")

        usage = result.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        return LLMResponse(
            content=content,
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            model=result.get("model", config.model),
            raw_response=result,
        )

    def _complete_google(
        self,
        config: LLMConfig,
        prompt: str,
        system: str | None = None,
    ) -> LLMResponse:
        model = config.model
        base = config.base_url.rstrip("/")
        url = f"{base}/models/{model}:generateContent?key={config.api_key}"
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

        data = json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=config.timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))

        content = ""
        candidates = result.get("candidates", [])
        if candidates and isinstance(candidates[0], dict):
            cand_content = candidates[0].get("content")
            if isinstance(cand_content, dict):
                parts_list = cand_content.get("parts", [])
                if parts_list and isinstance(parts_list[0], dict):
                    content = parts_list[0].get("text", "") or ""

        usage = result.get("usageMetadata", {})
        prompt_tokens = usage.get("promptTokenCount", 0)
        completion_tokens = usage.get("candidatesTokenCount", 0)
        total_tokens = usage.get("totalTokenCount", prompt_tokens + completion_tokens)
        return LLMResponse(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model=config.model,
            raw_response=result,
        )

    @staticmethod
    def _handle_http_error(exc: urllib.error.HTTPError, provider: str) -> None:
        body_str = ""
        with contextlib.suppress(Exception):
            body_str = exc.read().decode("utf-8", errors="replace")

        code = exc.code
        if code == 429:
            raise RateLimitError(
                f"Rate limit exceeded for {provider} (HTTP 429): {body_str or exc.reason}"
            ) from exc
        if code in (401, 403):
            raise AuthenticationError(
                f"Authentication failed for {provider} (HTTP {code}): {body_str or exc.reason}"
            ) from exc
        if code in (408, 504):
            raise TimeoutError(
                f"Request to {provider} timed out (HTTP {code}): {body_str or exc.reason}"
            ) from exc
        raise ProviderAPIError(
            f"{provider} API returned HTTP {code}: {body_str or exc.reason}"
        ) from exc


def complete(
    config: LLMConfig,
    prompt: str,
    system: str | None = None,
) -> str:
    """Convenience helper for string completion."""
    client = LLMClient(config=config)
    resp = client.complete(prompt=prompt, system=system)
    return resp.content
