"""Loop discovery and inspection for MCP servers and tooling."""

from __future__ import annotations

import ast
import importlib.util
import logging
import os
from pathlib import Path
from typing import Any

from loopmaster.core.types import LoopDef as LoopDefType

logger = logging.getLogger("loopmaster.mcp.discovery")

_parse_cache: dict[str, tuple[float, int, ast.Module | None]] = {}


def _cached_tree(py_file: Path) -> ast.Module | None:
    """Parse ``py_file`` into an AST, memoized by (mtime, size). Never executes."""
    try:
        st = py_file.stat()
    except OSError as exc:
        logger.debug("Cannot stat %s: %s", py_file, exc)
        return None
    key = str(py_file.resolve())
    cached = _parse_cache.get(key)
    if cached is not None and cached[0] == st.st_mtime and cached[1] == st.st_size:
        return cached[2]
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError, ValueError) as exc:
        logger.debug("Skipping unparseable file %s: %s", py_file, exc)
        tree = None
    _parse_cache[key] = (st.st_mtime, st.st_size, tree)
    return tree


def _is_loop_decorator(dec: ast.expr) -> bool:
    target = dec.func if isinstance(dec, ast.Call) else dec
    name = getattr(target, "attr", None) or getattr(target, "id", "")
    return name == "Loop"


_LOOP_CONST_FIELDS = ("name", "version")


def inspect_loop_file(py_file: Path) -> dict[str, Any] | None:
    """Extract loop metadata via AST analysis WITHOUT executing the module.

    Returns ``{"name": ..., "version": ..., "description": ...}`` for the first
    ``@Loop``-decorated function, or ``None`` when nothing is found/parsable.
    """
    tree = _cached_tree(py_file)
    if tree is None:
        return None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not _is_loop_decorator(dec):
                continue
            meta: dict[str, Any] = {}
            if isinstance(dec, ast.Call):
                pos = [
                    arg.value
                    for arg in dec.args[: len(_LOOP_CONST_FIELDS)]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                ]
                for idx, field in enumerate(_LOOP_CONST_FIELDS):
                    if idx < len(pos):
                        meta[field] = pos[idx]
                for kw in dec.keywords:
                    if (
                        kw.arg in _LOOP_CONST_FIELDS
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                    ):
                        meta[kw.arg] = kw.value.value
            if "name" not in meta:
                meta["name"] = node.name
            meta.setdefault("version", "0.0.0")
            doc = ast.get_docstring(node)
            if doc:
                meta["description"] = doc.strip().splitlines()[0]
            return meta
    return None


def _module_uses_loops(tree: ast.Module) -> bool:
    """True when the module plausibly defines loops (@Loop or LoopDef usage)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", "") or ""
            names = [a.name for a in node.names]
            if node.__class__.__name__ == "ImportFrom" and module.startswith("loopmaster"):
                return True
            if node.__class__.__name__ == "Import" and any(
                n.startswith("loopmaster") for n in names
            ):
                return True
        if isinstance(node, ast.Call):
            fn = node.func
            name = getattr(fn, "attr", None) or getattr(fn, "id", "")
            if name in ("Loop", "LoopDef"):
                return True
    return False


def find_loop_files(search_dir: Path | None = None) -> list[Path]:
    """Find .py files defining loops, using AST parsing only (no code execution)."""
    dirs = []
    if search_dir and search_dir.is_dir():
        dirs.append(search_dir)
    cwd = Path.cwd()
    if cwd not in dirs:
        dirs.append(cwd)
    env_dir = os.environ.get("LOOPMASTER_LOOPS_DIR")
    if env_dir:
        p = Path(env_dir)
        if p.is_dir() and p not in dirs:
            dirs.append(p)

    skip_dirs = {"templates", "__pycache__", ".git", "node_modules"}
    results = []
    seen = set()
    for d in dirs:
        for py_file in d.rglob("*.py"):
            if py_file.name.startswith("_") or py_file in seen:
                continue
            if any(part in skip_dirs for part in py_file.parts):
                continue
            seen.add(py_file)
            tree = _cached_tree(py_file)
            if tree is not None and _module_uses_loops(tree):
                results.append(py_file)
    return results


def load_loop_def_object(py_file: Path) -> LoopDefType | None:
    """Load raw LoopDef instance from a Python file."""
    try:
        spec = importlib.util.spec_from_file_location("_loop_mod", py_file)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, LoopDefType):
                return attr
            if hasattr(attr, "_loop_def") and isinstance(attr._loop_def, LoopDefType):
                return attr._loop_def
    except Exception as exc:
        logger.debug("Failed to load LoopDef object from %s: %s", py_file, exc)
    return None


def _format_step_or_block(item: Any) -> dict[str, Any]:
    if hasattr(item, "to_dict"):
        return dict(item.to_dict())
    step_info: dict[str, Any] = {"name": getattr(item, "name", "step")}
    for field in ("model", "tool", "prompt", "input", "retry", "timeout"):
        val = getattr(item, field, None)
        if val is not None:
            step_info[field] = val
    if getattr(item, "on_error", None):
        step_info["on_error"] = item.on_error.to_dict()
    return step_info


def loop_def_to_dict(loop_def: LoopDefType, source_file: Path) -> dict[str, Any]:
    """Convert LoopDef to a serializable dict with full step info."""
    from loopmaster.core.context import Context
    from loopmaster.core.engine import _set_current_steps

    steps: list[dict[str, Any]] = []
    try:
        collected: list[Any] = []
        _set_current_steps(collected)
        ctx = Context({})
        ctx._loop_engine = None
        ctx._executed_steps = []
        ctx._results = {}
        ctx._current_error_policy = None
        loop_def.body(ctx)
        for s in collected:
            steps.append(_format_step_or_block(s))
    except Exception as exc:
        logger.warning("Could not collect steps from %s: %s", loop_def.name, exc)
    finally:
        _set_current_steps(None)

    result: dict[str, Any] = {
        "name": loop_def.name,
        "version": loop_def.version,
        "source_file": str(source_file),
        "steps": steps,
        "step_count": len(steps),
    }
    if loop_def.agent:
        result["agent"] = loop_def.agent
    if loop_def.budget:
        result["budget"] = loop_def.budget.to_dict()
    if loop_def.interruption_protection:
        result["interruption_protection"] = loop_def.interruption_protection.to_dict()
    return result


def load_loop_def(py_file: Path) -> dict[str, Any] | None:
    """Load a LoopDef from a Python file and return it as a dict."""
    loop_def = load_loop_def_object(py_file)
    if loop_def is not None:
        return loop_def_to_dict(loop_def, py_file)
    return None


def get_error_policy(loop_def: dict[str, Any], step_name: str) -> dict[str, Any]:
    """Get error policy for a step."""
    for step in loop_def.get("steps", []):
        if step.get("name") == step_name and "on_error" in step:
            res: dict[str, Any] = step["on_error"]
            return res
    return {"retry": 2, "on_failure": "abort"}


def get_recovery_suggestion(policy: dict[str, Any], error: str) -> str:
    """Suggest recovery action based on error policy."""
    action = policy.get("on_failure", "abort")
    retry = policy.get("retry", 2)
    if action == "retry":
        return f"Retry the step (up to {retry} times). Error: {error}"
    if action == "skip":
        return f"Skip this step and continue. Error: {error}"
    if action == "fallback":
        fb = policy.get("fallback_model", "a different model")
        return f"Retry with {fb}. Error: {error}"
    return f"Abort the loop. Error: {error}"


def build_summary(results: dict[str, Any]) -> str:
    """Build execution summary from results dict."""
    total = len(results)
    succeeded = sum(1 for r in results.values() if r.get("success"))
    failed = total - succeeded
    return f"Completed {succeeded}/{total} steps" + (f" ({failed} failed)" if failed else "")
