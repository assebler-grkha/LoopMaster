"""Scenario 6: ErrorPolicy — Retry + fallback with a flaky executor."""

from loopmaster.core.types import ErrorPolicy, Loop, RecoveryAction, Step
from loopmaster.llm.types import RateLimitError

MODEL = "stealth/ox-alpha"


class FlakyExecutor:
    """Simulates a step that fails with RateLimitError on first 2 calls, then succeeds."""

    def __init__(self):
        self.call_count = 0

    def execute(self, ctx_data):
        self.call_count += 1
        if self.call_count <= 2:
            raise RateLimitError("Rate limit exceeded (HTTP 429)")
        return "success after retries"


_flaky = FlakyExecutor()


@Loop(name="test_error_policy", version="1.0.0")
def test_error_policy(ctx):
    Step(
        "flaky_step",
        model=MODEL,
        executor=_flaky,
        on_error=ErrorPolicy(
            retry=2,
            backoff=0.1,
            on_failure=RecoveryAction.FALLBACK,
            fallback_model=MODEL,
        ),
    )
    Step("final", model=MODEL, prompt="Summarize what happened in the previous step.")
    return ctx
