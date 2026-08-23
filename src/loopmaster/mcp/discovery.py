"""Loop discovery and inspection for MCP servers and tooling."""

from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from typing import Any

from loopmaster.core.types import LoopDef as LoopDefType

logger = logging.getLogger("loopmaster.mcp.discovery")


def find_loop_files(search_dir: Path | None = None) -> list[Path]:
    """Find .py files containing @Loop decorators."""
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

    results = []
    seen = set()
    for d in dirs:
        for py_file in d.rglob("*.py"):
            if py_file.name.startswith("_") or py_file in seen:
                continue
            seen.add(py_file)
            try:
                content = py_file.read_text(encoding="utf-8")
                if "@Loop" in content or "from loopmaster" in content:
                    results.append(py_file)
            except (OSError, UnicodeDecodeError) as exc:
                logger.debug("Skipping unreadable file %s: %s", py_file, exc)
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
            step_info: dict[str, Any] = {"name": s.name}
            if s.model:
                step_info["model"] = s.model
            if s.tool:
                step_info["tool"] = s.tool
            if s.prompt:
                step_info["prompt"] = s.prompt
            if s.input is not None:
                step_info["input"] = s.input
            if s.retry is not None:
                step_info["retry"] = s.retry
            if s.timeout is not None:
                step_info["timeout"] = s.timeout
            if s.on_error:
                step_info["on_error"] = s.on_error.to_dict()
            steps.append(step_info)
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
