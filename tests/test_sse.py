"""Tests for SSE formatting and SSEStream iterator."""

from __future__ import annotations

import asyncio

import pytest

from loopmaster.events import EventEmitter, LoopEvent, SSEStream, format_sse


class TestSSEFormatting:
    def test_format_sse_basic(self):
        msg = format_sse("Hello World")
        assert msg == "data: Hello World\n\n"

    def test_format_sse_with_event_and_id(self):
        msg = format_sse("Chunk", event_type="step_chunk", event_id="123", retry_ms=3000)
        assert "event: step_chunk\n" in msg
        assert "id: 123\n" in msg
        assert "retry: 3000\n" in msg
        assert "data: Chunk\n\n" in msg

    def test_format_sse_multiline_text(self):
        multiline = "line1\nline2\nline3"
        msg = format_sse(multiline, event_type="log")
        assert msg == "event: log\ndata: line1\ndata: line2\ndata: line3\n\n"

    def test_format_sse_json_dict(self):
        data = {"status": "ok", "count": 42}
        msg = format_sse(data, event_type="status")
        assert "event: status\n" in msg
        assert 'data: {"status": "ok", "count": 42}' in msg
        assert msg.endswith("\n\n")


class TestSSEStream:
    @pytest.mark.asyncio
    async def test_async_sse_stream_yields_events(self):
        emitter = EventEmitter()
        stream = SSEStream(emitter, job_id="job-1")

        received_events = []

        async def consumer():
            async for sse_msg in stream:
                received_events.append(sse_msg)

        task = asyncio.create_task(consumer())

        # Give consumer a moment to subscribe
        await asyncio.sleep(0.01)

        emitter.emit("job-1", "step_started", step_index=0, payload={"step_name": "s1"})
        emitter.emit("job-1", "step_chunk", step_index=0, payload={"delta": "Hi"})
        emitter.emit("job-2", "step_started", step_index=0, payload={"step_name": "s2"})  # filtered out
        emitter.emit("job-1", "loop_completed", step_index=1, payload={"status": "done"})  # terminal

        await asyncio.wait_for(task, timeout=2.0)

        assert len(received_events) == 3
        assert "event: step_started" in received_events[0]
        assert "event: step_chunk" in received_events[1]
        assert "event: loop_completed" in received_events[2]

    def test_sync_sse_stream(self):
        import threading
        import time

        emitter = EventEmitter()
        stream = SSEStream(emitter, job_id="job-sync")

        received = []

        def producer():
            time.sleep(0.02)
            emitter.emit("job-sync", "step_started", step_index=0, payload={"step_name": "init"})
            emitter.emit("job-sync", "loop_completed", step_index=1, payload={"ok": True})

        t = threading.Thread(target=producer)
        t.start()

        for msg in stream:
            received.append(msg)

        t.join()

        assert len(received) == 2
        assert "event: step_started" in received[0]
        assert "event: loop_completed" in received[1]
