"""Scenario 3: Conditional Branching — then/else based on step output."""

from loopmaster.core.types import Conditional, Loop, Step

MODEL = "stealth/ox-alpha"


@Loop(name="test_conditional", version="1.0.0")
def test_conditional(ctx):
    Step("check", model=MODEL, prompt="Answer with ONLY the word 'yes' or 'no': Is the sky blue?")
    Conditional(
        condition="'yes' in '{{check}}'.lower()",
        then_steps=[
            Step(
                "confirm",
                model=MODEL,
                prompt="Great! The sky is indeed blue. Confirm this fact.",
            ),
        ],
        else_steps=[
            Step("deny", model=MODEL, prompt="Interesting. Explain why the sky might not be blue."),
        ],
    )
    return ctx
