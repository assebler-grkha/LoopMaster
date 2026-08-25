"""Safe condition evaluation for Conditional branching in LoopMaster DSL."""

from __future__ import annotations

import ast
import logging
import operator
from collections.abc import Callable
from typing import Any

from .types import resolve_prompt

logger = logging.getLogger("loopmaster.core.condition")

_OPERATORS: dict[type[ast.AST], Callable[[Any, Any], bool]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


def _safe_eval_node(node: ast.AST, ctx_data: dict[str, Any]) -> Any:
    """Recursively evaluate a whitelisted AST node against context data."""
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body, ctx_data)

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        return ctx_data.get(node.id)

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _safe_eval_node(node.operand, ctx_data)

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        operand = _safe_eval_node(node.operand, ctx_data)
        if isinstance(operand, (int, float)) and not isinstance(operand, bool):
            return -operand
        raise ValueError("Unary minus requires a numeric operand")

    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_safe_eval_node(val, ctx_data) for val in node.values)
        if isinstance(node.op, ast.Or):
            return any(_safe_eval_node(val, ctx_data) for val in node.values)

    if isinstance(node, ast.Compare):
        left_val = _safe_eval_node(node.left, ctx_data)
        for op_node, comparator in zip(node.ops, node.comparators, strict=False):
            op_fn = _OPERATORS.get(type(op_node))
            if op_fn is None:
                raise ValueError(f"Disallowed comparison operator: {type(op_node).__name__}")
            right_val = _safe_eval_node(comparator, ctx_data)
            if not op_fn(left_val, right_val):
                return False
            left_val = right_val
        return True

    raise ValueError(f"Disallowed or unsupported AST expression node: {type(node).__name__}")


def evaluate_condition(
    condition: Callable[[Any], bool] | str | bool | None,
    ctx: Any,
) -> bool:
    """Safely evaluate a condition predicate or expression string against execution context."""
    if condition is None:
        return True

    if isinstance(condition, bool):
        return condition

    if callable(condition):
        try:
            return bool(condition(ctx))
        except Exception as exc:
            logger.warning("Condition callable raised exception, evaluating to False: %s", exc)
            return False

    if isinstance(condition, str):
        ctx_data = (
            ctx.to_dict()
            if hasattr(ctx, "to_dict")
            else (dict(ctx) if isinstance(ctx, dict) else {})
        )
        cond_str = resolve_prompt(condition.strip(), ctx_data)
        if not cond_str:
            return False

        # Fast path: simple key in context
        if cond_str in ctx_data:
            return bool(ctx_data[cond_str])

        # Safe AST whitelist evaluation (no eval() security risks)
        try:
            tree = ast.parse(cond_str, mode="eval")
            return bool(_safe_eval_node(tree, ctx_data))
        except Exception as exc:
            logger.debug("Condition AST evaluation failed for '%s': %s", cond_str, exc)
            return False

    return bool(condition)
