"""Multi-provider streaming parsers for OpenAI, Anthropic, and Google Gemini."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

from .types import LLMConfig, StreamChunk

logger = logging.getLogger("loopmaster.llm.streaming")


def stream_openai_compatible(
    config: LLMConfig,
    prompt: str,
    system: str | None = None,
) -> Iterator[StreamChunk]:
    """Stream completions from OpenAI-compatible SSE endpoints."""
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
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    payload = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.api_key}",
        **config.extra_headers,
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
            if line.startswith("data: "):
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
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


class _AnthropicStreamParser:
    """Table-driven SSE parser for Anthropic messages protocol."""

    def __init__(self, model: str) -> None:
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0
        self.model: str = model

    def handle_message_start(self, data: dict[str, Any]) -> StreamChunk | None:
        msg = data.get("message", {})
        self.model = msg.get("model", self.model)
        usage = msg.get("usage", {})
        self.prompt_tokens = usage.get("input_tokens", self.prompt_tokens)
        return None

    def handle_content_block_delta(self, data: dict[str, Any]) -> StreamChunk | None:
        delta_dict = data.get("delta")
        delta_text = delta_dict.get("text", "") if isinstance(delta_dict, dict) else ""
        if not delta_text:
            return None
        return StreamChunk(
            delta=delta_text,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.prompt_tokens + self.completion_tokens,
            is_final=False,
            model=self.model,
        )

    def handle_message_delta(self, data: dict[str, Any]) -> StreamChunk | None:
        usage = data.get("usage", {})
        self.completion_tokens = usage.get("output_tokens", self.completion_tokens)
        self.total_tokens = self.prompt_tokens + self.completion_tokens
        return None

    def handle_message_stop(self, _data: dict[str, Any]) -> StreamChunk | None:
        return StreamChunk(
            delta="",
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens or (self.prompt_tokens + self.completion_tokens),
            is_final=True,
            model=self.model,
        )


def stream_anthropic(
    config: LLMConfig,
    prompt: str,
    system: str | None = None,
) -> Iterator[StreamChunk]:
    """Stream completions from Anthropic SSE endpoint."""
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
    parser = _AnthropicStreamParser(model=config.model)
    dispatch_table = {
        "message_start": parser.handle_message_start,
        "content_block_delta": parser.handle_content_block_delta,
        "message_delta": parser.handle_message_delta,
        "message_stop": parser.handle_message_stop,
    }
    try:
        current_event: str = ""
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

                handler = dispatch_table.get(current_event)
                if handler:
                    chunk = handler(data)
                    if chunk is not None:
                        yield chunk
    finally:
        resp.close()


def stream_google(
    config: LLMConfig,
    prompt: str,
    system: str | None = None,
) -> Iterator[StreamChunk]:
    """Stream completions from Google Gemini SSE endpoint."""
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
                    total_tokens = usage.get("totalTokenCount", prompt_tokens + completion_tokens)

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
