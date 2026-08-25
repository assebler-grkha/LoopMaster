"""Tests for the `loop-engine block` sub-app."""

import pytest
from typer.testing import CliRunner

from loopmaster.cli.app import app

runner = CliRunner()


@pytest.fixture()
def store_env(tmp_path, monkeypatch):
    db = tmp_path / "jobs.db"
    monkeypatch.setenv("LOOPMASTER_JOBS_DB", str(db))
    monkeypatch.setattr("loopmaster.mcp.job_store._global_store", None)
    yield store_env


SRC = "print('block-ok')\n"


def test_block_add_get_list_roundtrip(store_env, tmp_path):
    src_file = tmp_path / "b.py"
    src_file.write_text(SRC, encoding="utf-8")

    res = runner.invoke(
        app,
        [
            "block",
            "add",
            "my-block",
            "1.0.0",
            "--lang",
            "python",
            "--source",
            str(src_file),
            "--caps",
            "net",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "Added" in res.output

    res = runner.invoke(app, ["block", "get", "my-block@1.0.0"])
    assert res.exit_code == 0, res.output
    assert "block-ok" in res.output
    assert '"verified_sha256"' in res.output

    res = runner.invoke(app, ["block", "list"])
    assert res.exit_code == 0
    assert "my-block@1.0.0" in res.output


def test_block_add_duplicate_fails(store_env, tmp_path):
    src_file = tmp_path / "b.py"
    src_file.write_text(SRC, encoding="utf-8")
    args = ["block", "add", "dup", "1.0.0", "--source", str(src_file)]
    assert runner.invoke(app, args).exit_code == 0
    res = runner.invoke(app, args)
    assert res.exit_code == 1
    assert "already exists" in res.output


def test_block_add_missing_source(store_env):
    res = runner.invoke(app, ["block", "add", "x", "1.0.0", "--source", "Z:/no/pe.py"])
    assert res.exit_code == 1
    assert "Cannot read" in res.output


def test_block_get_not_found(store_env):
    res = runner.invoke(app, ["block", "get", "ghost@9.9.9"])
    assert res.exit_code == 1
