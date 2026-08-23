"""Integration tests for LoopEngine with LLMClient."""

from __future__ import annotations

import pytest

from loopmaster.core import (
    Budget,
    BudgetExceededError,
    ErrorPolicy,
    Loop,
    LoopEngine,
    RecoveryAction,
    Step,
)
from loopmaster.cost.tracker import CostTracker
from loopmaster.llm import LLMResponse, RateLimitError
from loopmaster.metrics.collector import MetricsCollector


class MockLLMClient:
    def __init__(self, responses: dict[str, LLMResponse] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[dict[str, str]] = []

    def complete(
        self, prompt: str, system: str | None = None, model: str | None = None
    ) -> LLMResponse:
        self.calls.append({"prompt": prompt, "model": model or ""})
        for key, resp in self.responses.items():
            if key in prompt:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return LLMResponse(
            content=f"Echo: {prompt}",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            model=model or "mock-model",
        )


class TestEngineLLMIntegration:
    def test_multi_step_context_flow(self):
        """Verify that string output from step 1 is passed to step 2 via context."""
        mock_client = MockLLMClient(
            responses={
                "Say hello": LLMResponse(
                    content="Hello Agent!",
                    prompt_tokens=5,
                    completion_tokens=10,
                    total_tokens=15,
                    model="gpt-4o",
                ),
                "Explain LoopMaster": LLMResponse(
                    content="LoopMaster is a lightweight loop engine.",
                    prompt_tokens=8,
                    completion_tokens=12,
                    total_tokens=20,
                    model="gpt-4o",
                ),
            }
        )

        @Loop(name="flow_test")
        def flow_loop(ctx):
            Step("greet", model="gpt-4o", prompt="Say hello")
            Step("task", model="gpt-4o", prompt="Explain LoopMaster")
            Step("summary", model="gpt-4o", prompt="Combine: {{greet}} and {{task}}")

        cost_tracker = CostTracker()
        collector = MetricsCollector()
        engine = LoopEngine(
            cost_tracker=cost_tracker,
            metrics_collector=collector,
            llm_client=mock_client,
        )

        result = engine.run(flow_loop)

        assert result.success is True
        assert len(result.results) == 3
        assert result.results["greet"].output == "Hello Agent!"
        assert result.results["task"].output == "LoopMaster is a lightweight loop engine."
        assert (
            result.results["summary"].output
            == "Echo: Combine: Hello Agent! and LoopMaster is a lightweight loop engine."
        )
        assert result.total_tokens > 0
        assert result.total_cost > 0.0

    def test_token_budget_exceeded(self):
        """Verify budget.max_tokens limits loop execution."""
        mock_client = MockLLMClient()

        @Loop(name="token_budget_loop", budget=Budget(max_tokens=25))
        def token_loop(ctx):
            Step("s1", model="gpt-4o", prompt="First step")  # 30 tokens
            Step("s2", model="gpt-4o", prompt="Second step")

        engine = LoopEngine(
            budget=Budget(max_tokens=25),
            llm_client=mock_client,
        )

        with pytest.raises(BudgetExceededError) as exc_info:
            engine.run(token_loop)
        assert "tokens" in str(exc_info.value).lower()

    def test_cost_budget_exceeded(self):
        """Verify budget.max_cost limits loop execution."""
        mock_client = MockLLMClient()
        tracker = CostTracker()

        @Loop(name="cost_budget_loop")
        def cost_loop(ctx):
            Step("s1", model="gpt-4-turbo", prompt="Step 1")
            Step("s2", model="gpt-4-turbo", prompt="Step 2")

        engine = LoopEngine(
            budget=Budget(max_cost=0.00001),
            cost_tracker=tracker,
            llm_client=mock_client,
        )

        with pytest.raises(BudgetExceededError):
            engine.run(cost_loop)

    def test_rate_limit_retry_success(self):
        """Verify RateLimitError is retried and succeeds on attempt 2."""
        call_count = 0

        class FlakyLLMClient:
            def complete(self, prompt: str, system: str = None, model: str = None):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RateLimitError("HTTP 429: Too Many Requests")
                return LLMResponse(
                    content="Recovered after retry",
                    prompt_tokens=10,
                    completion_tokens=10,
                    total_tokens=20,
                    model="gpt-4o",
                )

        @Loop(name="retry_test")
        def retry_loop(ctx):
            Step(
                "risky",
                model="gpt-4o",
                prompt="Do something risky",
                on_error=ErrorPolicy(retry=2, backoff=0.01, on_failure=RecoveryAction.ABORT),
            )

        collector = MetricsCollector()
        engine = LoopEngine(
            metrics_collector=collector,
            llm_client=FlakyLLMClient(),
        )

        result = engine.run(retry_loop)
        assert result.success is True
        assert result.results["risky"].output == "Recovered after retry"
        assert call_count == 2

    def test_model_fallback_execution_and_cost(self):
        """Verify that fallback model executes and records the fallback model and cost."""

        class FallbackLLMClient:
            def complete(self, prompt: str, system: str = None, model: str = None):
                if model == "expensive-unavailable-model":
                    raise RateLimitError("Out of quota")
                return LLMResponse(
                    content=f"Executed with {model}",
                    prompt_tokens=10,
                    completion_tokens=10,
                    total_tokens=20,
                    model=model or "gpt-4o-mini",
                )

        @Loop(name="fallback_test")
        def fallback_loop(ctx):
            Step(
                "step1",
                model="expensive-unavailable-model",
                prompt="Prompt 1",
                on_error=ErrorPolicy(
                    retry=1,
                    on_failure=RecoveryAction.FALLBACK,
                    fallback_model="gpt-4o-mini",
                ),
            )

        tracker = CostTracker()
        engine = LoopEngine(
            cost_tracker=tracker,
            llm_client=FallbackLLMClient(),
        )

        result = engine.run(fallback_loop)
        assert result.success is True
        assert result.results["step1"].model == "gpt-4o-mini"
        assert "Executed with gpt-4o-mini" in result.results["step1"].output
        assert "gpt-4o-mini" in tracker.cost_by_model()
