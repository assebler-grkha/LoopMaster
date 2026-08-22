"""Deterministic replay — replay loops with recorded responses.

Records all LLM/tool responses during a run. On replay, injects
recorded responses instead of making real API calls. Enables debugging
without burning API credits.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .types import StepResult

logger = logging.getLogger(__name__)


@dataclass
class RecordedStep:
    """A recorded step execution with its input and output."""

    step_name: str
    input_data: dict[str, Any]
    output: Any = None
    error: str | None = None
    tokens_used: int = 0
    cost: float = 0.0
    duration_ms: float = 0.0
    model: str | None = None
    tool: str | None = None


@dataclass
class ReplaySession:
    """Complete recording of a loop execution for deterministic replay."""

    loop_name: str
    loop_version: str
    initial_context: dict[str, Any] = field(default_factory=dict)
    recorded_steps: list[RecordedStep] = field(default_factory=list)
    final_context: dict[str, Any] = field(default_factory=dict)

    def save(self, path: Path) -> None:
        """Save recording to disk as JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> ReplaySession:
        """Load recording from disk."""
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_steps = data.pop("recorded_steps", [])
        steps = []
        for s in raw_steps:
            if isinstance(s, RecordedStep):
                steps.append(s)
            else:
                steps.append(RecordedStep(**s))
        known_fields = {"loop_name", "loop_version", "initial_context", "final_context"}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(recorded_steps=steps, **filtered)


class ResponseRecorder:
    """Records step executions during a live run.

    Usage:
        recorder = ResponseRecorder("research", "0.1.0")
        recorder.set_initial_context({"query": "test"})

        # During each step:
        recorder.record_step("search", input_data, result)

        # After loop completes:
        recorder.set_final_context(ctx.to_dict())
        recorder.save(Path("recordings/research_v0.1.0.json"))
    """

    def __init__(self, loop_name: str, loop_version: str) -> None:
        self._session = ReplaySession(
            loop_name=loop_name,
            loop_version=loop_version,
        )

    def set_initial_context(self, ctx_data: dict[str, Any]) -> None:
        """Set the initial context for the recording session."""
        self._session.initial_context = ctx_data

    def record_step(
        self,
        step_name: str,
        input_data: dict[str, Any],
        result: StepResult,
        model: str | None = None,
        tool: str | None = None,
    ) -> None:
        """Record a step execution with its input and result."""
        self._session.recorded_steps.append(
            RecordedStep(
                step_name=step_name,
                input_data=input_data,
                output=result.output.updates if result.output else None,
                error=result.error,
                tokens_used=result.tokens_used,
                cost=result.cost,
                duration_ms=result.duration_ms,
                model=model,
                tool=tool,
            )
        )

    def set_final_context(self, ctx_data: dict[str, Any]) -> None:
        """Set the final context after loop completion."""
        self._session.final_context = ctx_data

    @property
    def session(self) -> ReplaySession:
        """The recorded replay session."""
        return self._session

    def save(self, path: Path) -> None:
        """Save the recording to disk as JSON."""
        self._session.save(path)


class ReplayRunner:
    """Replays a loop using recorded responses instead of real calls.

    Usage:
        recording = ReplaySession.load(Path("recordings/research_v0.1.0.json"))
        runner = ReplayRunner(recording)

        # Runner provides mock results for each step:
        mock_result = runner.get_mock_result("search")
        # → Returns StepResult matching the recorded output
    """

    def __init__(self, session: ReplaySession) -> None:
        self._session = session
        self._step_index = 0
        self._results_by_name: dict[str, list[RecordedStep]] = {}
        for step in session.recorded_steps:
            self._results_by_name.setdefault(step.step_name, []).append(step)

    @property
    def initial_context(self) -> dict[str, Any]:
        return self._session.initial_context

    @property
    def final_context(self) -> dict[str, Any]:
        return self._session.final_context

    def get_mock_result(self, step_name: str) -> StepResult | None:
        """Get the recorded result for a step by name.

        Returns None if no recording exists for this step.
        """
        recorded = self._results_by_name.get(step_name)
        if not recorded:
            return None

        # Pop first recording for this step name (FIFO)
        step_record = recorded.pop(0)
        if not recorded:
            del self._results_by_name[step_name]

        from .types import StepOutput

        output = None
        if step_record.output is not None:
            output = StepOutput(updates=step_record.output)

        return StepResult(
            step_name=step_name,
            success=step_record.error is None,
            output=output,
            error=step_record.error,
            tokens_used=step_record.tokens_used,
            cost=step_record.cost,
            duration_ms=step_record.duration_ms,
        )

    def get_all_recorded_steps(self) -> list[RecordedStep]:
        """Return all recorded steps in execution order."""
        return list(self._session.recorded_steps)

    def has_recording(self, step_name: str) -> bool:
        """Check whether a recording exists for the given step name."""
        return step_name in self._results_by_name
