"""Tests for mcp/__init__.py — LoopProtocol, MCPServer."""

from __future__ import annotations

import asyncio

from loopmaster.mcp import LoopEvent, LoopJob, LoopProtocol, MCPServer


class TestLoopEvent:
    def test_creation(self):
        e = LoopEvent(job_id="j1", event_type="step_started", timestamp=1.0)
        assert e.job_id == "j1"
        assert e.event_type == "step_started"


class TestLoopJob:
    def test_defaults(self):
        j = LoopJob(job_id="j1", loop_name="test")
        assert j.status == "pending"
        assert j.progress == 0.0
        assert j.error is None


class TestLoopProtocol:
    def test_start_loop(self):
        proto = LoopProtocol()
        job_id = proto.start_loop("myloop")
        assert job_id is not None
        status = proto.get_status(job_id)
        assert status["status"] == "running"

    def test_get_status_not_found(self):
        proto = LoopProtocol()
        assert proto.get_status("nonexistent") is None

    def test_pause_loop(self):
        proto = LoopProtocol()
        job_id = proto.start_loop("test")
        assert proto.pause_loop(job_id) is True
        status = proto.get_status(job_id)
        assert status["status"] == "paused"

    def test_pause_non_running(self):
        proto = LoopProtocol()
        job_id = proto.start_loop("test")
        proto.pause_loop(job_id)
        assert proto.pause_loop(job_id) is False

    def test_resume_loop(self):
        proto = LoopProtocol()
        job_id = proto.start_loop("test")
        proto.pause_loop(job_id)
        assert proto.resume_loop(job_id) is True
        status = proto.get_status(job_id)
        assert status["status"] == "running"

    def test_resume_non_paused(self):
        proto = LoopProtocol()
        job_id = proto.start_loop("test")
        assert proto.resume_loop(job_id) is False

    def test_cancel_loop(self):
        proto = LoopProtocol()
        job_id = proto.start_loop("test")
        assert proto.cancel_loop(job_id) is True
        status = proto.get_status(job_id)
        assert status["status"] == "cancelled"

    def test_cancel_completed(self):
        proto = LoopProtocol()
        job_id = proto.start_loop("test")
        proto.cancel_loop(job_id)
        assert proto.cancel_loop(job_id) is False

    def test_list_loops_empty(self):
        proto = LoopProtocol()
        assert proto.list_loops() == []


class TestMCPServer:
    def test_tool_definitions(self):
        server = MCPServer()
        defs = server.get_tool_definitions()
        names = [d["name"] for d in defs]
        assert "list_loops" in names
        assert "start_loop" in names
        assert "get_status" in names
        assert "pause_loop" in names
        assert "resume_loop" in names
        assert "cancel_loop" in names

    def test_handle_start_loop(self):
        server = MCPServer()
        result = asyncio.get_event_loop().run_until_complete(
            server.handle_tool_call("start_loop", {"loop_name": "test"})
        )
        assert "job_id" in result

    def test_handle_get_status(self):
        server = MCPServer()
        start_result = asyncio.get_event_loop().run_until_complete(
            server.handle_tool_call("start_loop", {"loop_name": "test"})
        )
        job_id = start_result["job_id"]
        result = asyncio.get_event_loop().run_until_complete(
            server.handle_tool_call("get_status", {"job_id": job_id})
        )
        assert result["status"] == "running"

    def test_handle_unknown_tool(self):
        server = MCPServer()
        result = asyncio.get_event_loop().run_until_complete(
            server.handle_tool_call("unknown_tool", {})
        )
        assert "error" in result

    def test_handle_cancel_loop(self):
        server = MCPServer()
        start_result = asyncio.get_event_loop().run_until_complete(
            server.handle_tool_call("start_loop", {"loop_name": "test"})
        )
        job_id = start_result["job_id"]
        result = asyncio.get_event_loop().run_until_complete(
            server.handle_tool_call("cancel_loop", {"job_id": job_id})
        )
        assert result["success"] is True
