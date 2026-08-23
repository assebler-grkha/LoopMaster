"""Test Plan for LoopMaster — 5 scenarios using local-default model.

Scenarios:
1. Simple sequential pipeline — 3 steps with template variable data flow
2. Parallel execution — Parallel(*steps) with concurrent processing
3. Conditional branching — Conditional(condition, then_steps, else_steps)
4. Error handling + fallback — ErrorPolicy with retry and fallback model
5. Budget limit — Budget constraint enforcement
"""

from loopmaster.core.types import (
    Conditional,
    ErrorPolicy,
    Loop,
    Parallel,
    RecoveryAction,
    Step,
)

MODEL = "local-default"


# ─── Scenario 1: Simple Sequential Pipeline ───────────────────────────────────
# Tests: Step chaining, template variables ({{step_name}}), data flow
@Loop(name="test_simple_pipeline", version="1.0.0")
def test_simple_pipeline(ctx):
    Step("greet", model=MODEL, prompt="Say hello in one sentence.")
    Step("topic", model=MODEL, prompt="Name a random interesting topic in 5 words.")
    Step("summary", model=MODEL, prompt="Combine these into a fun fact: {{greet}} about {{topic}}")
    return ctx


# ─── Scenario 2: Parallel Execution ──────────────────────────────────────────
# Tests: Parallel(*steps) — steps run concurrently, results merged
@Loop(name="test_parallel", version="1.0.0")
def test_parallel(ctx):
    Step("setup", model=MODEL, prompt="Write the number 42.")
    Parallel(
        Step("branch_a", model=MODEL, prompt="What is 2+2?"),
        Step("branch_b", model=MODEL, prompt="What is 3+3?"),
        Step("branch_c", model=MODEL, prompt="What is 7+7?"),
    )
    Step(
        "combine",
        model=MODEL,
        prompt="Add these results: {{branch_a}} + {{branch_b}} + {{branch_c}}",
    )
    return ctx


# ─── Scenario 3: Conditional Branching ───────────────────────────────────────
# Tests: Conditional(condition, then_steps, else_steps) with safe AST eval
@Loop(name="test_conditional", version="1.0.0")
def test_conditional(ctx):
    Step("check", model=MODEL, prompt="Answer with ONLY the word 'yes' or 'no': Is the sky blue?")
    Conditional(
        condition="'yes' in '{{check}}'.lower()",
        then_steps=[
            Step(
                "confirm", model=MODEL, prompt="Great! The sky is indeed blue. Confirm this fact."
            ),
        ],
        else_steps=[
            Step("deny", model=MODEL, prompt="Interesting. Explain why the sky might not be blue."),
        ],
    )
    return ctx


# ─── Scenario 4: Error Handling + Fallback ───────────────────────────────────
# Tests: ErrorPolicy(retry=N, on_failure=FALLBACK, fallback_model=...)
@Loop(name="test_error_handling", version="1.0.0")
def test_error_handling(ctx):
    Step("primary", model=MODEL, prompt="Do this task: list 3 prime numbers.")
    Step(
        "resilient",
        model=MODEL,
        prompt="If the previous step worked, repeat its output: {{primary}}",
        on_error=ErrorPolicy(
            retry=2,
            on_failure=RecoveryAction.FALLBACK,
            fallback_model=MODEL,
        ),
    )
    Step("final", model=MODEL, prompt="Summarize: {{primary}} and {{resilient}}")
    return ctx


# ─── Scenario 5: Budget Limit ────────────────────────────────────────────────
# Tests: @Loop(budget=...) — cost tracking and enforcement
@Loop(name="test_budget", version="1.0.0", budget="$0.50")
def test_budget(ctx):
    Step("step1", model=MODEL, prompt="Write a haiku about coding.")
    Step("step2", model=MODEL, prompt="Translate your haiku to emoji.")
    Step("step3", model=MODEL, prompt="Rate the emoji translation 1-10 and explain.")
    return ctx
