"""Built-in loop templates."""

from __future__ import annotations

TEMPLATES: dict[str, str] = {
    "reflection": ("Self-improving loop: execute -> evaluate -> revise -> repeat"),
    "tool_use": (
        "Agent calls tools in a loop: decide -> call tool -> process result -> decide again"
    ),
    "planning": ("Plan -> execute steps -> verify results -> replan if needed"),
    "multi_agent": ("Multiple agents work in parallel on subtasks, results merged"),
    "critique": ("Generate -> critique -> revise -> repeat until critique is satisfied"),
    "escalation": ("Try cheap model first, escalate to expensive model on failure"),
    "hybrid": ("Combination of reflection + tool_use + planning patterns"),
}

_TEMPLATE_CODE: dict[str, str] = {
    "reflection": '''\
"""Reflection loop: execute -> evaluate -> revise -> repeat."""

from loopmaster import Loop, Step, ErrorPolicy, RecoveryAction


@Loop(name="{name}", version="0.1.0")
def {func_name}(ctx):
    Step("execute", model="gpt-4", prompt="Complete the task: {task}")
    Step("evaluate", model="gpt-4", prompt="Evaluate the result from execute step")
    Step("revise", model="gpt-4", prompt="Improve based on evaluation")
    return ctx
''',
    "tool_use": '''\
"""Tool-use loop: decide -> call tool -> process result -> decide again."""

from loopmaster import Loop, Step


@Loop(name="{name}", version="0.1.0")
def {func_name}(ctx):
    Step("decide", model="gpt-4", prompt="Decide which tool to use for: {task}")
    Step("call_tool", tool="{tool}", input={{"query": "{{decide}}"}})
    Step("process", model="gpt-4", prompt="Process tool result: {{call_tool}}")
    return ctx
''',
    "planning": '''\
"""Planning loop: plan -> execute -> verify -> replan."""

from loopmaster import Loop, Step


@Loop(name="{name}", version="0.1.0")
def {func_name}(ctx):
    Step("plan", model="gpt-4", prompt="Create a plan for: {task}")
    Step("execute", model="gpt-4", prompt="Execute this plan: {{plan}}")
    Step("verify", model="gpt-4", prompt="Verify the results: {{execute}}")
    return ctx
''',
    "multi_agent": '''\
"""Multi-agent loop: parallel subtasks with result merging."""

from loopmaster import Loop, Step, Parallel


@Loop(name="{name}", version="0.1.0")
def {func_name}(ctx):
    Parallel(
        Step("agent_a", model="gpt-4", prompt="Subtask A: {task}"),
        Step("agent_b", model="gpt-4", prompt="Subtask B: {task}"),
        Step("agent_c", model="gpt-4", prompt="Subtask C: {task}"),
    )
    Step(
        "merge",
        model="gpt-4",
        prompt="Merge results: A={{agent_a}}, B={{agent_b}}, C={{agent_c}}",
    )
    return ctx
''',
    "critique": '''\
"""Critique loop: generate -> critique -> revise until satisfied."""

from loopmaster import Loop, Step


@Loop(name="{name}", version="0.1.0")
def {func_name}(ctx):
    Step("generate", model="gpt-4", prompt="Generate content for: {task}")
    Step("critique", model="gpt-4", prompt="Critique this output: {{generate}}")
    Step("revise", model="gpt-4", prompt="Revise based on critique: {{critique}}")
    return ctx
''',
    "escalation": '''\
"""Escalation loop: try cheap model, escalate on failure."""

from loopmaster import Loop, Step, ErrorPolicy, RecoveryAction


@Loop(name="{name}", version="0.1.0")
def {func_name}(ctx):
    Step(
        "cheap_attempt",
        model="gpt-4o-mini",
        prompt="Complete: {task}",
        on_error=ErrorPolicy(
            retry=1,
            on_failure=RecoveryAction.FALLBACK,
            fallback_model="gpt-4",
        ),
    )
    return ctx
''',
    "hybrid": '''\
"""Hybrid loop: reflection + tool use + planning combined."""

from loopmaster import Loop, Step


@Loop(name="{name}", version="0.1.0")
def {func_name}(ctx):
    Step("plan", model="gpt-4", prompt="Plan the approach for: {task}")
    Step("research", model="gpt-4", prompt="Research context: {{plan}}")
    Step("execute", model="gpt-4", prompt="Execute plan: {{plan}}, context: {{research}}")
    Step("evaluate", model="gpt-4", prompt="Evaluate result: {{execute}}")
    Step("finalize", model="gpt-4", prompt="Final output based on evaluation: {{evaluate}}")
    return ctx
''',
}


def get_template(name: str) -> str:
    """Get a template description by name."""
    if name not in TEMPLATES:
        available = ", ".join(TEMPLATES.keys())
        msg = f"Unknown template '{name}'. Available: {available}"
        raise ValueError(msg)
    return TEMPLATES[name]


def generate_code(
    name: str,
    *,
    name_var: str = "my_loop",
    task: str = "...",
    tool: str = "search",
) -> str:
    """Generate runnable Python code for a template.

    Args:
        name: Template name (reflection, tool_use, planning, etc.)
        name_var: Loop name variable for the decorator
        task: Task description placeholder
        tool: Tool name for tool_use template

    Returns:
        Complete Python file content.
    """
    if name not in _TEMPLATE_CODE:
        available = ", ".join(_TEMPLATE_CODE.keys())
        msg = f"Unknown template '{name}'. Available: {available}"
        raise ValueError(msg)

    func_name = name_var.replace("-", "_")
    safe_task = task.replace("{", "{{").replace("}", "}}") if task else task
    safe_tool = tool.replace("{", "{{").replace("}", "}}") if tool else tool
    return _TEMPLATE_CODE[name].format(
        name=name_var,
        func_name=func_name,
        task=safe_task,
        tool=safe_tool,
    )


def list_templates() -> dict[str, str]:
    """Return all template names and descriptions."""
    return dict(TEMPLATES)
