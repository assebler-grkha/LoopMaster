"""Tests for LoopStore CRUD on JobStore (loops table)."""

from __future__ import annotations

import pytest

from loopmaster.mcp.job_store import get_job_store

SPEC = {
    "loopmaster": "1.0",
    "name": "store-loop",
    "version": "1.0.0",
    "steps": [{"type": "llm", "name": "only", "prompt": "hello"}],
}


@pytest.fixture()
def store(tmp_path):
    return get_job_store(str(tmp_path / "jobs.db"))


class TestLoopCRUD:
    def test_save_and_get(self, store):
        saved = store.save_loop("store-loop", "1.0.0", SPEC, source_hash="abc")
        assert saved.name == "store-loop"
        assert saved.version == "1.0.0"
        assert saved.spec == SPEC
        assert saved.source_hash == "abc"

    def test_get_missing_returns_none(self, store):
        assert store.get_loop("nope") is None

    def test_upsert_updates_version_and_keeps_created_at(self, store):
        first = store.save_loop("store-loop", "1.0.0", SPEC)
        updated = store.save_loop("store-loop", "1.1.0", {**SPEC, "version": "1.1.0"})
        assert updated.version == "1.1.0"
        assert updated.created_at == first.created_at
        assert store.get_loop("store-loop").spec["version"] == "1.1.0"

    def test_list_loops_newest_first(self, store):
        store.save_loop("aaa", "1.0.0", SPEC)
        store.save_loop("bbb", "1.0.0", SPEC)
        names = [loop.name for loop in store.list_loops()]
        assert set(names) == {"aaa", "bbb"}

    def test_list_limit(self, store):
        for name in ("l1", "l2", "l3"):
            store.save_loop(name, "1.0.0", SPEC)
        assert len(store.list_loops(limit=2)) == 2

    def test_delete_existing(self, store):
        store.save_loop("store-loop", "1.0.0", SPEC)
        assert store.delete_loop("store-loop") is True
        assert store.get_loop("store-loop") is None

    def test_delete_missing_returns_false(self, store):
        assert store.delete_loop("ghost") is False

    def test_to_dict_roundtrip(self, store):
        store.save_loop("store-loop", "2.0.0", SPEC, source_hash="h" * 64)
        data = store.get_loop("store-loop").to_dict()
        assert data["name"] == "store-loop"
        assert data["version"] == "2.0.0"
        assert data["source_hash"].startswith("hhhh")

    def test_persistence_across_instances(self, tmp_path):
        db = str(tmp_path / "jobs.db")
        get_job_store(db).save_loop("persist-me", "1.0.0", SPEC, source_hash="ph")
        other = get_job_store(db)
        loaded = other.get_loop("persist-me")
        assert loaded is not None
        assert loaded.name == "persist-me"
        assert loaded.version == "1.0.0"
