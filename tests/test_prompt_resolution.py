"""Tests for prompt variable resolution."""

from __future__ import annotations

from loopmaster.core.types import resolve_prompt


class TestPromptResolution:
    def test_single_curly_brace_resolution(self):
        prompt = "Hello {name}, welcome to {place}!"
        ctx = {"name": "Alice", "place": "Wonderland"}
        res = resolve_prompt(prompt, ctx)
        assert res == "Hello Alice, welcome to Wonderland!"

    def test_double_curly_brace_resolution(self):
        prompt = "Summarize findings: {{greet}} and {{task}}"
        ctx = {"greet": "Hello World", "task": "Write Code"}
        res = resolve_prompt(prompt, ctx)
        assert res == "Summarize findings: Hello World and Write Code"

    def test_mixed_curly_braces(self):
        prompt = "Step 1: {step1}, Step 2: {{step2}}"
        ctx = {"step1": "A", "step2": "B"}
        res = resolve_prompt(prompt, ctx)
        assert res == "Step 1: A, Step 2: B"

    def test_unmatched_variables_preserved(self):
        prompt = "Known: {known}, Unknown: {unknown}, Double: {{also_unknown}}"
        ctx = {"known": "YES"}
        res = resolve_prompt(prompt, ctx)
        assert res == "Known: YES, Unknown: {unknown}, Double: {{also_unknown}}"

    def test_json_payload_inside_prompt_not_corrupted(self):
        prompt = 'Format response as JSON: {"status": "ok", "user": {user_name}, "data": {"items": [1, 2]}}'
        ctx = {"user_name": "Gregory"}
        res = resolve_prompt(prompt, ctx)
        assert res == 'Format response as JSON: {"status": "ok", "user": Gregory, "data": {"items": [1, 2]}}'

    def test_empty_and_none_templates(self):
        assert resolve_prompt("", {"a": 1}) == ""
        assert resolve_prompt(None, {"a": 1}) is None
