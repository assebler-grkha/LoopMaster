"""Tests for core/replay.py — Deterministic replay."""

from __future__ import annotations

import tempfile
from pathlib import Path

from loopmaster.core.replay import ReplayRunner, ReplaySession, ResponseRecorder
from loopmaster.core.types import StepOutput, StepResult


class TestResponseRecorder:
    def test_record_step(self):
        rec = ResponseRecorder("test_loop", "1.0.0")
        rec.set_initial_context({"query": "test"})
        sr = StepResult(
            step_name="s1",
            success=True,
            output=StepOutput(updates={"result": "data"}),
            tokens_used=100,
            cost=0.01,
        )
        rec.record_step("s1", {"query": "test"}, sr, model="gpt-4o")
        rec.set_final_context({"result": "data"})

        session = rec.session
        assert session.loop_name == "test_loop"
        assert session.loop_version == "1.0.0"
        assert len(session.recorded_steps) == 1
        assert session.recorded_steps[0].step_name == "s1"
        assert session.recorded_steps[0].model == "gpt-4o"


class TestReplaySession:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            session = ReplaySession(
                loop_name="test",
                loop_version="1.0.0",
                initial_context={"q": "test"},
                recorded_steps=[],
                final_context={"r": "done"},
            )
            path = Path(tmpdir) / "session.json"
            session.save(path)
            assert path.exists()

            loaded = ReplaySession.load(path)
            assert loaded.loop_name == "test"
            assert loaded.initial_context == {"q": "test"}
            assert loaded.final_context == {"r": "done"}


class TestReplayRunner:
    def test_get_mock_result(self):
        session = ReplaySession(
            loop_name="test",
            loop_version="1.0.0",
            initial_context={},
            recorded_steps=[],
        )
        runner = ReplayRunner(session)
        assert runner.get_mock_result("nonexistent") is None

    def test_initial_final_context(self):
        session = ReplaySession(
            loop_name="test",
            loop_version="1.0.0",
            initial_context={"x": 1},
            final_context={"x": 10},
        )
        runner = ReplayRunner(session)
        assert runner.initial_context == {"x": 1}
        assert runner.final_context == {"x": 10}

    def test_has_recording(self):
        rec = ResponseRecorder("test", "1.0.0")
        sr = StepResult(step_name="s1", success=True)
        rec.record_step("s1", {}, sr)
        runner = ReplayRunner(rec.session)
        assert runner.has_recording("s1") is True
        assert runner.has_recording("s2") is False

    def test_get_all_recorded_steps(self):
        rec = ResponseRecorder("test", "1.0.0")
        rec.record_step("s1", {}, StepResult(step_name="s1", success=True))
        rec.record_step("s2", {}, StepResult(step_name="s2", success=True))
        runner = ReplayRunner(rec.session)
        steps = runner.get_all_recorded_steps()
        assert len(steps) == 2

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rec = ResponseRecorder("test_loop", "1.0.0")
            rec.set_initial_context({"q": "hello"})
            sr = StepResult(
                step_name="s1",
                success=True,
                output=StepOutput(updates={"answer": "42"}),
                tokens_used=50,
                cost=0.005,
            )
            rec.record_step("s1", {"q": "hello"}, sr)
            rec.set_final_context({"answer": "42"})

            path = Path(tmpdir) / "replay.json"
            rec.save(path)

            loaded = ReplaySession.load(path)
            runner = ReplayRunner(loaded)
            mock = runner.get_mock_result("s1")
            assert mock is not None
            assert mock.success is True
            assert mock.output.updates["answer"] == "42"
            assert mock.tokens_used == 50
