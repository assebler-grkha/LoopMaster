"""Tests for agents/ — PromptManager."""

from __future__ import annotations

from loopmaster.agents.prompt_manager import PromptManager


class TestPromptManager:
    def test_inject_new(self):
        pm = PromptManager()
        original = "You are a helpful assistant."
        result = pm.inject(original, "Loop instructions here")
        assert "LOOP_ENGINEER:start" in result
        assert "Loop instructions here" in result
        assert "You are a helpful assistant." in result

    def test_inject_replaces_existing(self):
        pm = PromptManager()
        original = (
            "System prompt\n<!-- LOOP_ENGINEER:start -->\n"
            "old instructions\n<!-- LOOP_ENGINEER:end -->\nMore text"
        )
        result = pm.inject(original, "new instructions")
        assert "new instructions" in result
        assert "old instructions" not in result
        assert "More text" in result
        assert "System prompt" in result

    def test_restore_original(self):
        pm = PromptManager()
        original = "You are a helpful assistant."
        injected = pm.inject(original, "Loop instructions")
        restored = pm.restore_original(injected)
        assert "LOOP_ENGINEER" not in restored
        assert "You are a helpful assistant." in restored

    def test_restore_no_markers(self):
        pm = PromptManager()
        text = "No markers here"
        result = pm.restore_original(text)
        assert result.strip() == text

    def test_has_markers(self):
        pm = PromptManager()
        assert pm.has_markers("has <!-- LOOP_ENGINEER:start --> markers") is True
        assert pm.has_markers("no markers") is False

    def test_extract_section(self):
        pm = PromptManager()
        text = (
            "Before\n<!-- LOOP_ENGINEER:start -->\n"
            "important content\n<!-- LOOP_ENGINEER:end -->\nAfter"
        )
        section = pm.extract_section(text)
        assert section is not None
        assert "important content" in section

    def test_extract_section_none(self):
        pm = PromptManager()
        assert pm.extract_section("no markers") is None

    def test_inject_empty_original(self):
        pm = PromptManager()
        result = pm.inject("", "instructions")
        assert "LOOP_ENGINEER:start" in result
        assert "instructions" in result

    def test_roundtrip(self):
        pm = PromptManager()
        original = "Original system prompt.\nBe helpful."
        instructions = "Run loop X with budget $5"
        injected = pm.inject(original, instructions)
        restored = pm.restore_original(injected)
        assert restored.strip() == original.strip()
