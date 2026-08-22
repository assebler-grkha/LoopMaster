import time

from loopmaster.events import EventEmitter, LoopEvent


class TestLoopEvent:
    def test_creation(self):
        ts = time.time()
        event = LoopEvent(job_id="j1", event_type="step_complete", timestamp=ts)
        assert event.job_id == "j1"
        assert event.event_type == "step_complete"
        assert event.step_index == 0
        assert event.metrics_snapshot == {}
        assert event.payload == {}

    def test_with_all_fields(self):
        event = LoopEvent(
            job_id="j1",
            event_type="step_complete",
            timestamp=12345.0,
            step_index=2,
            metrics_snapshot={"tokens": 100},
            payload={"result": "ok"},
        )
        assert event.step_index == 2
        assert event.metrics_snapshot["tokens"] == 100
        assert event.payload["result"] == "ok"
        assert event.timestamp == 12345.0


class TestEventEmitter:
    def test_emit_and_listen(self):
        emitter = EventEmitter()
        received = []
        emitter.on("step_complete", lambda e: received.append(e))
        event = emitter.emit("j1", "step_complete")
        assert len(received) == 1
        assert received[0] is event

    def test_emit_returns_event(self):
        emitter = EventEmitter()
        event = emitter.emit("j1", "start", step_index=1, metrics={"t": 1}, payload={"k": "v"})
        assert event.job_id == "j1"
        assert event.event_type == "start"
        assert event.step_index == 1
        assert event.metrics_snapshot == {"t": 1}
        assert event.payload == {"k": "v"}

    def test_multiple_listeners(self):
        emitter = EventEmitter()
        results = {"a": 0, "b": 0}
        emitter.on("step", lambda e: results.__setitem__("a", results["a"] + 1))
        emitter.on("step", lambda e: results.__setitem__("b", results["b"] + 1))
        emitter.emit("j1", "step")
        assert results["a"] == 1
        assert results["b"] == 1

    def test_wildcard_listener(self):
        emitter = EventEmitter()
        received = []
        emitter.on("*", lambda e: received.append(e))
        emitter.emit("j1", "step")
        emitter.emit("j1", "complete")
        assert len(received) == 2

    def test_off_removes_listener(self):
        emitter = EventEmitter()
        received = []

        def handler(e):
            received.append(e)

        emitter.on("step", handler)
        emitter.emit("j1", "step")
        assert len(received) == 1
        emitter.off("step", handler)
        emitter.emit("j1", "step")
        assert len(received) == 1

    def test_off_nonexistent_handler_no_error(self):
        emitter = EventEmitter()
        emitter.off("step", lambda e: None)

    def test_emit_unknown_event_no_error(self):
        emitter = EventEmitter()
        emitter.emit("j1", "unknown")

    def test_history(self):
        emitter = EventEmitter()
        e1 = emitter.emit("j1", "start")
        e2 = emitter.emit("j1", "end")
        assert len(emitter.history) == 2
        assert emitter.history[0] is e1
        assert emitter.history[1] is e2

    def test_clear_history(self):
        emitter = EventEmitter()
        emitter.emit("j1", "start")
        assert len(emitter.history) == 1
        emitter.clear()
        assert len(emitter.history) == 0

    def test_handler_error_does_not_break_emit(self):
        emitter = EventEmitter()

        def bad_handler(e):
            raise ValueError("oops")

        emitter.on("step", bad_handler)
        received = []
        emitter.on("step", lambda e: received.append(e))
        emitter.emit("j1", "step")
        assert len(received) == 1

    def test_listener_ordering(self):
        emitter = EventEmitter()
        order = []
        emitter.on("step", lambda e: order.append("first"))
        emitter.on("step", lambda e: order.append("second"))
        emitter.emit("j1", "step")
        assert order == ["first", "second"]

    def test_multiple_event_types(self):
        emitter = EventEmitter()
        starts = []
        ends = []
        emitter.on("start", lambda e: starts.append(e))
        emitter.on("end", lambda e: ends.append(e))
        emitter.emit("j1", "start")
        emitter.emit("j1", "end")
        emitter.emit("j1", "start")
        assert len(starts) == 2
        assert len(ends) == 1
