"""Detached loop worker: runs LoopEngine in daemon threads inside the host process."""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from loopmaster.core.engine import LoopEngine
from loopmaster.core.policies import ErrorPolicy, RecoveryAction
from loopmaster.core.types import LoopDef
from loopmaster.mcp.job_store import ACTIVE_STATUSES, TERMINAL_STATUSES, JobStore, is_pid_alive

logger = logging.getLogger("loopmaster.mcp.worker")

EngineFactory = Callable[[threading.Event], LoopEngine]

DEFAULT_CHECKPOINT_DIR = ".loopmaster/checkpoints"


def _default_engine_factory(
    cancel_event: threading.Event,
    checkpoint_dir: str = DEFAULT_CHECKPOINT_DIR,
) -> LoopEngine:
    """Build a LoopEngine with LLM support when config exists, shell-only otherwise."""
    llm_client = None
    try:
        from loopmaster.llm import LLMClient, get_llm_config

        config = get_llm_config()
        if config is not None:
            llm_client = LLMClient(config=config)
    except Exception:
        logger.debug("LLM config unavailable; detached loop runs without llm client", exc_info=True)

    from loopmaster.cost.tracker import CostTracker
    from loopmaster.metrics.collector import MetricsCollector

    return LoopEngine(
        error_policy=ErrorPolicy(retry=2, backoff=1.0, on_failure=RecoveryAction.SKIP),
        cost_tracker=CostTracker(),
        metrics_collector=MetricsCollector(),
        llm_client=llm_client,
        cancel_event=cancel_event,
        checkpoint_dir=checkpoint_dir,
    )


def _normalize_output(value: Any) -> Any:
    """Convert executor result objects into JSON-friendly values."""
    if hasattr(value, "content"):
        return value.content
    if hasattr(value, "stdout"):
        return {
            "stdout": getattr(value, "stdout", None),
            "stderr": getattr(value, "stderr", None),
            "returncode": getattr(value, "returncode", None),
        }
    return value


def _format_results(results: dict[str, Any]) -> dict[str, Any]:
    """Serialize StepResult outputs (failures included)."""
    out: dict[str, Any] = {}
    for name, res in results.items():
        value = _normalize_output(res.output)
        if res.success:
            out[name] = value
        else:
            out[name] = {"success": False, "output": value, "error": getattr(res, "error", None)}
    return out


class DetachedRunner:
    """Runs loop definitions in daemon threads within the host process.

    Jobs are tracked in the shared JobStore so MCP tools can poll progress.
    Threads never outlive the process, satisfying the no-orphan-processes
    requirement: when the host (e.g. opencode) exits, everything is gone.
    """

    def __init__(
        self,
        store: JobStore,
        engine_factory: EngineFactory | None = None,
        checkpoint_dir: str = DEFAULT_CHECKPOINT_DIR,
        poll_s: float = 1.0,
        heartbeat_s: float = 60.0,
    ) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._events: dict[str, threading.Event] = {}
        self._checkpoint_dir = checkpoint_dir
        self._poll_s = poll_s
        self._heartbeat_s = heartbeat_s
        self._engine_factory: EngineFactory = engine_factory or (
            lambda cancel_event: _default_engine_factory(cancel_event, checkpoint_dir)
        )

    def submit(
        self,
        loop_def: LoopDef,
        initial_context: dict[str, Any] | None = None,
        definition: dict[str, Any] | None = None,
        job_id: str | None = None,
    ) -> str:
        """Create a running job and start it in a daemon thread. Returns job_id."""
        if job_id is None:
            job_id = f"{loop_def.name}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        steps = list(getattr(loop_def, "_collected_steps", None) or [])
        definition = definition or {"step_count": len(steps)}

        existing = self._store.get_job(job_id)
        if existing is not None and existing.status in ACTIVE_STATUSES:
            lease_pid = (existing.metrics or {}).get("host_pid")
            if is_pid_alive(lease_pid):
                raise ValueError(f"job '{job_id}' already running on live host pid {lease_pid}")

        self._store.create_job(
            job_id=job_id,
            loop_name=loop_def.name,
            definition=definition,
            status="running",
            total_steps=len(steps),
            metrics={"host_pid": os.getpid(), "detached": True},
        )

        cancel_event = threading.Event()
        with self._lock:
            self._events[job_id] = cancel_event

        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, loop_def, dict(initial_context or {}), cancel_event),
            name=f"lm-loop-{job_id}",
            daemon=True,
        )
        thread.start()
        return job_id

    def request_cancel(self, job_id: str) -> bool:
        """Signal cancellation for a tracked job. False when unknown/finished."""
        with self._lock:
            event = self._events.get(job_id)
        if event is None:
            return False
        event.set()
        return True

    def is_running(self, job_id: str) -> bool:
        """True while the job's worker thread is still alive."""
        with self._lock:
            return job_id in self._events

    def _watch_job(
        self,
        job_id: str,
        cancel_event: threading.Event,
        stop: threading.Event,
    ) -> None:
        """Poll JobStore for cross-process cancellation; emit heartbeats while active.

        Never holds the store lock while sleeping, so concurrent MCP reads stay
        unblocked. Heartbeats refresh updated_at so stale-detectors do not
        misreport a live detached worker as dead.
        """
        next_beat = time.monotonic() + self._heartbeat_s
        while not stop.wait(self._poll_s):
            try:
                job = self._store.get_job(job_id)
            except Exception:
                logger.debug("Watcher read failed for %s", job_id, exc_info=True)
                continue
            if job is None or job.status in TERMINAL_STATUSES:
                return
            if job.status == "cancelled":
                cancel_event.set()
                return
            now = time.monotonic()
            if now >= next_beat:
                try:
                    self._store.touch_job(job_id)
                except Exception:
                    logger.debug("Heartbeat failed for %s", job_id, exc_info=True)
                next_beat = now + self._heartbeat_s

    def _run_job(
        self,
        job_id: str,
        loop_def: LoopDef,
        ctx_data: dict[str, Any],
        cancel_event: threading.Event,
    ) -> None:
        started = time.time()
        stop_watch = threading.Event()
        try:
            engine = self._engine_factory(cancel_event)
            engine.on_step_complete(lambda result: self._on_step(job_id, result))
            watcher = threading.Thread(
                target=self._watch_job,
                args=(job_id, cancel_event, stop_watch),
                name=f"lm-watch-{job_id}",
                daemon=True,
            )
            watcher.start()
            run_result = engine.run(loop_def, initial_context=ctx_data, job_id=job_id)
        except Exception as exc:  # noqa: BLE001 - worker boundary must persist failures
            logger.exception("Detached loop %s crashed", job_id)
            cancelled = cancel_event.is_set()
            self._store.update_job(
                job_id=job_id,
                status="cancelled" if cancelled else "failed",
                error="Cancelled by user request" if cancelled else str(exc),
                metrics={"duration_ms": int((time.time() - started) * 1000)},
                completed=True,
            )
        else:
            duration_ms = int((time.time() - started) * 1000)
            output_results = _format_results(run_result.results)
            metrics = {
                "host_pid": os.getpid(),
                "total_cost": round(run_result.total_cost, 6),
                "total_tokens": run_result.total_tokens,
                "duration_ms": duration_ms,
            }
            if run_result.success:
                self._store.update_job(
                    job_id=job_id,
                    status="completed",
                    results=output_results,
                    metrics=metrics,
                    completed=True,
                )
            else:
                self._store.update_job(
                    job_id=job_id,
                    status="failed",
                    results=output_results,
                    error=run_result.error or "Loop execution failed",
                    metrics=metrics,
                    completed=True,
                )
        finally:
            stop_watch.set()
            with self._lock:
                self._events.pop(job_id, None)

    def _on_step(self, job_id: str, result: Any) -> None:
        try:
            value = _normalize_output(result.output)
            self._store.record_step_result(
                job_id=job_id,
                step_name=result.step_name,
                success=result.success,
                output=value,
                error=result.error,
            )
        except Exception:
            logger.debug(
                "Progress update failed for %s/%s",
                job_id,
                getattr(result, "step_name", "?"),
                exc_info=True,
            )
