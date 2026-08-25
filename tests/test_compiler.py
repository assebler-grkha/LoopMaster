"""Tests for the Python DSL → LoopSpec v1 compiler."""

from __future__ import annotations

import json
import textwrap
from typing import Any

import pytest

from loopmaster.core.policies import ErrorPolicy, RecoveryAction
from loopmaster.core.types import Conditional, Loop, LoopDef, Parallel, Step
from loopmaster.executors.code_block import CodeBlockExecutor
from loopmaster.executors.http import HTTPExecutor
from loopmaster.executors.human_input import HumanInputExecutor
from loopmaster.executors.mcp import MCPToolExecutor
from loopmaster.executors.shell import ShellExecutor
from loopmaster.spec.compiler import CompileError, compile_loop_file, compile_loop_spec
from loopmaster.spec.loader import load_loop_from_dict, validate_loop_spec


def _make_loop(body: Any) -> Any:
    @Loop(name="compile-test", version="1.2.3")
    def _loop(ctx: Any) -> Any:
        return body(ctx)

    return _loop._loop_def if hasattr(_loop, "_loop_def") else _loop


def _manual_loop(steps: list[Any]) -> LoopDef:
    ld = LoopDef(name="compile-test", version="1.2.3", body=lambda ctx: ctx)
    ld._collected_steps = list(steps)  # noqa: SLF001
    return ld


def _types(compiled: dict[str, Any]) -> list[str]:
    return [s["type"] for s in compiled["steps"]]


class TestTopLevel:
    def test_header_fields(self) -> None:
        compiled = compile_loop_spec(_make_loop(lambda ctx: Step("a", model="m", prompt="hi")))
        assert compiled["loopmaster"] == "1.0"
        assert compiled["name"] == "compile-test"
        assert compiled["version"] == "1.2.3"
        assert compiled["execution"] == "engine"
        assert _types(compiled) == ["llm"]

    def test_budget_carried(self) -> None:
        def body(ctx: Any) -> Any:
            Step("a", model="m", prompt="x")
            return ctx

        @Loop(name="b", version="1.0.0", budget="$5.00")
        def budgeted(ctx: Any) -> Any:
            return body(ctx)

        compiled = compile_loop_spec(
            budgeted._loop_def if hasattr(budgeted, "_loop_def") else budgeted
        )
        assert compiled["budget"] == {"max_cost": 5.0}

    def test_description_from_docstring(self) -> None:
        @Loop(name="d", version="1.0.0")
        def documented(ctx: Any) -> Any:
            """Do the thing."""
            Step("a", model="m", prompt="x")
            return ctx

        loop_def = documented._loop_def if hasattr(documented, "_loop_def") else documented
        compiled = compile_loop_spec(loop_def)
        assert compiled["description"] == "Do the thing."

    def test_validates_clean(self) -> None:
        compiled = compile_loop_spec(
            _make_loop(lambda ctx: Step("a", model="m", prompt="hi {q}")), context={"q": ""}
        )
        assert validate_loop_spec(compiled) == []


class TestStepMapping:
    def test_llm(self) -> None:
        compiled = compile_loop_spec(_make_loop(lambda ctx: Step("a", model="m1", prompt="p {a}")))
        assert compiled["steps"][0] == {
            "type": "llm",
            "name": "a",
            "model": "m1",
            "prompt": "p {a}",
        }

    def test_shell(self) -> None:
        ex = ShellExecutor(command=["python", "-c", "print(1)"], timeout=7.5, env={"K": "V"})
        compiled = compile_loop_spec(_make_loop(lambda ctx: Step("sh", executor=ex)))
        node = compiled["steps"][0]
        assert node["type"] == "shell"
        assert node["command"] == ["python", "-c", "print(1)"]
        assert node["timeout"] == 7.5
        assert node["env"] == {"K": "V"}

    def test_http(self) -> None:
        ex = HTTPExecutor(
            url="https://x.test/api", method="POST", json_data={"a": 1}, allowed_status=[200]
        )
        compiled = compile_loop_spec(_make_loop(lambda ctx: Step("h", executor=ex)))
        node = compiled["steps"][0]
        assert node["type"] == "http"
        assert node["url"] == "https://x.test/api"
        assert node["method"] == "POST"
        assert node["json_data"] == {"a": 1}
        assert node["allowed_status"] == [200]

    def test_mcp(self) -> None:
        ex = MCPToolExecutor(server_command=["uvx", "srv"], tool_name="do", arguments={"k": "{v}"})
        compiled = compile_loop_spec(_make_loop(lambda ctx: Step("m", executor=ex)))
        node = compiled["steps"][0]
        assert node["type"] == "mcp"
        assert node["server_command"] == ["uvx", "srv"]
        assert node["tool_name"] == "do"
        assert node["arguments"] == {"k": "{v}"}

    def test_code_with_deny_hoisting(self) -> None:
        ex = CodeBlockExecutor(ref="fixer@1.0.0", input={"q": "{goal}"})
        compiled = compile_loop_spec(
            _make_loop(
                lambda ctx: (
                    setattr(ex, "deny_capabilities", {"net"}),
                    Step("c", executor=ex),
                )[1]
            )
        )
        node = compiled["steps"][0]
        assert node["type"] == "code"
        assert node["ref"] == "fixer@1.0.0"
        assert compiled.get("deny_capabilities") == ["net"]

    def test_human(self) -> None:
        ex = HumanInputExecutor(
            step_name="confirm",
            question="Proceed?",
            options=["yes", "no"],
            timeout="30m",
            default_answer="yes",
            on_timeout="default_answer",
        )
        compiled = compile_loop_spec(_make_loop(lambda ctx: Step("confirm", executor=ex)))
        node = compiled["steps"][0]
        assert node["type"] == "human"
        assert node["question"] == "Proceed?"
        assert node["ask"] == "agent"
        assert node["options"] == ["yes", "no"]
        assert node["timeout"] == "30m"
        assert node["default_answer"] == "yes"

    def test_human_nondefault_timeout_policy(self) -> None:
        ex = HumanInputExecutor(step_name="w", question="?", on_timeout="skip")
        compiled = compile_loop_spec(_make_loop(lambda ctx: Step("w", executor=ex)))
        assert compiled["steps"][0]["on_timeout"] == "skip"


class TestBlocks:
    def test_parallel(self) -> None:
        compiled = compile_loop_spec(
            _manual_loop(
                [
                    Parallel(
                        Step("a1", model="m", prompt="A"),
                        Step("b1", model="m", prompt="B"),
                    ),
                    Step("after", model="m", prompt="{a1} {b1}"),
                ]
            )
        )
        par = compiled["steps"][0]
        assert par["type"] == "parallel"
        assert [s["name"] for s in par["steps"]] == ["a1", "b1"]
        assert _types(compiled) == ["parallel", "llm"]
        assert validate_loop_spec(compiled) == []

    def test_conditional_string_condition(self) -> None:
        compiled = compile_loop_spec(
            _make_loop(
                lambda ctx: (
                    Step("check", model="m", prompt="yes or no"),
                    Conditional(
                        condition="{check} == 'yes'",
                        then_steps=[Step("t", model="m", prompt="T")],
                        else_steps=[Step("e", model="m", prompt="E")],
                    ),
                )[1]
            )
        )
        cond = compiled["steps"][1]
        assert cond["type"] == "conditional"
        assert cond["name"] == "branch-1"
        assert [s["name"] for s in cond["then"]] == ["t"]
        assert [s["name"] for s in cond["else"]] == ["e"]
        assert validate_loop_spec(compiled) == []

    def test_conditional_callable_rejected(self) -> None:
        compiled_error = None
        try:
            compile_loop_spec(
                _make_loop(
                    lambda ctx: Conditional(
                        condition=lambda c: True, then_steps=[Step("t", model="m", prompt="T")]
                    ),
                )
            )
        except CompileError as exc:
            compiled_error = exc
        assert compiled_error is not None
        assert "callable" in str(compiled_error)

    def test_call_condition_in_result_caught_by_validator(self) -> None:
        compiled = compile_loop_spec(
            _make_loop(
                lambda ctx: (
                    Step("check", model="m", prompt="yes or no"),
                    Conditional(
                        condition="'yes' in '{check}'.lower()",
                        then_steps=[Step("t", model="m", prompt="T")],
                    ),
                )[1]
            )
        )
        errors = validate_loop_spec(compiled)
        assert any("disallowed construct" in e for e in errors)


class TestUnsupported:
    def test_tool_step_rejected(self) -> None:
        with pytest.raises(CompileError, match="tool="):
            compile_loop_spec(_make_loop(lambda ctx: Step("s", tool="web_search")))

    def test_per_step_retry_rejected(self) -> None:
        with pytest.raises(CompileError, match="retry/on_error"):
            compile_loop_spec(_make_loop(lambda ctx: Step("s", model="m", prompt="p", retry=3)))

    def test_per_step_on_error_rejected(self) -> None:
        with pytest.raises(CompileError, match="retry/on_error"):
            compile_loop_spec(
                _make_loop(
                    lambda ctx: Step(
                        "s",
                        model="m",
                        prompt="p",
                        on_error=ErrorPolicy(retry=1, on_failure=RecoveryAction.SKIP),
                    )
                )
            )

    def test_bare_step_rejected(self) -> None:
        with pytest.raises(CompileError, match="neither an executor"):
            compile_loop_spec(_make_loop(lambda ctx: Step("bare")))

    def test_unknown_executor_rejected(self) -> None:
        class Weird:
            pass

        with pytest.raises(CompileError, match="unsupported executor"):
            compile_loop_spec(_make_loop(lambda ctx: Step("s", executor=Weird())))

    def test_nested_parallel_in_parallel_rejected(self) -> None:
        inner = Parallel(Step("x", model="m", prompt="X"))

        with pytest.raises(CompileError, match="leaf steps"):
            compile_loop_spec(_manual_loop([Parallel(inner)]))  # type: ignore[arg-type]


class TestRoundTrip:
    def test_load_compiled_dict_back(self) -> None:
        compiled = compile_loop_spec(
            _make_loop(
                lambda ctx: (
                    Step("greet", model="m", prompt="hello"),
                    Step("sh", executor=ShellExecutor(command=["python", "-c", "print(2)"])),
                    Step("final", model="m", prompt="{greet} {sh.stdout}"),
                )[2]
            )
        )
        assert validate_loop_spec(compiled) == []
        loop_def, spec = load_loop_from_dict(json.loads(json.dumps(compiled)))
        assert loop_def.name == "compile-test"
        names = spec.step_names()
        assert names[:2] == ["greet", "sh"]

    def test_compile_loop_file_scenario1(self) -> None:
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "loops" / "scenario1_simple_pipeline.py"
        compiled = compile_loop_file(path)
        assert validate_loop_spec(compiled) == []
        assert _types(compiled) == ["llm", "llm", "llm"]
        assert compiled["steps"][2]["prompt"].startswith("Combine")

    def test_compile_loop_file_scenario7(self) -> None:
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "loops" / "scenario7_shell_pipeline.py"
        compiled = compile_loop_file(path)
        assert validate_loop_spec(compiled) == []
        assert _types(compiled) == ["shell", "shell", "llm"]
        assert compiled["steps"][2]["prompt"].count(".stdout") == 2

    def test_compile_missing_file(self) -> None:
        with pytest.raises(CompileError, match="cannot read"):
            compile_loop_file("does-not-exist.py")

    def test_decorated_loop_preserves_parallel(self, tmp_path: Any) -> None:

        source = textwrap.dedent(
            """
            from loopmaster.core.types import Loop, Parallel, Step

            @Loop(name="par-file", version="1.0.0")
            def run(ctx):
                Step("fetch", model="x", prompt="go")
                _ = (Parallel(Step("sec", model="x", prompt="s"), Step("sty", model="x", prompt="t")),)[0]
                Step("merge", model="x", prompt="done {fetch.output}")
            """
        )
        path = tmp_path / "par_loop.py"
        path.write_text(source, encoding="utf-8")
        compiled = compile_loop_file(path)
        assert validate_loop_spec(compiled) == []
        assert _types(compiled) == ["llm", "parallel", "llm"]
        parallel_node = compiled["steps"][1]
        assert [child["name"] for child in parallel_node["steps"]] == ["sec", "sty"]
