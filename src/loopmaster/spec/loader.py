"""LoopSpec v1 loader: JSON configuration -> validated IR -> engine LoopDef."""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loopmaster.core.policies import Budget, ErrorPolicy, RecoveryAction
from loopmaster.core.types import Conditional, LoopDef, Parallel, Step
from loopmaster.executors.http import HTTPExecutor
from loopmaster.executors.mcp import MCPToolExecutor
from loopmaster.executors.shell import ShellExecutor

logger = logging.getLogger("loopmaster.spec.loader")

SPEC_VERSION = "1.0"
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_LEAF_TYPES = {"llm", "shell", "http", "mcp"}
_RESERVED_PHASES = {
    "code": "Phase 3 (CodeBlockStore)",
    "human": "Phase 4 (HITL protocol)",
}

_PLACEHOLDER_RE = re.compile(r"\{\{?([a-zA-Z_][\w\.]*)\}?\}")
_BRACE_CANDIDATE_RE = re.compile(r"\{[^{}]{0,80}\}")
_PLAUSIBLE_REF_RE = re.compile(r"[A-Za-z0-9_.\- ]+")
_ALLOWED_COND_NODES = (
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
    ast.And,
    ast.Or,
    ast.Not,
    ast.USub,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
    ast.Load,
)


@dataclass
class LoopSpec:
    """Validated LoopSpec v1 intermediate representation."""

    name: str
    version: str
    description: str = ""
    execution: str = "engine"
    initial_context: dict[str, Any] = field(default_factory=dict)
    budget: Budget | None = None
    error_policy: ErrorPolicy | None = None
    steps: list[Any] = field(default_factory=list)
    source_path: str | None = None

    def step_names(self) -> list[str]:
        """Flat ordered list of all step names (parallel children inline)."""
        names: list[str] = []
        self._collect(names, self.steps)
        return names

    def _collect(self, names: list[str], nodes: list[Any]) -> None:
        for node in nodes:
            if isinstance(node, Parallel):
                self._collect(names, node.steps)
            elif isinstance(node, Conditional):
                self._collect(names, node.then_steps)
                self._collect(names, node.else_steps)
            else:
                names.append(node.name)


class SpecValidationError(Exception):
    """Raised when a JSON spec fails validation. Carries all errors found."""

    def __init__(self, errors: list[str], path: str | None = None) -> None:
        self.errors = errors
        self.path = path
        where = f" ({path})" if path else ""
        details = "\n".join(f"  - {e}" for e in errors)
        super().__init__(f"Invalid LoopSpec{where}, {len(errors)} error(s):\n{details}")


def validate_loop_spec(data: Any) -> list[str]:
    """Validate a parsed spec dict. Returns a list of errors (empty = valid)."""
    errors: list[str] = _validate(data, "$")
    if not errors and isinstance(data, dict):
        known = {k: "context" for k in (data.get("context") or {})}
        _walk_semantics(data.get("steps") or [], known, errors, "$.steps")
    return errors


def parse_loop_spec(data: Any, *, source_path: str | None = None) -> tuple[LoopSpec, list[Any]]:
    """Parse and validate raw JSON data.

    Returns (LoopSpec IR, built runtime objects for each root step).
    Raises SpecValidationError with every problem found.
    """
    if not isinstance(data, dict):
        raise SpecValidationError(["top-level value must be a JSON object"], source_path)

    errors = _validate(data, "$")
    if errors:
        raise SpecValidationError(errors, source_path)

    steps = _build_steps(data["steps"], source_path=source_path)
    seen: set[str] = set()

    def _check_names(nodes: list[Any]) -> None:
        for node in nodes:
            if isinstance(node, Parallel):
                _check_names(node.steps)
            elif isinstance(node, Conditional):
                _check_names(node.then_steps)
                _check_names(node.else_steps)
            else:
                if node.name in seen:
                    errors.append(f"$.steps: duplicate step name '{node.name}'")
                seen.add(node.name)

    _check_names(steps)
    if errors:
        raise SpecValidationError(errors, source_path)

    known = {k: "context" for k in (data.get("context") or {})}
    _walk_semantics(data["steps"], known, errors, "$.steps")
    if errors:
        raise SpecValidationError(errors, source_path)

    budget = _parse_budget(data.get("budget"))
    policy = _parse_error_policy(data.get("error_policy"))

    spec = LoopSpec(
        name=data["name"],
        version=data["version"],
        description=data.get("description", ""),
        execution=data.get("execution", "engine"),
        initial_context=dict(data.get("context") or {}),
        budget=budget,
        error_policy=policy,
        steps=steps,
        source_path=source_path,
    )
    logger.debug("Parsed LoopSpec %s v%s (%d root nodes)", spec.name, spec.version, len(steps))
    return spec, steps


def load_loop_from_dict(data: Any, *, source_path: str | None = None) -> tuple[LoopDef, LoopSpec]:
    """Load a parsed JSON spec dict into an executable LoopDef plus its IR."""
    spec, steps = parse_loop_spec(data, source_path=source_path)

    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    source_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    loop_def = LoopDef(
        name=spec.name,
        version=spec.version,
        body=_noop_body,
        agent=None,
        budget=spec.budget,
        interruption_protection=None,
        source_hash=source_hash,
    )
    loop_def._collected_steps = steps  # noqa: SLF001 - engine consumes prebuilt steps
    loop_def._recollect_steps = False  # noqa: SLF001 - declarative steps are immutable
    return loop_def, spec


def load_loop_from_json_file(path: str | Path) -> tuple[LoopDef, LoopSpec]:
    """Load a JSON spec file into an executable LoopDef plus its IR."""
    file_path = Path(path)
    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecValidationError([f"cannot read file: {exc}"], str(file_path)) from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SpecValidationError([f"invalid JSON: {exc}"], str(file_path)) from exc

    return load_loop_from_dict(data, source_path=str(file_path))


def _noop_body(ctx: dict[str, Any]) -> dict[str, Any]:
    """Placeholder body: the engine uses prebuilt steps and never calls it."""
    return ctx


def _err(errors: list[str], at: str, msg: str) -> None:
    errors.append(f"{at}: {msg}")


def _validate(node: Any, at: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(node, dict):
        _err(errors, at, "must be an object")
        return errors

    marker = node.get("loopmaster")
    if marker != SPEC_VERSION:
        _err(errors, at, f"'loopmaster' must be \"{SPEC_VERSION}\", got {marker!r}")

    name = node.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name):
        _err(errors, at, "'name' must match ^[a-z][a-z0-9-]*$")

    version = node.get("version")
    if not isinstance(version, str) or not _SEMVER_RE.match(version):
        _err(errors, at, "'version' must be semantic version X.Y.Z")

    execution = node.get("execution", "engine")
    if execution not in ("engine", "agent"):
        _err(errors, at, f"'execution' must be 'engine' or 'agent', got {execution!r}")

    ctx = node.get("context")
    if ctx is not None and not isinstance(ctx, dict):
        _err(errors, at, "'context' must be an object")

    budget = node.get("budget")
    if budget is not None:
        if not isinstance(budget, dict):
            _err(errors, at, "'budget' must be an object")
        else:
            for key, kind in (
                ("max_cost", (int, float)),
                ("max_tokens", int),
                ("max_steps", int),
            ):
                val = budget.get(key)
                if key in budget and (not isinstance(val, kind) or val <= 0):
                    _err(errors, at, f"'budget.{key}' must be positive number")

    policy = node.get("error_policy")
    if policy is not None:
        if not isinstance(policy, dict):
            _err(errors, at, "'error_policy' must be an object")
        else:
            retry = policy.get("retry")
            if retry is not None and (not isinstance(retry, int) or retry < 0):
                _err(errors, at, "'error_policy.retry' must be non-negative integer")
            backoff = policy.get("backoff")
            if backoff is not None and (not isinstance(backoff, (int, float)) or backoff < 0):
                _err(errors, at, "'error_policy.backoff' must be non-negative number")
            on_failure = policy.get("on_failure")
            if on_failure is not None and (
                not isinstance(on_failure, str)
                or on_failure.upper() not in RecoveryAction.__members__
            ):
                allowed = ", ".join(a.lower() for a in RecoveryAction.__members__)
                _err(errors, at, f"'error_policy.on_failure' must be one of: {allowed}")
            fm = policy.get("fallback_model")
            if fm is not None and not isinstance(fm, str):
                _err(errors, at, "'error_policy.fallback_model' must be a string")

    unknown = set(node) - {
        "loopmaster",
        "name",
        "version",
        "description",
        "execution",
        "context",
        "budget",
        "error_policy",
        "steps",
    }
    if unknown:
        _err(errors, at, f"unknown top-level keys: {sorted(unknown)}")

    steps = node.get("steps")
    if not isinstance(steps, list) or not steps:
        _err(errors, at, "'steps' must be a non-empty array")
        return errors

    for i, child in enumerate(steps):
        _validate_node(child, f"$.steps[{i}]", errors)
    return errors


def _validate_node(node: Any, at: str, errors: list[str], leaf_only: bool = False) -> None:
    if not isinstance(node, dict):
        _err(errors, at, "step must be an object")
        return

    ntype = node.get("type")
    if ntype not in (_LEAF_TYPES | {"parallel", "conditional"} | set(_RESERVED_PHASES)):
        _err(
            errors,
            at,
            f"unknown step type {ntype!r}; expected one of "
            f"{sorted(_LEAF_TYPES | {'parallel', 'conditional'})}",
        )
        return

    name = node.get("name")
    if not isinstance(name, str) or not name.strip():
        _err(errors, at, "'name' must be a non-empty string")

    if ntype in _RESERVED_PHASES:
        _err(errors, at, f"type '{ntype}' is reserved for {_RESERVED_PHASES[ntype]}")
        return

    if leaf_only and ntype not in _LEAF_TYPES:
        _err(errors, at, f"'{ntype}' cannot be nested inside parallel")

    if ntype == "llm":
        prompt = node.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            _err(errors, at, "llm step requires non-empty string 'prompt'")
        model = node.get("model")
        if model is not None and not isinstance(model, str):
            _err(errors, at, "llm 'model' must be a string or alias (@fast)")

    elif ntype == "shell":
        command = node.get("command")
        if not isinstance(command, str) and not (
            isinstance(command, list) and command and all(isinstance(c, str) for c in command)
        ):
            _err(errors, at, "shell step requires 'command' as string or array of strings")
        _check_positive_number(node, "timeout", at, errors)
        env = node.get("env")
        if env is not None and not (
            isinstance(env, dict) and all(isinstance(v, str) for v in env.values())
        ):
            _err(errors, at, "shell 'env' must map strings to strings")

    elif ntype == "http":
        url = node.get("url")
        if not isinstance(url, str) or not url.strip():
            _err(errors, at, "http step requires non-empty string 'url'")
        method = node.get("method", "GET")
        if not isinstance(method, str) or method.upper() not in _HTTP_METHODS:
            _err(errors, at, f"http 'method' must be one of {sorted(_HTTP_METHODS)}")
        _check_positive_number(node, "timeout", at, errors)
        status = node.get("allowed_status")
        if status is not None and not (
            isinstance(status, list) and status and all(isinstance(s, int) for s in status)
        ):
            _err(errors, at, "http 'allowed_status' must be a non-empty array of integers")

    elif ntype == "mcp":
        tool_name = node.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            _err(errors, at, "mcp step requires non-empty string 'tool_name'")
        server_command = node.get("server_command")
        if not isinstance(server_command, str) and not (
            isinstance(server_command, list)
            and server_command
            and all(isinstance(c, str) for c in server_command)
        ):
            _err(errors, at, "mcp step requires 'server_command' as string or array of strings")
        args = node.get("arguments")
        if args is not None and not isinstance(args, dict):
            _err(errors, at, "mcp 'arguments' must be an object")
        _check_positive_number(node, "timeout", at, errors)

    elif ntype == "parallel":
        children = node.get("steps")
        if not isinstance(children, list) or not children:
            _err(errors, at, "parallel requires non-empty 'steps' array")
            return
        for i, child in enumerate(children):
            _validate_node(child, f"{at}.steps[{i}]", errors, leaf_only=True)

    elif ntype == "conditional":
        condition = node.get("condition")
        if not isinstance(condition, str) or not condition.strip():
            _err(errors, at, "conditional requires non-empty string 'condition'")
        then_branch = node.get("then")
        if not isinstance(then_branch, list) or not then_branch:
            _err(errors, at, "conditional requires non-empty 'then' array")
            return
        for i, child in enumerate(then_branch):
            _validate_node(child, f"{at}.then[{i}]", errors)
        else_branch = node.get("else")
        if else_branch is not None:
            if not isinstance(else_branch, list):
                _err(errors, at, "conditional 'else' must be an array")
            else:
                for i, child in enumerate(else_branch):
                    _validate_node(child, f"{at}.else[{i}]", errors)


def _check_positive_number(node: dict[str, Any], key: str, at: str, errors: list[str]) -> None:
    val = node.get(key)
    if key in node and (not isinstance(val, (int, float)) or val <= 0):
        _err(errors, at, f"'{key}' must be a positive number")


def _condition_ast_error(condition: str) -> str | None:
    """Return an error message if a condition is outside the AST whitelist.

    Placeholders ({var}) are substituted with literal stubs before parsing
    because resolve_prompt replaces them with values prior to evaluation.
    """
    probe = _PLACEHOLDER_RE.sub("zz_ref_stub", condition.strip())
    try:
        tree = ast.parse(probe, mode="eval")
    except SyntaxError as exc:
        return f"syntax error: {exc.msg}"
    for sub in ast.walk(tree):
        if not isinstance(sub, _ALLOWED_COND_NODES):
            return (
                f"disallowed construct '{type(sub).__name__}' "
                f"(only comparisons, and/or/not, names and literals are allowed)"
            )
    return None


def _check_template_refs(template: str, known: dict[str, str], at: str, errors: list[str]) -> None:
    """Validate {placeholder} references against known sources (A1/A6/H2)."""
    strict_spans = [m.span() for m in _PLACEHOLDER_RE.finditer(template)]
    for match in _PLACEHOLDER_RE.finditer(template):
        ref = match.group(1)
        root, _, leaf = ref.partition(".")
        src_type = known.get(root)
        if src_type is None:
            _err(
                errors,
                at,
                f"unknown placeholder {{{ref}}} — no context key or preceding step named '{root}'",
            )
        elif src_type in ("shell", "http", "mcp") and not leaf:
            _err(
                errors,
                at,
                f"{{{ref}}} resolves to a raw result object; use '{{{ref}.stdout}}'",
            )
    for cand in _BRACE_CANDIDATE_RE.finditer(template):
        cand_start, cand_end = cand.span()
        if any(cand_start >= s and cand_end <= e for (s, e) in strict_spans):
            continue
        inner = cand.group(0)[1:-1].strip()
        if not inner or not _PLAUSIBLE_REF_RE.fullmatch(inner):
            continue
        _err(
            errors,
            at,
            f"'{cand.group(0)}' looks like a reference but is invalid — "
            "names must match [a-zA-Z_][word chars/dots], e.g. '{step_name.stdout}'",
        )


def _collect_strings(value: Any) -> list[str]:
    """Recursively collect string leaves from JSON-ish structures."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_collect_strings(item))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_collect_strings(item))
        return out
    return []


def _walk_semantics(
    nodes: list[Any],
    known: dict[str, str],
    errors: list[str],
    prefix: str,
) -> dict[str, str]:
    """Validate placeholder refs and conditions against declaration order.

    ``known`` maps names to their output kind ("context", "llm", "shell",
    "http", "mcp", "branch"). Returns the mapping of step names registered
    at this level (merged into ``known`` as walking progresses).
    """
    registered: dict[str, str] = {}
    for i, node in enumerate(nodes):
        at = f"{prefix}[{i}]"
        if not isinstance(node, dict):
            continue
        ntype = node.get("type")
        name = node.get("name")

        if ntype == "llm":
            prompt = node.get("prompt")
            if isinstance(prompt, str):
                _check_template_refs(prompt, known, at, errors)

        elif ntype == "shell":
            command = node.get("command")
            for item in [command] if isinstance(command, str) else list(command or []):
                if isinstance(item, str):
                    _check_template_refs(item, known, at, errors)
            for env_value in (node.get("env") or {}).values():
                if isinstance(env_value, str):
                    _check_template_refs(env_value, known, at, errors)

        elif ntype == "http":
            url = node.get("url")
            if isinstance(url, str):
                _check_template_refs(url, known, at, errors)
            for header_value in (node.get("headers") or {}).values():
                if isinstance(header_value, str):
                    _check_template_refs(header_value, known, at, errors)
            for text in _collect_strings(node.get("json_data")):
                _check_template_refs(text, known, at, errors)
            data_field = node.get("data")
            if isinstance(data_field, str):
                _check_template_refs(data_field, known, at, errors)

        elif ntype == "mcp":
            for text in _collect_strings(node.get("arguments")):
                _check_template_refs(text, known, at, errors)

        elif ntype == "parallel":
            # Children run concurrently: siblings must not reference each
            # other's outputs; only the enclosing scope is visible. Merge all
            # registrations AFTER the loop so later siblings stay blind to
            # earlier ones.
            sibling_merged: dict[str, str] = {}
            for j, child in enumerate(node.get("steps") or []):
                reg_child = _walk_semantics([child], dict(known), errors, f"{at}.steps[{j}]")
                sibling_merged.update(reg_child)
            known.update(sibling_merged)
            registered.update(sibling_merged)

        elif ntype == "conditional":
            condition = node.get("condition")
            if isinstance(condition, str):
                for match in _PLACEHOLDER_RE.finditer(condition):
                    root = match.group(1).partition(".")[0]
                    if known.get(root) is None:
                        _err(
                            errors,
                            at,
                            f"unknown placeholder {{{match.group(1)}}} in condition",
                        )
                cond_err = _condition_ast_error(condition)
                if cond_err:
                    _err(errors, at, f"invalid condition: {cond_err}")
            reg_then = _walk_semantics(node.get("then") or [], dict(known), errors, f"{at}.then")
            reg_else = _walk_semantics(node.get("else") or [], dict(known), errors, f"{at}.else")
            for branch_reg in (reg_then, reg_else):
                known.update(branch_reg)
                registered.update(branch_reg)

        if (
            isinstance(name, str)
            and name.strip()
            and ntype
            in (
                *_LEAF_TYPES,
                "parallel",
                "conditional",
            )
        ):
            known[name] = ntype
            registered[name] = ntype
    return registered


def _build_steps(spec_steps: list[Any], *, source_path: str | None = None) -> list[Any]:
    return [_build_node(s, source_path=source_path) for s in spec_steps]


def _build_node(node: dict[str, Any], **_kwargs: Any) -> Any:
    """Build a runtime object (Step / Parallel / Conditional) from a validated node."""
    ntype = node["type"]
    name = node["name"]

    if ntype == "llm":
        kwargs: dict[str, Any] = {}
        if "timeout" in node:
            kwargs["timeout"] = node["timeout"]
        return Step(name=name, model=node.get("model"), prompt=node["prompt"], **kwargs)

    if ntype == "shell":
        return Step(
            name=name,
            executor=ShellExecutor(
                command=node["command"],
                cwd=node.get("cwd"),
                env=node.get("env"),
                timeout=float(node.get("timeout", 60.0)),
                capture_output=bool(node.get("capture_output", True)),
                check=bool(node.get("check", False)),
                shell=bool(node.get("shell", False)),
            ),
            timeout=node.get("timeout"),
        )

    if ntype == "http":
        return Step(
            name=name,
            executor=HTTPExecutor(
                url=node["url"],
                method=node.get("method", "GET"),
                headers=node.get("headers"),
                json_data=node.get("json_data"),
                data=node.get("data"),
                timeout=float(node.get("timeout", 30.0)),
                json_output=bool(node.get("json_output", True)),
                allowed_status=node.get("allowed_status"),
            ),
            timeout=node.get("timeout"),
        )

    if ntype == "mcp":
        return Step(
            name=name,
            executor=MCPToolExecutor(
                server_command=node["server_command"],
                tool_name=node["tool_name"],
                arguments=node.get("arguments"),
                timeout=float(node.get("timeout", 60.0)),
                cwd=node.get("cwd"),
                env=node.get("env"),
            ),
            timeout=node.get("timeout"),
        )

    if ntype == "parallel":
        children = [Step(**_leaf_fields(c)) for c in node["steps"]]
        return Parallel(*children)

    if ntype == "conditional":
        then_steps = [_build_node(c) for c in node["then"]]
        else_raw = node.get("else")
        else_steps = [_build_node(c) for c in (else_raw or [])]
        return Conditional(
            condition=node["condition"],
            then_steps=then_steps,
            else_steps=else_steps,
            name=name,
        )

    raise SpecValidationError([f"unbuildable type '{ntype}'"])


def _leaf_fields(node: dict[str, Any]) -> dict[str, Any]:
    """Extract Step constructor fields for parallel leaf nodes."""
    ntype = node["type"]
    base: dict[str, Any] = {"name": node["name"]}
    if ntype == "llm":
        base.update(model=node.get("model"), prompt=node["prompt"])
    elif ntype == "shell":
        base["executor"] = ShellExecutor(
            command=node["command"],
            cwd=node.get("cwd"),
            env=node.get("env"),
            timeout=float(node.get("timeout", 60.0)),
            shell=bool(node.get("shell", False)),
        )
    elif ntype == "http":
        base["executor"] = HTTPExecutor(
            url=node["url"],
            method=node.get("method", "GET"),
            headers=node.get("headers"),
            json_data=node.get("json_data"),
            timeout=float(node.get("timeout", 30.0)),
        )
    elif ntype == "mcp":
        base["executor"] = MCPToolExecutor(
            server_command=node["server_command"],
            tool_name=node["tool_name"],
            arguments=node.get("arguments"),
            timeout=float(node.get("timeout", 60.0)),
        )
    else:
        raise SpecValidationError([f"type '{ntype}' cannot nest inside parallel"])
    return base


def _parse_budget(raw: dict[str, Any] | None) -> Budget | None:
    if not raw:
        return None
    return Budget(
        max_cost=raw.get("max_cost"),
        max_tokens=raw.get("max_tokens"),
        max_steps=raw.get("max_steps"),
    )


def _parse_error_policy(raw: dict[str, Any] | None) -> ErrorPolicy | None:
    if not raw:
        return None
    action = raw.get("on_failure")
    recovery = RecoveryAction[action.upper()] if action else RecoveryAction.ABORT
    return ErrorPolicy(
        retry=raw.get("retry", 2),
        backoff=raw.get("backoff", 1.0),
        on_failure=recovery,
        fallback_model=raw.get("fallback_model"),
    )
