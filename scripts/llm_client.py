"""LLM client for LoopMaster — supports multiple providers via env vars."""

import os
import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass

logger = logging.getLogger("loopmaster-mcp")


@dataclass
class LLMConfig:
    provider: str
    api_key: str
    base_url: str
    model: str
    max_tokens: int = 4096
    temperature: float = 0.7


def get_llm_config(model_override: str | None = None) -> LLMConfig | None:
    provider = os.environ.get("LOOPMASTER_LLM_PROVIDER", "openai")
    api_key = (
        os.environ.get(f"LOOPMASTER_{provider.upper()}_API_KEY")
        or os.environ.get("LOOPMASTER_LLM_API_KEY")
    )
    if not api_key:
        logger.warning("No LLM API key found. Set LOOPMASTER_LLM_API_KEY or LOOPMASTER_%s_API_KEY", provider.upper())
        return None
    base_url = _get_base_url(provider)
    model = model_override or os.environ.get("LOOPMASTER_LLM_MODEL") or _default_model(provider)
    return LLMConfig(provider=provider, api_key=api_key, base_url=base_url, model=model)


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
        "openai": "gpt-4",
        "anthropic": "claude-3-sonnet-20240229",
        "google": "gemini-pro",
        "openrouter": "openai/gpt-4",
    }
    return defaults.get(provider, "gpt-4")


def complete(config: LLMConfig, prompt: str, system: str | None = None) -> str:
    """Call LLM API synchronously."""
    if config.provider == "anthropic":
        return _complete_anthropic(config, prompt, system)
    return _complete_openai_compatible(config, prompt, system)


def _complete_openai_compatible(config: LLMConfig, prompt: str, system: str | None = None) -> str:
    url = f"{config.base_url}/chat/completions"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = json.dumps({
        "model": config.model,
        "messages": messages,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.api_key}",
    }
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def _complete_anthropic(config: LLMConfig, prompt: str, system: str | None = None) -> str:
    url = f"{config.base_url}/v1/messages"
    body = {
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
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["content"][0]["text"]
