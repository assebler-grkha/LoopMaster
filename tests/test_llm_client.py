"""Tests for loopmaster.llm package — client, config, and error handling."""

from __future__ import annotations

import io
import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from loopmaster.llm import (
    AuthenticationError,
    LLMClient,
    LLMConfig,
    LLMError,
    ProviderAPIError,
    RateLimitError,
    TimeoutError,
    complete,
    get_llm_config,
)


class TestLLMConfig:
    def test_get_llm_config_from_env(self, monkeypatch):
        monkeypatch.setenv("LOOPMASTER_LLM_PROVIDER", "openai")
        monkeypatch.setenv("LOOPMASTER_OPENAI_API_KEY", "sk-test-123")
        monkeypatch.setenv("LOOPMASTER_LLM_MODEL", "gpt-4o")

        config = get_llm_config()
        assert config is not None
        assert config.provider == "openai"
        assert config.api_key == "sk-test-123"
        assert config.model == "gpt-4o"
        assert "openai.com" in config.base_url

    def test_get_llm_config_fallback_key(self, monkeypatch):
        monkeypatch.delenv("LOOPMASTER_OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("LOOPMASTER_LLM_PROVIDER", "openai")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-direct")

        config = get_llm_config()
        assert config is not None
        assert config.api_key == "sk-openai-direct"

    def test_get_llm_config_missing_key(self, monkeypatch):
        monkeypatch.delenv("LOOPMASTER_OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("LOOPMASTER_LLM_API_KEY", raising=False)
        monkeypatch.setenv("LOOPMASTER_LLM_PROVIDER", "openai")

        config = get_llm_config()
        assert config is None


class TestLLMClientProviders:
    def test_openai_compatible_response_parsing(self):
        config = LLMConfig(
            provider="openai",
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
        )
        client = LLMClient(config=config)

        mock_resp_data = {
            "id": "chatcmpl-123",
            "model": "gpt-4o-2024-08-06",
            "choices": [{"message": {"role": "assistant", "content": "Hello from OpenAI"}}],
            "usage": {"prompt_tokens": 15, "completion_tokens": 25, "total_tokens": 40},
        }
        mock_resp = io.BytesIO(json.dumps(mock_resp_data).encode("utf-8"))

        with patch("urllib.request.urlopen", return_value=mock_resp):
            resp = client.complete(prompt="Hi")
            assert resp.content == "Hello from OpenAI"
            assert resp.prompt_tokens == 15
            assert resp.completion_tokens == 25
            assert resp.total_tokens == 40
            assert resp.model == "gpt-4o-2024-08-06"
            assert resp.duration_ms >= 0.0

    def test_anthropic_response_parsing(self):
        config = LLMConfig(
            provider="anthropic",
            api_key="sk-ant-test",
            base_url="https://api.anthropic.com",
            model="claude-3-5-sonnet-20241022",
        )
        client = LLMClient(config=config)

        mock_resp_data = {
            "id": "msg-123",
            "model": "claude-3-5-sonnet-20241022",
            "content": [{"type": "text", "text": "Hello from Claude"}],
            "usage": {"input_tokens": 12, "output_tokens": 18},
        }
        mock_resp = io.BytesIO(json.dumps(mock_resp_data).encode("utf-8"))

        with patch("urllib.request.urlopen", return_value=mock_resp):
            resp = client.complete(prompt="Hi", system="Be helpful")
            assert resp.content == "Hello from Claude"
            assert resp.prompt_tokens == 12
            assert resp.completion_tokens == 18
            assert resp.total_tokens == 30

    def test_google_response_parsing(self):
        config = LLMConfig(
            provider="google",
            api_key="gemini-key",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            model="gemini-1.5-pro",
        )
        client = LLMClient(config=config)

        mock_resp_data = {
            "candidates": [{"content": {"parts": [{"text": "Hello from Gemini"}]}}],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 20,
                "totalTokenCount": 30,
            },
        }
        mock_resp = io.BytesIO(json.dumps(mock_resp_data).encode("utf-8"))

        with patch("urllib.request.urlopen", return_value=mock_resp):
            resp = client.complete(prompt="Hi")
            assert resp.content == "Hello from Gemini"
            assert resp.prompt_tokens == 10
            assert resp.completion_tokens == 20
            assert resp.total_tokens == 30

    def test_convenience_complete_helper(self):
        config = LLMConfig(
            provider="openai",
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
        )
        mock_resp_data = {
            "choices": [{"message": {"content": "Direct text"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 5},
        }
        with patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(mock_resp_data).encode("utf-8"))):
            text = complete(config, "Hello")
            assert text == "Direct text"


class TestLLMErrorMapping:
    def test_rate_limit_error_429(self):
        config = LLMConfig(
            provider="openai",
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
        )
        client = LLMClient(config=config)

        http_err = urllib.error.HTTPError(
            url="http://api.openai.com",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=io.BytesIO(b'{"error": "rate limit"}'),
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            with pytest.raises(RateLimitError) as exc_info:
                client.complete("Hi")
            assert "429" in str(exc_info.value) or "Rate limit" in str(exc_info.value)

    def test_authentication_error_401(self):
        config = LLMConfig(
            provider="openai",
            api_key="sk-bad",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
        )
        client = LLMClient(config=config)

        http_err = urllib.error.HTTPError(
            url="http://api.openai.com",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=io.BytesIO(b'{"error": "invalid api key"}'),
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            with pytest.raises(AuthenticationError):
                client.complete("Hi")

    def test_timeout_error_504(self):
        config = LLMConfig(
            provider="openai",
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
        )
        client = LLMClient(config=config)

        http_err = urllib.error.HTTPError(
            url="http://api.openai.com",
            code=504,
            msg="Gateway Timeout",
            hdrs={},
            fp=io.BytesIO(b"Gateway Timeout"),
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            with pytest.raises(TimeoutError):
                client.complete("Hi")

    def test_socket_timeout_mapping(self):
        config = LLMConfig(
            provider="openai",
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
        )
        client = LLMClient(config=config)

        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with pytest.raises(TimeoutError):
                client.complete("Hi")
