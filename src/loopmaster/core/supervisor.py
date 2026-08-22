"""Custom concurrency supervisor for parallel step execution.

NOT asyncio.TaskGroup — TaskGroup cancels ALL tasks on any exception.
This supervisor collects results and applies ErrorPolicy per-step.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from loopmaster.core.types import Step, StepResult


@dataclass
class SupervisorResult:
    """Result of running parallel steps under the supervisor.

    Attributes:
        results: Mapping of step name to its execution result.
        all_succeeded: True if every step completed successfully.
        errors: List of error messages for failed steps.
    """

    results: dict[str, StepResult] = field(default_factory=dict)
    all_succeeded: bool = True
    errors: list[str] = field(default_factory=list)


class Supervisor:
    """Custom concurrency supervisor that does NOT cancel siblings on failure.

    Usage:
        supervisor = Supervisor(max_concurrency=5)
        result = await supervisor.run_parallel(steps, ctx_data)
    """

    def __init__(self, max_concurrency: int = 10):
        self.max_concurrency = max_concurrency

    async def run_parallel(
        self,
        steps: list[Step],
        ctx_data: dict[str, Any],
    ) -> SupervisorResult:
        """Execute steps concurrently with bounded concurrency."""
        semaphore = asyncio.Semaphore(self.max_concurrency)
        result = SupervisorResult()

        async def _run_step(step: Step) -> None:
            async with semaphore:
                # Run step.execute in a thread pool to avoid blocking the event loop
                loop = asyncio.get_running_loop()
                step_result = await loop.run_in_executor(None, step.execute, ctx_data)
                result.results[step.name] = step_result
                if not step_result.success:
                    result.all_succeeded = False
                    result.errors.append(f"{step.name}: {step_result.error}")

        tasks = [asyncio.create_task(_run_step(s)) for s in steps]
        # Wait for ALL tasks — never cancel siblings
        await asyncio.gather(*tasks, return_exceptions=False)

        return result
