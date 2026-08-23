"""Tests for LoopEngine real-time lifecycle event emissions and streaming."""

from __future__ import annotations

from collections.abc import Iterator

from loopmaster.core import (
    ErrorPolicy,
    Loop,
    LoopEngine,
    RecoveryAction,
    Step,
)
from loopmaster.cost.tracker import CostTracker
from loopmaster.events import EventEmitter
from loopmaster.llm import LLMResponse, RateLimitError, StreamChunk


class MockStreamingLLMClient:
    def complete(
        self, prompt: str, system: str | None = None, model: str | None = None
    ) -> LLMResponse:
        return LLMResponse(
            content="Fallback completed",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            model=model or "gpt-4o",
        )

    def stream_complete(
        self, prompt: str, system: str | None = None, model: str | None = None
    ) -> Iterator[StreamChunk]:
        words = ["Streamed", " ", "output", " ", "data"]
        for i, word in enumerate(words):
            is_last = i == len(words) - 1
            yield StreamChunk(
                delta=word,
                prompt_tokens=12 if is_last else 0,
                completion_tokens=8 if is_last else 0,
                total_tokens=20 if is_last else 0,
                is_final=is_last,
                model=model or "gpt-4o",
            )


class TestEngineEvents:
    def test_engine_streaming_lifecycle(self):
        @Loop(name="streaming_test")
        def streaming_loop(ctx):
            Step("step1", model="gpt-4o", prompt="Prompt 1")
            Step("step2", model="gpt-4o", prompt="Prompt 2")

        emitter = EventEmitter()
        cost_tracker = CostTracker()
        client = MockStreamingLLMClient()

        engine = LoopEngine(
            event_emitter=emitter,
            cost_tracker=cost_tracker,
            llm_client=client,
        )

        all_events = []
        emitter.on("*", lambda e: all_events.append(e))

        result = engine.run(streaming_loop, job_id="job-stream-123")

        assert result.success is True
        assert result.total_tokens == 40  # 20 + 20
        assert result.total_cost > 0.0

        event_types = [e.event_type for e in all_events]
        assert "loop_started" in event_types
        assert "step_started" in event_types
        assert "step_chunk" in event_types
        assert "step_completed" in event_types
        assert "loop_completed" in event_types

        # Verify progress in step_completed
        step_completed_events = [e for e in all_events if e.event_type == "step_completed"]
        assert len(step_completed_events) == 2
        assert step_completed_events[0].payload["progress"] == 0.5
        assert step_completed_events[1].payload["progress"] == 1.0

        # Verify memory safety: step_chunk was dispatched to listeners but NOT kept in emitter.history
        history_types = [e.event_type for e in emitter.history]
        assert "step_chunk" not in history_types
        assert "loop_started" in history_types
        assert "step_completed" in history_types
        assert "loop_completed" in history_types

    def test_engine_retry_event_emitted(self):
        call_count = 0

        class FlakyStreamingClient:
            def stream_complete(
                self, prompt: str, system: str | None = None, model: str | None = None
            ):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise RateLimitError("Rate limit hit")
                yield StreamChunk(
                    delta="Recovered",
                    prompt_tokens=5,
                    completion_tokens=5,
                    total_tokens=10,
                    is_final=True,
                    model=model or "gpt-4o-mini",
                )

        @Loop(name="retry_event_test")
        def retry_loop(ctx):
            Step(
                "step_flaky",
                model="gpt-4o-mini",
                prompt="Do task",
                on_error=ErrorPolicy(retry=2, backoff=0.01, on_failure=RecoveryAction.RETRY),
            )

        emitter = EventEmitter()
        engine = LoopEngine(
            event_emitter=emitter,
            llm_client=FlakyStreamingClient(),
        )

        all_events = []
        emitter.on("*", lambda e: all_events.append(e))

        result = engine.run(retry_loop, job_id="job-retry-test")

        assert result.success is True
        retry_events = [e for e in all_events if e.event_type == "step_retry"]
        assert len(retry_events) == 1
        assert retry_events[0].payload["attempt"] == 2
        assert retry_events[0].payload["reset_buffer"] is True
