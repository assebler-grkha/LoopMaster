"""Tests for multi-provider LLM streaming in loopmaster.llm package."""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from loopmaster.llm import (
    LLMClient,
    LLMConfig,
    RateLimitError,
)


class TestLLMStreaming:
    def test_openai_streaming(self):
        config = LLMConfig(
            provider="openai",
            api_key="sk-test-key",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
        )
        client = LLMClient(config=config)

        sse_body = (
            b": keep-alive\n\n"
            b'data: {"model": "gpt-4o", "choices": [{"delta": {"content": "Hello"}}]}\n\n'
            b'data: {"model": "gpt-4o", "choices": [{"delta": {"content": " World!"}}]}\n\n'
            b'data: {"model": "gpt-4o", "choices": [{"delta": {}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}}\n\n'
            b"data: [DONE]\n\n"
        )

        mock_resp = MagicMock()
        mock_resp.__iter__.return_value = sse_body.splitlines(keepends=True)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            chunks = list(client.stream_complete("Say hello"))

        assert len(chunks) == 3
        assert chunks[0].delta == "Hello"
        assert chunks[0].is_final is False
        assert chunks[1].delta == " World!"
        assert chunks[2].is_final is True
        assert chunks[2].prompt_tokens == 8
        assert chunks[2].completion_tokens == 4
        assert chunks[2].total_tokens == 12

    def test_anthropic_streaming(self):
        config = LLMConfig(
            provider="anthropic",
            api_key="sk-ant-test",
            base_url="https://api.anthropic.com",
            model="claude-3-5-sonnet",
        )
        client = LLMClient(config=config)

        sse_body = (
            b"event: message_start\n"
            b'data: {"type": "message_start", "message": {"model": "claude-3-5-sonnet", "usage": {"input_tokens": 15}}}\n\n'
            b"event: content_block_delta\n"
            b'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Anthropic"}}\n\n'
            b"event: content_block_delta\n"
            b'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": " stream"}}\n\n'
            b"event: message_delta\n"
            b'data: {"type": "message_delta", "usage": {"output_tokens": 6}}\n\n'
            b"event: message_stop\n"
            b'data: {"type": "message_stop"}\n\n'
        )

        mock_resp = MagicMock()
        mock_resp.__iter__.return_value = sse_body.splitlines(keepends=True)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            chunks = list(client.stream_complete("Hello Claude"))

        assert len(chunks) == 3
        assert chunks[0].delta == "Anthropic"
        assert chunks[1].delta == " stream"
        assert chunks[2].is_final is True
        assert chunks[2].prompt_tokens == 15
        assert chunks[2].completion_tokens == 6
        assert chunks[2].total_tokens == 21

    def test_google_streaming(self):
        config = LLMConfig(
            provider="google",
            api_key="test-gemini-key",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            model="gemini-1.5-pro",
        )
        client = LLMClient(config=config)

        sse_body = (
            b'data: {"candidates": [{"content": {"parts": [{"text": "Gemini"}]}}]}\n\n'
            b'data: {"candidates": [{"content": {"parts": [{"text": " response"}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15}}\n\n'
        )

        mock_resp = MagicMock()
        mock_resp.__iter__.return_value = sse_body.splitlines(keepends=True)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            chunks = list(client.stream_complete("Hello Gemini"))

        assert len(chunks) == 2
        assert chunks[0].delta == "Gemini"
        assert chunks[1].delta == " response"
        assert chunks[1].is_final is True
        assert chunks[1].prompt_tokens == 10
        assert chunks[1].completion_tokens == 5
        assert chunks[1].total_tokens == 15

    def test_streaming_error_mapping(self):
        import urllib.error

        config = LLMConfig(
            provider="openai",
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
            model="gpt-4o",
        )
        client = LLMClient(config=config)

        http_err_429 = urllib.error.HTTPError(
            url="http://api.openai.com",
            code=429,
            msg="Rate limit",
            hdrs={},
            fp=io.BytesIO(b'{"error": "rate limit exceeded"}'),
        )

        with patch("urllib.request.urlopen", side_effect=http_err_429):
            with pytest.raises(RateLimitError):
                list(client.stream_complete("Hi"))
