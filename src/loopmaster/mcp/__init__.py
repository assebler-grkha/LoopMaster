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

logger = logging.getLogger(__name__)


@dataclass
class LoopEvent:
    """Event emitted during loop execution."""

    job_id: str
    event_type: str
    timestamp: float
    step_index: int = 0
    metrics_snapshot: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)


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
    """Loop lifecycle protocol — independent of MCP transport."""

    def __init__(self) -> None:
        self._jobs: dict[str, LoopJob] = {}
        self._event_queues: dict[str, list[asyncio.Queue[LoopEvent]]] = {}
        self._engines: dict[str, Any] = {}

    def list_loops(self) -> list[dict[str, Any]]:
        """Discover available loop definitions."""
        loops = []
        for _name, engine in self._engines.items():
            for loop_name, loop_def in engine._registry.items():
                loops.append({
                    "name": loop_name,
                    "version": loop_def.version,
                    "agent": loop_def.agent,
                    "has_budget": loop_def.budget is not None,
                })
        return loops

    def start_loop(
        self,
        loop_name: str,
        initial_context: dict[str, Any] | None = None,
    ) -> str:
        """Start a loop execution. Returns job_id."""
        job_id = uuid.uuid4().hex[:8]
        job = LoopJob(
            job_id=job_id, loop_name=loop_name, status="running"
        )
        self._jobs[job_id] = job
        self._event_queues[job_id] = []
        self._emit_event(
            job_id, "loop_started", payload={"loop_name": loop_name}
        )
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

    async def subscribe_events(
        self, job_id: str
    ) -> AsyncIterator[LoopEvent]:
        """Subscribe to real-time events for a job."""
        queue: asyncio.Queue[LoopEvent] = asyncio.Queue()
        if job_id in self._event_queues:
            self._event_queues[job_id].append(queue)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=1.0
                    )
                    yield event
                    if event.event_type in (
                        "completed", "failed", "cancelled"
                    ):
                        break
                except TimeoutError:
                    job = self._jobs.get(job_id)
                    if job and job.status in (
                        "completed", "failed", "cancelled"
                    ):
                        break
        finally:
            if job_id in self._event_queues:
                with contextlib.suppress(ValueError):
                    self._event_queues[job_id].remove(queue)

    def _emit_event(
        self,
        job_id: str,
        event_type: str,
        step_index: int = 0,
        metrics: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Emit an event to all subscribers."""
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
        for q in self._event_queues.get(job_id, []):
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(event)


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

    async def handle_tool_call(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Handle an MCP tool call."""
        if tool_name == "list_loops":
            return {"loops": self.protocol.list_loops()}
        if tool_name == "start_loop":
            job_id = self.protocol.start_loop(
                loop_name=arguments["loop_name"],
                initial_context=arguments.get("initial_context"),
            )
            return {"job_id": job_id}
        if tool_name == "get_status":
            status = self.protocol.get_status(arguments["job_id"])
            return status or {"error": "Job not found"}
        if tool_name == "pause_loop":
            success = self.protocol.pause_loop(arguments["job_id"])
            return {"success": success}
        if tool_name == "resume_loop":
            success = self.protocol.resume_loop(arguments["job_id"])
            return {"success": success}
        if tool_name == "cancel_loop":
            success = self.protocol.cancel_loop(arguments["job_id"])
            return {"success": success}
        return {"error": f"Unknown tool: {tool_name}"}
