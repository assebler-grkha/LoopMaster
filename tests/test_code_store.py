"""Tests for the code_blocks store (Phase 3: CodeBlockStore)."""

from __future__ import annotations

import hashlib

import pytest

from loopmaster.mcp.job_store import JobStore, _split_block_ref

BLOCK_SRC = "import json, sys\njson.dump({'ok': True, 'output': {}}, sys.stdout)\n"


@pytest.fixture()
def store(tmp_path):
    return JobStore(db_path=tmp_path / "jobs.db")


class TestSaveAndGet:
    def test_roundtrip_with_sha256(self, store):
        block = store.save_code_block(
            "test-fixer",
            "1.0.0",
            "python",
            BLOCK_SRC,
            capabilities=["net"],
            description="fixes tests",
        )
        assert block.sha256 == hashlib.sha256(BLOCK_SRC.encode()).hexdigest()

        got = store.get_code_block("test-fixer@1.0.0")
        assert got is not None
        assert got.source == BLOCK_SRC
        assert got.capabilities == ["net"]
        assert got.description == "fixes tests"
        assert got.entrypoint == "main"

    def test_immutability_same_version(self, store):
        store.save_code_block("blk", "1.2.3", "python", BLOCK_SRC)
        with pytest.raises(ValueError, match="already exists"):
            store.save_code_block("blk", "1.2.3", "shell", "# other")

    def test_multiple_versions_allowed(self, store):
        store.save_code_block("blk", "1.0.0", "python", BLOCK_SRC)
        store.save_code_block("blk", "2.0.0", "python", BLOCK_SRC)
        assert store.get_code_block("blk@1.0.0") is not None
        assert store.get_code_block("blk@2.0.0") is not None

    @pytest.mark.parametrize(
        ("name", "version", "lang"),
        [("Bad_Name", "1.0.0", "python"), ("ok", "abc", "python"), ("ok", "1.0.0", "ruby")],
    )
    def test_validation_errors(self, store, name, version, lang):
        with pytest.raises(ValueError):
            store.save_code_block(name, version, lang, BLOCK_SRC)

    def test_invalid_capability(self, store):
        with pytest.raises(ValueError, match="capability"):
            store.save_code_block("blk", "1.0.0", "python", BLOCK_SRC, capabilities=["sudo"])

    def test_get_latest_when_no_version(self, store):
        store.save_code_block("blk", "1.0.0", "python", BLOCK_SRC)
        store.save_code_block("blk", "1.1.0", "python", BLOCK_SRC)
        assert store.get_code_block("blk").version == "1.1.0"

    def test_missing_returns_none(self, store):
        assert store.get_code_block("ghost@9.9.9") is None


class TestListDeleteVerify:
    def test_list_excludes_source(self, store):
        store.save_code_block("alpha", "1.0.0", "python", BLOCK_SRC)
        blocks = store.list_code_blocks()
        assert len(blocks) == 1
        assert blocks[0].source == ""
        assert blocks[0].sha256

    def test_list_pattern(self, store):
        store.save_code_block("alpha", "1.0.0", "python", BLOCK_SRC)
        store.save_code_block("beta", "1.0.0", "shell", "echo hi")
        names = [b.name for b in store.list_code_blocks(pattern="alp")]
        assert names == ["alpha"]

    def test_delete(self, store):
        store.save_code_block("blk", "1.0.0", "python", BLOCK_SRC)
        assert store.delete_code_block("blk", "1.0.0") is True
        assert store.delete_code_block("blk", "1.0.0") is False

    def test_verify_ok_and_tamper(self, store):
        store.save_code_block("blk", "1.0.0", "python", BLOCK_SRC)
        assert store.verify_code_block("blk@1.0.0") is True
        row = store.conn.execute("SELECT id FROM code_blocks WHERE name='blk'").fetchone()
        store.conn.execute("UPDATE code_blocks SET source = 'tampered' WHERE id = ?", (row["id"],))
        store.conn.commit()
        assert store.verify_code_block("blk@1.0.0") is False


class TestRefParsing:
    @pytest.mark.parametrize(
        ("ref", "expected"),
        [
            ("test-fixer@1.4.2", ("test-fixer", "1.4.2")),
            ("plain", ("plain", None)),
        ],
    )
    def test_split(self, ref, expected):
        assert _split_block_ref(ref) == expected
