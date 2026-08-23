"""MCP transport layer — exposes loop operations as MCP tools.

MCP = thin transport (request-response).
Loop Protocol = own contract for lifecycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from loopmaster.events import LoopEvent

from .job_store import JobData, JobStore, get_job_store

logger = logging.getLogger(__name__)

__all__ = [
    "LoopEvent",
    "LoopJob",
    "LoopProtocol",
    "MCPServer",
    "JobData",
    "JobStore",
    "get_job_store",
]


@dataclass
class LoopJob:
    """Represents a running or completed loop job."""

    job_id: str
    loop_name: str
    status: str = "pending"
    progress: float = 0.0
    result: Any = None
    error: str | None = None
    events: list[LoopEvent] = field(default_factory=list)


class LoopProtocol:
    """Loop lifecycle protocol — independent of MCP transport.

    Manages loop jobs, event subscriptions, and lifecycle operations
    (start, pause, resume, cancel). Can be wrapped by MCPServer or used
    directly.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, LoopJob] = {}
        self._event_queues: dict[str, list[asyncio.Queue[LoopEvent]]] = {}
        self._engines: dict[str, Any] = {}

    def list_loops(self) -> list[dict[str, Any]]:
        """Discover available loop definitions."""
        loops = []
        for _name, engine in self._engines.items():
            for loop_name, loop_def in engine._registry.items():
                loops.append(
                    {
                        "name": loop_name,
                        "version": loop_def.version,
                        "agent": loop_def.agent,
                        "has_budget": loop_def.budget is not None,
                    }
                )
        return loops

    def start_loop(
        self,
        loop_name: str,
        initial_context: dict[str, Any] | None = None,
    ) -> str:
        """Start a loop execution. Returns job_id."""
        job_id = uuid.uuid4().hex[:8]
        job = LoopJob(job_id=job_id, loop_name=loop_name, status="running")
        self._jobs[job_id] = job
        self._event_queues[job_id] = []
        self._emit_event(job_id, "loop_started", payload={"loop_name": loop_name})
        return job_id

    def get_status(self, job_id: str) -> dict[str, Any] | None:
        """Get current state + metrics for a job."""
        job = self._jobs.get(job_id)
        if not job:
            return None
        return {
            "job_id": job.job_id,
            "loop_name": job.loop_name,
            "status": job.status,
            "progress": job.progress,
            "error": job.error,
            "event_count": len(job.events),
        }

    def pause_loop(self, job_id: str) -> bool:
        """Checkpoint and pause a running loop."""
        job = self._jobs.get(job_id)
        if not job or job.status != "running":
            return False
        job.status = "paused"
        self._emit_event(job_id, "loop_paused")
        return True

    def resume_loop(self, job_id: str) -> bool:
        """Resume a paused loop from its last checkpoint."""
        job = self._jobs.get(job_id)
        if not job or job.status != "paused":
            return False
        job.status = "running"
        self._emit_event(job_id, "loop_resumed")
        return True

    def cancel_loop(self, job_id: str) -> bool:
        """Gracefully cancel a running loop."""
        job = self._jobs.get(job_id)
        if not job or job.status in ("completed", "failed", "cancelled"):
            return False
        job.status = "cancelled"
        self._emit_event(job_id, "loop_cancelled")
        return True

    async def subscribe_events(self, job_id: str) -> AsyncIterator[LoopEvent]:
        """Subscribe to real-time events for a job."""
        queue: asyncio.Queue[LoopEvent] = asyncio.Queue(maxsize=500)
        self._event_queues.setdefault(job_id, []).append(queue)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield event
                    if event.event_type in (
                        "completed",
                        "failed",
                        "cancelled",
                        "loop_completed",
                        "loop_failed",
                        "loop_cancelled",
                    ):
                        break
                except TimeoutError:
                    job = self._jobs.get(job_id)
                    if job and job.status in ("completed", "failed", "cancelled"):
                        break
        finally:
            if job_id in self._event_queues:
                with contextlib.suppress(ValueError):
                    self._event_queues[job_id].remove(queue)
                if not self._event_queues[job_id]:
                    del self._event_queues[job_id]

    def _emit_event(
        self,
        job_id: str,
        event_type: str,
        step_index: int = 0,
        metrics: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Emit an event to all subscribers in a thread-safe manner."""
        job = self._jobs.get(job_id)
        if not job:
            return
        event = LoopEvent(
            job_id=job_id,
            event_type=event_type,
            timestamp=time.time(),
            step_index=step_index,
            metrics_snapshot=metrics or {},
            payload=payload or {},
        )
        job.events.append(event)
        for q in list(self._event_queues.get(job_id, [])):
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    loop.call_soon_threadsafe(self._safe_enqueue, q, event)
                else:
                    with contextlib.suppress(asyncio.QueueFull):
                        q.put_nowait(event)
            except RuntimeError:
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(event)

    @staticmethod
    def _safe_enqueue(target_q: asyncio.Queue[LoopEvent], ev: LoopEvent) -> None:
        if not target_q.full():
            target_q.put_nowait(ev)


class MCPServer:
    """MCP server wrapping LoopProtocol as MCP tools.

    Maps Loop Protocol methods to MCP tool definitions.
    Transport-agnostic.
    """

    def __init__(self, protocol: LoopProtocol | None = None) -> None:
        self.protocol = protocol or LoopProtocol()

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return MCP tool definitions for loop operations."""
        return [
            {
                "name": "list_loops",
                "description": "Discover available loop definitions",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "start_loop",
                "description": "Start a loop execution",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "loop_name": {
                            "type": "string",
                            "description": "Name of the loop to run",
                        },
                        "initial_context": {
                            "type": "object",
                            "description": "Initial context data",
                        },
                    },
                    "required": ["loop_name"],
                },
            },
            {
                "name": "get_status",
                "description": "Get state and metrics for a job",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Job ID to check",
                        },
                    },
                    "required": ["job_id"],
                },
            },
            {
                "name": "pause_loop",
                "description": "Checkpoint and pause a running loop",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Job ID to pause",
                        },
                    },
                    "required": ["job_id"],
                },
            },
            {
                "name": "resume_loop",
                "description": "Resume a paused loop",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Job ID to resume",
                        },
                    },
                    "required": ["job_id"],
                },
            },
            {
                "name": "cancel_loop",
                "description": "Gracefully cancel a running loop",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "description": "Job ID to cancel",
                        },
                    },
                    "required": ["job_id"],
                },
            },
        ]

    async def handle_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Handle an MCP tool call."""
        handlers = {
            "list_loops": self._handle_list_loops,
            "start_loop": self._handle_start_loop,
            "get_status": self._handle_get_status,
            "pause_loop": self._handle_simple_loop_op,
            "resume_loop": self._handle_simple_loop_op,
            "cancel_loop": self._handle_simple_loop_op,
        }
        handler = handlers.get(tool_name)
        if handler:
            return handler(tool_name, arguments)
        return {"error": f"Unknown tool: {tool_name}"}

    def _handle_list_loops(self, _tool: str, _args: dict[str, Any]) -> dict[str, Any]:
        return {"loops": self.protocol.list_loops()}

    def _handle_start_loop(self, _tool: str, args: dict[str, Any]) -> dict[str, Any]:
        job_id = self.protocol.start_loop(
            loop_name=args["loop_name"],
            initial_context=args.get("initial_context"),
        )
        return {"job_id": job_id}

    def _handle_get_status(self, _tool: str, args: dict[str, Any]) -> dict[str, Any]:
        status = self.protocol.get_status(args["job_id"])
        return status or {"error": "Job not found"}

    def _handle_simple_loop_op(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        method = getattr(self.protocol, tool)
        success = method(args["job_id"])
        return {"success": success}
