"""Compiler: Python DSL LoopDef → LoopSpec v1 JSON.

One-way translation (dual-mode forever): Python loops stay the rich
authoring format, while the produced JSON can be loaded back through
spec.loader. Constructs without a LoopSpec v1 equivalent raise
CompileError instead of being silently dropped.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from loopmaster.core.types import Conditional, LoopDef, Parallel, Step
from loopmaster.executors.code_block import CodeBlockExecutor
from loopmaster.executors.http import HTTPExecutor
from loopmaster.executors.human_input import HumanInputExecutor
from loopmaster.executors.mcp import MCPToolExecutor
from loopmaster.executors.shell import ShellExecutor

from .loader import SPEC_VERSION


class CompileError(Exception):
    """A Python loop construct cannot be represented as LoopSpec v1."""


def compile_loop_spec(loop_def: LoopDef, *, context: dict | None = None) -> dict[str, Any]:
    """Compile a LoopDef into a LoopSpec v1 JSON-compatible dict.

    Args:
        loop_def: The loop definition to compile.
        context: Optional initial-context keys to embed for semantic validation.

    Returns:
        Dict conforming to schemas/loopspec-v1.schema.json.

    Raises:
        CompileError: On constructs LoopSpec v1 cannot represent.
    """
    spec: dict[str, Any] = {
        "loopmaster": SPEC_VERSION,
        "name": loop_def.name,
        "version": loop_def.version,
        "execution": "engine",
    }
    doc = (loop_def.body.__doc__ or "").strip()
    if doc:
        spec["description"] = doc
    if context is not None:
        spec["context"] = dict(context)
    if loop_def.budget is not None:
        budget = loop_def.budget.to_dict()
        if budget:
            spec["budget"] = budget

    deny_union: set[str] = set()
    steps = [
        _compile_node(node, "$.steps", i, deny_union)
        for i, node in enumerate(_collect_steps(loop_def))
    ]
    if steps and deny_union:
        spec["deny_capabilities"] = sorted(deny_union)
    spec["steps"] = steps
    return spec


def compile_loop_file(py_file: str | Path, *, context: dict | None = None) -> dict[str, Any]:
    """Import a Python loop file and compile its first @Loop definition."""
    loop_def = _load_first_loop_def(Path(py_file))
    return compile_loop_spec(loop_def, context=context)


def _load_first_loop_def(path: Path) -> LoopDef:
    from loopmaster.core.types import LoopDef as LoopDefT

    if not path.is_file():
        raise CompileError(f"cannot read file: {path}")
    mod_name = f"_lm_compile_{path.stem}"
    spec_obj = importlib.util.spec_from_file_location(mod_name, path)
    if spec_obj is None or spec_obj.loader is None:
        raise CompileError(f"cannot import module from {path}")
    module = importlib.util.module_from_spec(spec_obj)
    try:
        spec_obj.loader.exec_module(module)
    except Exception as exc:
        raise CompileError(f"import failed: {exc}") from exc

    for attr in vars(module).values():
        if isinstance(attr, LoopDefT):
            return attr
        inner = getattr(attr, "_loop_def", None)
        if isinstance(inner, LoopDefT):
            return inner
    raise CompileError(f"No @Loop found in {path}")


def _collect_steps(loop_def: LoopDef) -> list[Any]:
    if loop_def._collected_steps is not None:
        return list(loop_def._collected_steps)

    from loopmaster.core.context import Context
    from loopmaster.core.engine import _set_current_steps

    collected: list[Any] = []
    _set_current_steps(collected)
    try:
        ctx = Context({})
        ctx._loop_engine = None
        ctx._executed_steps = []
        ctx._results = {}
        ctx._current_error_policy = None
        loop_def.body(ctx)
    finally:
        _set_current_steps(None)
    return collected


def _compile_node(node: Any, prefix: str, index: int, deny_union: set[str]) -> dict[str, Any]:
    at = f"{prefix}[{index}]"
    if isinstance(node, Parallel):
        children = [
            _compile_leaf_step(child, f"{at}.steps", j, deny_union)
            for j, child in enumerate(node.steps)
        ]
        return {"type": "parallel", "name": f"group-{index}", "steps": children}
    if isinstance(node, Conditional):
        return _compile_conditional(node, at, index, deny_union)
    if isinstance(node, Step):
        return _compile_step(node, at, deny_union)
    raise CompileError(f"{at}: unsupported block type {type(node).__name__}")


def _compile_conditional(
    node: Conditional, at: str, index: int, deny_union: set[str]
) -> dict[str, Any]:
    if not isinstance(node.condition, str):
        kind = "callable" if callable(node.condition) else type(node.condition).__name__
        raise CompileError(
            f"{at}: conditional condition must be a string expression "
            f"(got {kind}) — LoopSpec v1 evaluates string AST conditions only"
        )
    name = node.name or f"branch-{index}"
    compiled: dict[str, Any] = {
        "type": "conditional",
        "name": name,
        "condition": node.condition,
        "then": [
            _compile_node(child, f"{at}.then", j, deny_union)
            for j, child in enumerate(node.then_steps)
        ],
    }
    if node.else_steps:
        compiled["else"] = [
            _compile_node(child, f"{at}.else", j, deny_union)
            for j, child in enumerate(node.else_steps)
        ]
    return compiled


def _compile_leaf_step(step: Step, at: str, index: int, deny_union: set[str]) -> dict[str, Any]:
    if not isinstance(step, Step):
        raise CompileError(
            f"{at}: parallel children must be leaf steps (got {type(step).__name__})"
        )
    return _compile_step(step, at, deny_union)


def _compile_step(step: Step, at: str, deny_union: set[str]) -> dict[str, Any]:
    name = step.name
    executor = step.executor
    if executor is not None:
        return _compile_executor_step(step, executor, at, deny_union)

    if step.retry is not None or step.on_error is not None:
        raise CompileError(
            f"{at}: step '{name}' uses per-step retry/on_error "
            f"— LoopSpec v1 has no per-step error policy"
        )
    if step.tool:
        raise CompileError(
            f"{at}: step '{name}' uses tool=... which LoopSpec v1 cannot represent "
            "(use shell/http/mcp/code executors instead)"
        )
    if not step.model or not step.prompt:
        raise CompileError(f"{at}: step '{name}' has neither an executor nor model+prompt")
    compiled: dict[str, Any] = {"type": "llm", "name": name, "prompt": step.prompt}
    if step.model:
        compiled["model"] = step.model
    if step.timeout is not None:
        compiled["timeout"] = step.timeout
    return compiled


def _compile_executor_step(
    step: Step, executor: Any, at: str, deny_union: set[str]
) -> dict[str, Any]:
    name = step.name
    if isinstance(executor, ShellExecutor):
        compiled: dict[str, Any] = {
            "type": "shell",
            "name": name,
            "command": executor.command,
            "timeout": executor.timeout,
        }
        if executor.cwd:
            compiled["cwd"] = executor.cwd
        if executor.env:
            compiled["env"] = dict(executor.env)
        if executor.shell:
            compiled["shell"] = True
        return compiled
    if isinstance(executor, HTTPExecutor):
        compiled = {
            "type": "http",
            "name": name,
            "url": executor.url,
            "method": executor.method,
            "timeout": executor.timeout,
        }
        if executor.headers:
            compiled["headers"] = dict(executor.headers)
        if executor.json_data is not None:
            compiled["json_data"] = executor.json_data
        if executor.data is not None:
            compiled["data"] = executor.data
        if executor.allowed_status:
            compiled["allowed_status"] = list(executor.allowed_status)
        return compiled
    if isinstance(executor, MCPToolExecutor):
        compiled = {
            "type": "mcp",
            "name": name,
            "server_command": list(executor.server_command),
            "tool_name": executor.tool_name,
            "timeout": executor.timeout,
        }
        if executor.arguments is not None:
            compiled["arguments"] = executor.arguments
        if executor.cwd:
            compiled["cwd"] = executor.cwd
        if executor.env:
            compiled["env"] = dict(executor.env)
        return compiled
    if isinstance(executor, CodeBlockExecutor):
        if executor.deny_capabilities:
            deny_union.update(executor.deny_capabilities)
        compiled = {"type": "code", "name": name, "ref": executor.ref, "timeout": executor.timeout}
        if executor.sha256:
            compiled["sha256"] = executor.sha256
        if executor.input is not None:
            compiled["input"] = executor.input
        return compiled
    if isinstance(executor, HumanInputExecutor):
        compiled = {
            "type": "human",
            "name": name,
            "question": executor.question,
            "ask": executor.ask_to,
        }
        if executor.options:
            compiled["options"] = list(executor.options)
        if executor.timeout:
            compiled["timeout"] = executor.timeout
        if executor.default_answer is not None:
            compiled["default_answer"] = executor.default_answer
        if executor.on_timeout != "default_answer":
            compiled["on_timeout"] = executor.on_timeout
        return compiled
    raise CompileError(f"{at}: step '{name}' has unsupported executor {type(executor).__name__}")
