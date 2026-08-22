"""Tests for checkpoint/__init__.py — CheckpointManager."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from loopmaster.checkpoint import CheckpointManager
from loopmaster.core.exceptions import CheckpointError
from loopmaster.core.types import CheckpointData


def _make_checkpoint(name: str = "test", step: int = 0) -> CheckpointData:
    return CheckpointData(
        loop_name=name,
        loop_version="1.0.0",
        loop_source_hash="abc123",
        step_index=step,
        context_data={"key": "value"},
        completed_results={},
        executed_step_names=[f"s{i}" for i in range(step)],
    )


class TestCheckpointManager:
    def test_save_and_load_latest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir)
            cp = _make_checkpoint("myloop", 3)
            path = mgr.save(cp)
            assert path.exists()

            loaded = mgr.load_latest("myloop")
            assert loaded is not None
            assert loaded.loop_name == "myloop"
            assert loaded.loop_version == "1.0.0"
            assert loaded.step_index == 3
            assert loaded.context_data == {"key": "value"}

    def test_load_latest_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir)
            assert mgr.load_latest("nonexistent") is None

    def test_load_specific_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir)
            cp = _make_checkpoint("loop_a")
            path = mgr.save(cp)
            loaded = mgr.load(path)
            assert loaded.loop_name == "loop_a"

    def test_load_nonexistent_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir)
            with pytest.raises(CheckpointError, match="not found"):
                mgr.load(Path("/nonexistent/file.json"))

    def test_list_checkpoints(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir)
            mgr.save(_make_checkpoint("loop1", 1))
            mgr.save(_make_checkpoint("loop1", 2))
            mgr.save(_make_checkpoint("loop2", 1))

            all_cps = mgr.list_checkpoints()
            assert len(all_cps) == 3

            filtered = mgr.list_checkpoints("loop1")
            assert len(filtered) == 2

    def test_delete(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir)
            mgr.save(_make_checkpoint("loop1", 1))
            mgr.save(_make_checkpoint("loop1", 2))
            mgr.save(_make_checkpoint("loop2"))

            count = mgr.delete("loop1")
            assert count == 3
            assert mgr.load_latest("loop1") is None
            assert mgr.load_latest("loop2") is not None

    def test_latest_symlink(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir)
            mgr.save(_make_checkpoint("test", 1))
            mgr.save(_make_checkpoint("test", 2))
            mgr.save(_make_checkpoint("test", 3))

            loaded = mgr.load_latest("test")
            assert loaded.step_index == 3  # Latest

    def test_format_version_in_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir)
            path = mgr.save(_make_checkpoint())
            data = json.loads(path.read_text())
            assert data["_format_version"] == 1
