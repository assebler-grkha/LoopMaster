"""Tests for the CLI commands."""

from __future__ import annotations

import textwrap
from pathlib import Path

from typer.testing import CliRunner

from loopmaster.cli.app import app

runner = CliRunner()


class TestCLIInit:
    """Tests for the init command."""

    def test_init_creates_default_template(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", "my_loop", "--path", str(tmp_path)])
        assert result.exit_code == 0
        assert "Created:" in result.output
        loop_file = tmp_path / "my_loop.py"
        assert loop_file.exists()
        content = loop_file.read_text(encoding="utf-8")
        assert '@Loop(name="my_loop"' in content
        assert "Step(" in content

    def test_init_with_template(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "init",
                "refl",
                "--path",
                str(tmp_path),
                "--template",
                "reflection",
                "--task",
                "test task",
            ],
        )
        assert result.exit_code == 0
        loop_file = tmp_path / "refl.py"
        assert loop_file.exists()
        content = loop_file.read_text(encoding="utf-8")
        assert "test task" in content
        assert "evaluate" in content

    def test_init_unknown_template(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["init", "x", "--path", str(tmp_path), "--template", "nope"])
        assert result.exit_code == 1
        assert "Unknown template" in result.output

    def test_init_existing_file(self, tmp_path: Path) -> None:
        (tmp_path / "exists.py").write_text("# old", encoding="utf-8")
        result = runner.invoke(app, ["init", "exists", "--path", str(tmp_path)])
        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_init_tool_use_template(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            [
                "init",
                "tool",
                "--path",
                str(tmp_path),
                "--template",
                "tool_use",
                "--task",
                "search docs",
            ],
        )
        assert result.exit_code == 0
        content = (tmp_path / "tool.py").read_text(encoding="utf-8")
        assert "search docs" in content
        assert "call_tool" in content

    def test_init_all_templates(self, tmp_path: Path) -> None:
        from loopmaster.templates import TEMPLATES

        for tpl_name in TEMPLATES:
            sub = tmp_path / tpl_name
            sub.mkdir()
            result = runner.invoke(
                app, ["init", "loop", "--path", str(sub), "--template", tpl_name]
            )
            assert result.exit_code == 0, f"Template {tpl_name} failed: {result.output}"


class TestCLIValidate:
    """Tests for the validate command."""

    def test_validate_valid_loop(self, tmp_path: Path) -> None:
        loop_code = textwrap.dedent('''\
            """Test loop."""
            from loopmaster import Loop, Step

            @Loop(name="test", version="0.1.0")
            def test_loop(ctx):
                Step("step1", model="gpt-4", prompt="hello")
                return ctx
        ''')
        loop_file = tmp_path / "test_loop.py"
        loop_file.write_text(loop_code, encoding="utf-8")
        result = runner.invoke(app, ["validate", str(loop_file)])
        assert result.exit_code == 0
        assert "Valid:" in result.output

    def test_validate_no_loop(self, tmp_path: Path) -> None:
        loop_file = tmp_path / "noloop.py"
        loop_file.write_text("# nothing here", encoding="utf-8")
        result = runner.invoke(app, ["validate", str(loop_file)])
        assert result.exit_code == 1
        assert "No @Loop found" in result.output

    def test_validate_bad_file(self) -> None:
        result = runner.invoke(app, ["validate", "/nonexistent/file.py"])
        assert result.exit_code == 1
        assert "Error:" in result.output


class TestCLIRun:
    """Tests for the run command."""

    def test_run_dry_run(self, tmp_path: Path) -> None:
        loop_code = textwrap.dedent('''\
            """Test loop."""
            from loopmaster import Loop, Step

            @Loop(name="test", version="0.1.0")
            def test_loop(ctx):
                Step("step1", model="gpt-4", prompt="hello")
                return ctx
        ''')
        loop_file = tmp_path / "test_loop.py"
        loop_file.write_text(loop_code, encoding="utf-8")
        result = runner.invoke(app, ["run", str(loop_file), "--dry-run"])
        assert result.exit_code == 0
        assert "Dry run:" in result.output

    def test_run_no_loop_file(self) -> None:
        result = runner.invoke(app, ["run", "/nonexistent/file.py"])
        assert result.exit_code == 1
        assert "Error:" in result.output

    def test_run_no_loop_in_file(self, tmp_path: Path) -> None:
        loop_file = tmp_path / "empty.py"
        loop_file.write_text("# nothing", encoding="utf-8")
        result = runner.invoke(app, ["run", str(loop_file)])
        assert result.exit_code == 1
        assert "No @Loop found" in result.output


class TestCLITemplates:
    """Tests for the templates command."""

    def test_templates_list(self) -> None:
        result = runner.invoke(app, ["templates"])
        assert result.exit_code == 0
        assert "Available Templates" in result.output
        assert "reflection" in result.output
        assert "tool_use" in result.output
        assert "planning" in result.output


class TestCLICheckpoints:
    """Tests for the checkpoints command."""

    def test_checkpoints_empty(self) -> None:
        result = runner.invoke(app, ["checkpoints", "nonexistent_loop"])
        assert result.exit_code == 0
        assert "No checkpoints found" in result.output
