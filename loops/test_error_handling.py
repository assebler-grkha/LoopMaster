"""Test loop with error policies for LoopMaster MCP verification."""

from loopmaster.core.types import ErrorPolicy, Loop, RecoveryAction, Step


@Loop(name="error_handling_test", version="0.1.0", budget="$10.00")
def error_handling_loop(ctx):
    Step(
        "risky_step",
        model="gpt-4",
        prompt="Try to do something that might fail",
        on_error=ErrorPolicy(retry=2, on_failure=RecoveryAction.RETRY),
    )
    Step(
        "fallback_step",
        model="gpt-4o-mini",
        prompt="Cheaper fallback task",
        on_error=ErrorPolicy(retry=1, on_failure=RecoveryAction.FALLBACK, fallback_model="gpt-4"),
    )
    Step("final", model="gpt-4", prompt="Wrap up: {{risky_step}} + {{fallback_step}}")
    return ctx
