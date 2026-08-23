"""Scenario 5: Budget Limit — Max-steps enforcement (Budget(max_steps=2))."""

from loopmaster.core.types import Budget, Loop, Step

MODEL = "stealth/ox-alpha"


@Loop(name="test_budget_limit", version="1.0.0", budget=Budget(max_steps=2))
def test_budget_limit(ctx):
    Step("step1", model=MODEL, prompt="Write a haiku about coding.")
    Step("step2", model=MODEL, prompt="Translate your haiku to emoji.")
    Step("step3", model=MODEL, prompt="Rate the emoji translation 1-10 and explain.")
    return ctx
