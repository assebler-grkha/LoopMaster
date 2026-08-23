"""Scenario 4: Error Handling + Fallback — ErrorPolicy with retry and fallback."""

from loopmaster.core.types import ErrorPolicy, Loop, RecoveryAction, Step

MODEL = "stealth/ox-alpha"


@Loop(name="test_error_fallback", version="1.0.0")
def test_error_fallback(ctx):
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
