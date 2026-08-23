"""Scenario 2: Parallel Execution — Concurrent steps with Parallel()."""

from loopmaster.core.types import Loop, Parallel, Step

MODEL = "stealth/ox-alpha"


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
