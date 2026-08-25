"""Tests for LoopSpec v1 JSON loader."""

from __future__ import annotations

import copy
import json

import pytest

from loopmaster.core.policies import Budget, ErrorPolicy, RecoveryAction
from loopmaster.core.types import Conditional, Parallel, Step
from loopmaster.spec import (
    SpecValidationError,
    load_loop_from_dict,
    load_loop_from_json_file,
    parse_loop_spec,
    validate_loop_spec,
)

MINIMAL_SPEC = {
    "loopmaster": "1.0",
    "name": "minimal-loop",
    "version": "1.0.0",
    "steps": [{"type": "llm", "name": "greet", "prompt": "Say hi"}],
}

FULL_SPEC = {
    "loopmaster": "1.0",
    "name": "full-loop",
    "version": "2.1.3",
    "description": "All node types",
    "execution": "agent",
    "context": {"topic": "testing"},
    "budget": {"max_cost": 5.0, "max_tokens": 100000, "max_steps": 50},
    "error_policy": {"retry": 3, "backoff": 0.5, "on_failure": "skip"},
    "steps": [
        {
            "type": "shell",
            "name": "list-files",
            "command": ["python", "--version"],
            "timeout": 30.0,
        },
        {
            "type": "http",
            "name": "fetch-api",
            "url": "https://example.com/api",
            "method": "POST",
            "json_data": {"q": "x"},
            "allowed_status": [200, 201],
        },
        {
            "type": "parallel",
            "name": "fan-out",
            "steps": [
                {"type": "llm", "name": "branch-a", "prompt": "A {topic}"},
                {
                    "type": "mcp",
                    "name": "branch-b",
                    "server_command": ["python", "-m", "fake_server"],
                    "tool_name": "ping",
                },
            ],
        },
        {
            "type": "conditional",
            "name": "route",
            "condition": "topic == 'testing'",
            "then": [{"type": "llm", "name": "then-step", "prompt": "T"}],
            "else": [{"type": "llm", "name": "else-step", "prompt": "E"}],
        },
        {"type": "llm", "name": "final", "prompt": "Done {topic}"},
    ],
}


def _names(nodes):
    out = []
    for n in nodes:
        if isinstance(n, Parallel):
            out.extend(_names(n.steps))
        elif isinstance(n, Conditional):
            out.extend(_names(n.then_steps))
            out.extend(_names(n.else_steps))
        else:
            out.append(n.name)
    return out


class TestValidateLoopSpec:
    def test_minimal_spec_valid(self):
        assert validate_loop_spec(MINIMAL_SPEC) == []

    def test_full_spec_valid(self):
        errors = validate_loop_spec(FULL_SPEC)
        assert errors == [], errors

    def test_missing_marker(self):
        data = copy.deepcopy(MINIMAL_SPEC)
        del data["loopmaster"]
        errors = validate_loop_spec(data)
        assert any("loopmaster" in e for e in errors)

    def test_wrong_marker_version(self):
        data = {**MINIMAL_SPEC, "loopmaster": "2.0"}
        assert any("'loopmaster'" in e for e in validate_loop_spec(data))

    def test_bad_semver(self):
        for bad in ("1.0", "v1.0.0", "1.0.0.0"):
            data = {**MINIMAL_SPEC, "version": bad}
            assert any("semantic version" in e for e in validate_loop_spec(data))

    def test_bad_name_pattern(self):
        data = {**MINIMAL_SPEC, "name": "Bad_Name"}
        assert any("'name'" in e for e in validate_loop_spec(data))

    def test_unknown_step_type(self):
        data = {
            **MINIMAL_SPEC,
            "steps": [{"type": "quantum", "name": "s1"}],
        }
        errors = validate_loop_spec(data)
        assert any("unknown step type" in e for e in errors)

    def test_code_type_requires_ref(self):
        data = {
            **MINIMAL_SPEC,
            "steps": [{"type": "code", "name": "blk"}],
        }
        errors = validate_loop_spec(data)
        assert any("'ref'" in e for e in errors)

    def test_human_type_requires_question(self):
        data = {
            **MINIMAL_SPEC,
            "steps": [{"type": "human", "name": "ask"}],
        }
        errors = validate_loop_spec(data)
        assert any("question" in e for e in errors)

    def test_human_type_valid_full_node(self):
        data = {
            **MINIMAL_SPEC,
            "steps": [
                {
                    "type": "human",
                    "name": "confirm",
                    "question": "Proceed?",
                    "options": ["yes", "no"],
                    "timeout": "30m",
                    "default_answer": "no",
                    "on_timeout": "skip",
                }
            ],
        }
        assert validate_loop_spec(data) == []

    def test_parallel_nesting_rejected(self):
        data = {
            **MINIMAL_SPEC,
            "steps": [
                {
                    "type": "parallel",
                    "name": "outer",
                    "steps": [
                        {
                            "type": "parallel",
                            "name": "inner",
                            "steps": [{"type": "llm", "name": "leaf", "prompt": "p"}],
                        }
                    ],
                }
            ],
        }
        errors = validate_loop_spec(data)
        assert any("cannot be nested inside parallel" in e for e in errors)

    def test_empty_steps_rejected(self):
        assert any("'steps'" in e for e in validate_loop_spec({**MINIMAL_SPEC, "steps": []}))

    def test_unknown_top_level_keys(self):
        data = {**MINIMAL_SPEC, "wat": True}
        assert any("unknown top-level keys" in e for e in validate_loop_spec(data))

    def test_llm_requires_prompt(self):
        data = {**MINIMAL_SPEC, "steps": [{"type": "llm", "name": "s"}]}
        assert any("prompt" in e for e in validate_loop_spec(data))

    def test_shell_requires_command(self):
        data = {**MINIMAL_SPEC, "steps": [{"type": "shell", "name": "s"}]}
        assert any("command" in e for e in validate_loop_spec(data))

    def test_http_bad_method(self):
        data = {
            **MINIMAL_SPEC,
            "steps": [{"type": "http", "name": "s", "url": "https://x.dev", "method": "TELEPORT"}],
        }
        assert any("method" in e for e in validate_loop_spec(data))

    def test_mcp_requires_tool_and_server(self):
        data = {**MINIMAL_SPEC, "steps": [{"type": "mcp", "name": "s"}]}
        errors = validate_loop_spec(data)
        assert any("tool_name" in e for e in errors)
        assert any("server_command" in e for e in errors)

    def test_conditional_requires_condition_and_then(self):
        data = {
            **MINIMAL_SPEC,
            "steps": [{"type": "conditional", "name": "c"}],
        }
        errors = validate_loop_spec(data)
        assert any("condition" in e for e in errors)
        assert any("'then'" in e for e in errors)

    def test_aggregates_multiple_errors(self):
        data = {"name": "Bad Name", "version": "oops", "steps": []}
        errors = validate_loop_spec(data)
        assert len(errors) >= 4

    def test_non_dict_input(self):
        assert validate_loop_spec([1, 2]) != []


class TestParseLoopSpec:
    def test_minimal_parse_builds_step(self):
        spec, steps = parse_loop_spec(copy.deepcopy(MINIMAL_SPEC))
        assert spec.name == "minimal-loop"
        assert spec.version == "1.0.0"
        assert spec.execution == "engine"
        assert isinstance(steps[0], Step)
        assert steps[0].model is None

    def test_full_parse_structure(self):
        spec, steps = parse_loop_spec(copy.deepcopy(FULL_SPEC))
        assert [n.type if hasattr(n, "type") else "" for n in steps]  # smoke
        names = _names(steps)
        assert names == [
            "list-files",
            "fetch-api",
            "branch-a",
            "branch-b",
            "then-step",
            "else-step",
            "final",
        ]
        assert isinstance(steps[2], Parallel)
        assert len(steps[2].steps) == 2
        assert isinstance(steps[3], Conditional)
        assert steps[3].condition == "topic == 'testing'"
        assert spec.step_names() == names

    def test_budget_mapping(self):
        spec, _ = parse_loop_spec({**copy.deepcopy(MINIMAL_SPEC), "budget": {"max_cost": 2.5}})
        assert spec.budget == Budget(max_cost=2.5)
        assert spec.budget is not None and spec.budget.max_tokens is None

    def test_error_policy_mapping_case_insensitive(self):
        spec, _ = parse_loop_spec(
            {**copy.deepcopy(MINIMAL_SPEC), "error_policy": {"on_failure": "Retry"}}
        )
        assert spec.error_policy == ErrorPolicy(
            retry=2, backoff=1.0, on_failure=RecoveryAction.RETRY, fallback_model=None
        )

    def test_duplicate_names_across_branches(self):
        data = copy.deepcopy(MINIMAL_SPEC)
        data["steps"] = [
            {"type": "llm", "name": "dup", "prompt": "a"},
            {
                "type": "parallel",
                "name": "p",
                "steps": [{"type": "llm", "name": "dup", "prompt": "b"}],
            },
        ]
        with pytest.raises(SpecValidationError) as exc_info:
            parse_loop_spec(data)
        assert any("duplicate step name 'dup'" in e for e in exc_info.value.errors)

    def test_invalid_raises_with_all_errors(self):
        with pytest.raises(SpecValidationError) as exc_info:
            parse_loop_spec({"nope": True})
        assert exc_info.value.errors

    def test_shell_executor_built(self):
        data = copy.deepcopy(MINIMAL_SPEC)
        data["steps"] = [{"type": "shell", "name": "s", "command": "echo hi"}]
        _, steps = parse_loop_spec(data)
        assert steps[0].executor is not None

    def test_http_executor_built(self):
        data = copy.deepcopy(MINIMAL_SPEC)
        data["steps"] = [{"type": "http", "name": "s", "url": "https://x.dev"}]
        _, steps = parse_loop_spec(data)
        assert steps[0].executor is not None


class TestLoadFromJsonFile:
    def _write(self, tmp_path, payload):
        p = tmp_path / "spec.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def test_load_returns_ready_loop_def(self, tmp_path):
        path = self._write(tmp_path, FULL_SPEC)
        loop_def, spec = load_loop_from_json_file(path)
        assert loop_def.name == "full-loop"
        assert loop_def.version == "2.1.3"
        assert isinstance(loop_def.budget, Budget)
        assert loop_def._collected_steps is not None
        assert len(loop_def._collected_steps) == 5
        assert spec.source_path == str(path)

    def test_source_hash_is_deterministic(self, tmp_path):
        a, _ = load_loop_from_json_file(self._write(tmp_path, MINIMAL_SPEC))
        reordered = {
            "steps": MINIMAL_SPEC["steps"],
            "version": MINIMAL_SPEC["version"],
            "name": MINIMAL_SPEC["name"],
            "loopmaster": MINIMAL_SPEC["loopmaster"],
        }
        b, _ = load_loop_from_json_file(self._write(tmp_path, reordered))
        assert a.source_hash == b.source_hash
        c, _ = load_loop_from_json_file(
            self._write(tmp_path, {**MINIMAL_SPEC, "description": "changed"})
        )
        assert a.source_hash != c.source_hash

    def test_invalid_json_raises(self, tmp_path):
        p = tmp_path / "broken.json"
        p.write_text("{not json", encoding="utf-8")
        with pytest.raises(SpecValidationError, match="invalid JSON"):
            load_loop_from_json_file(p)

    def test_validation_errors_carry_path(self, tmp_path):
        p = self._write(tmp_path, {"loopmaster": "9.9"})
        with pytest.raises(SpecValidationError) as exc_info:
            load_loop_from_json_file(p)
        assert exc_info.value.path == str(p)

    def test_unreadable_file_raises(self, tmp_path):
        with pytest.raises(SpecValidationError, match="cannot read"):
            load_loop_from_json_file(tmp_path / "missing.json")


class TestSemanticValidation:
    @staticmethod
    def _spec(steps, context=None):
        data = {
            "loopmaster": "1.0",
            "name": "sem-loop",
            "version": "1.0.0",
            "steps": steps,
        }
        if context:
            data["context"] = context
        return data

    def test_unknown_placeholder_rejected(self):
        spec = self._spec([{"type": "llm", "name": "a", "prompt": "Hi {missing_var}"}])
        errors = validate_loop_spec(spec)
        assert any("unknown placeholder {missing_var}" in e for e in errors)

    def test_context_key_ok(self):
        spec = self._spec(
            [{"type": "llm", "name": "a", "prompt": "Hi {topic}"}],
            context={"topic": "x"},
        )
        assert validate_loop_spec(spec) == []

    def test_previous_step_output_ok(self):
        spec = self._spec(
            [
                {"type": "llm", "name": "first", "prompt": "one"},
                {"type": "llm", "name": "second", "prompt": "use {first}"},
            ]
        )
        assert validate_loop_spec(spec) == []

    def test_forward_reference_rejected(self):
        spec = self._spec(
            [
                {"type": "llm", "name": "a", "prompt": "use {later}"},
                {"type": "llm", "name": "later", "prompt": "two"},
            ]
        )
        errors = validate_loop_spec(spec)
        assert any("unknown placeholder {later}" in e for e in errors)

    def test_parallel_child_visible_to_next_step(self):
        spec = self._spec(
            [
                {
                    "type": "parallel",
                    "name": "fan",
                    "steps": [{"type": "llm", "name": "kid", "prompt": "k"}],
                },
                {"type": "llm", "name": "after", "prompt": "{kid} done"},
            ]
        )
        assert validate_loop_spec(spec) == []

    def test_shell_bare_ref_requires_stdout(self):
        spec = self._spec(
            [
                {"type": "shell", "name": "sh_one", "command": "echo hi"},
                {"type": "llm", "name": "after", "prompt": "{sh_one}"},
            ]
        )
        errors = validate_loop_spec(spec)
        assert any("{sh_one.stdout}" in e for e in errors)

    def test_shell_stdout_ref_ok(self):
        spec = self._spec(
            [
                {"type": "shell", "name": "sh_one", "command": "echo hi"},
                {"type": "llm", "name": "after", "prompt": "{sh_one.stdout}"},
            ]
        )
        assert validate_loop_spec(spec) == []

    def test_condition_call_rejected(self):
        spec = self._spec(
            [
                {
                    "type": "conditional",
                    "name": "route",
                    "condition": "len('abcd') > 50",
                    "then": [{"type": "llm", "name": "t", "prompt": "t"}],
                },
            ]
        )
        errors = validate_loop_spec(spec)
        assert any("disallowed construct 'Call'" in e for e in errors)

    def test_condition_syntax_error_rejected(self):
        spec = self._spec(
            [
                {
                    "type": "conditional",
                    "name": "route",
                    "condition": "status === 'yes'",
                    "then": [{"type": "llm", "name": "t", "prompt": "t"}],
                },
            ]
        )
        errors = validate_loop_spec(spec)
        assert any("syntax error" in e for e in errors)

    @pytest.mark.parametrize(
        ("cond", "ctx"),
        [
            ("status == 'yes'", {"status": "yes"}),
            ("not flag", {"flag": False}),
            ("flag and other", {"flag": True, "other": True}),
            ("{flag} == 'yes'", {"flag": True}),
        ],
    )
    def test_condition_forms_ok(self, cond, ctx):
        spec = self._spec(
            [
                {
                    "type": "conditional",
                    "name": "route",
                    "condition": cond,
                    "then": [{"type": "llm", "name": "t", "prompt": "t"}],
                },
            ],
            context=ctx,
        )
        assert validate_loop_spec(spec) == []

    def test_condition_unknown_placeholder_rejected(self):
        spec = self._spec(
            [
                {
                    "type": "conditional",
                    "name": "route",
                    "condition": "{nope} == 'yes'",
                    "then": [{"type": "llm", "name": "t", "prompt": "t"}],
                },
            ]
        )
        errors = validate_loop_spec(spec)
        assert any("unknown placeholder {nope}" in e for e in errors)


class TestTimeoutCompilation:
    def test_shell_timeout_compiles_into_executor(self):
        loop_def, _spec = load_loop_from_dict(
            {
                "loopmaster": "1.0",
                "name": "to-loop",
                "version": "1.0.0",
                "steps": [
                    {"type": "shell", "name": "s", "command": "echo x", "timeout": 5},
                ],
            }
        )
        steps = loop_def._collected_steps
        assert steps is not None
        executor = steps[0].executor
        assert executor is not None
        assert executor.timeout == 5.0

    def test_http_timeout_compiles_into_executor(self):
        loop_def, _spec = load_loop_from_dict(
            {
                "loopmaster": "1.0",
                "name": "to-http",
                "version": "1.0.0",
                "steps": [
                    {"type": "http", "name": "h", "url": "https://example.com", "timeout": 7},
                ],
            }
        )
        steps = loop_def._collected_steps
        assert steps is not None
        executor = steps[0].executor
        assert executor is not None
        assert executor.timeout == 7.0
