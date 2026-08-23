"""Tests for Tool Execution Bridge (ShellExecutor, HTTPExecutor, MCPToolExecutor)."""

from __future__ import annotations

import json
import sys
import urllib.error
from unittest.mock import MagicMock, patch

from loopmaster import (
    HTTPExecutor,
    HTTPResult,
    Loop,
    LoopEngine,
    MCPToolExecutor,
    MCPToolResult,
    ShellExecutor,
    ShellResult,
    Step,
)
from loopmaster.core.types import resolve_prompt
from loopmaster.executors import resolve_path_value, resolve_template_value
from loopmaster.telemetry import (
    InMemorySpanExporter,
    SpanKind,
    TelemetryProvider,
    reset_telemetry,
    set_global_provider,
)


class TestTemplateResolution:
    def test_resolve_nested_dot_paths(self):
        ctx = {
            "shell_step": ShellResult(returncode=0, stdout="compiled ok", stderr="", success=True),
            "http_step": {"status": 200, "data": {"user": {"id": 123, "name": "Alice"}}},
            "simple": "world",
        }

        assert resolve_path_value("shell_step.stdout", ctx) == "compiled ok"
        assert resolve_path_value("http_step.data.user.name", ctx) == "Alice"
        assert resolve_path_value("simple", ctx) == "world"
        assert resolve_path_value("nonexistent.field", ctx) is None

    def test_resolve_template_values_in_structures(self):
        ctx = {"host": "api.example.com", "port": 8080, "path": "v1/users"}
        url_template = "https://{host}:{port}/{path}"
        assert resolve_template_value(url_template, ctx) == "https://api.example.com:8080/v1/users"

        payload = {"url": "{host}", "items": ["{port}", 123]}
        resolved = resolve_template_value(payload, ctx)
        assert resolved == {"url": "api.example.com", "items": [8080, 123]}

    def test_resolve_prompt_with_dot_paths(self):
        ctx = {
            "run_tests": ShellResult(returncode=0, stdout="5 passed", stderr="", success=True),
        }
        res = resolve_prompt("Analyze test results: {run_tests.stdout}", ctx)
        assert res == "Analyze test results: 5 passed"


class TestShellExecutor:
    def test_execute_python_command(self):
        code = "import sys; sys.stdout.write('hello from subprocess')"
        cmd = [sys.executable, "-c", code]
        executor = ShellExecutor(command=cmd, timeout=10.0)

        result = executor.execute({})
        assert isinstance(result, ShellResult)
        assert result.success is True
        assert result.returncode == 0
        assert result.stdout == "hello from subprocess"
        assert result["stdout"] == "hello from subprocess"
        assert result.get("returncode") == 0

    def test_template_substitution_in_shell_command(self):
        code = "import sys; sys.stdout.write('target=' + '{target_name}')"
        cmd = [sys.executable, "-c", code]
        executor = ShellExecutor(command=cmd)

        result = executor.execute({"target_name": "production_cluster"})
        assert result.success is True
        assert result.stdout == "target=production_cluster"

    def test_failed_command_non_zero_exit(self):
        code = "import sys; sys.stderr.write('fatal syntax error'); sys.exit(2)"
        cmd = [sys.executable, "-c", code]
        executor = ShellExecutor(command=cmd)

        result = executor.execute({})
        assert result.success is False
        assert result.returncode == 2
        assert "fatal syntax error" in result.stderr
        assert result.error == "fatal syntax error"

    def test_timeout_handling(self):
        code = "import time; time.sleep(5.0)"
        cmd = [sys.executable, "-c", code]
        executor = ShellExecutor(command=cmd, timeout=0.1)

        result = executor.execute({})
        assert result.success is False
        assert result.returncode == -1
        assert "timed out" in (result.error or "").lower()


class TestHTTPExecutor:
    def test_successful_get_request(self):
        executor = HTTPExecutor(
            url="https://api.github.com/repos/test",
            method="GET",
            headers={"User-Agent": "LoopMaster"},
        )

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"name": "LoopMaster", "stars": 100}'
        mock_resp.headers.items.return_value = [("Content-Type", "application/json")]
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            result = executor.execute({})
            assert mock_urlopen.called
            assert isinstance(result, HTTPResult)
            assert result.success is True
            assert result.status_code == 200
            assert result.body == {"name": "LoopMaster", "stars": 100}
            assert result["status_code"] == 200

    def test_http_error_body_capture(self):
        executor = HTTPExecutor(url="https://api.example.com/data")

        http_err = urllib.error.HTTPError(
            url="https://api.example.com/data",
            code=404,
            msg="Not Found",
            hdrs={},  # type: ignore[arg-type]
            fp=MagicMock(read=lambda: b'{"error": "resource not found"}'),
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            result = executor.execute({})
            assert result.success is False
            assert result.status_code == 404
            assert result.body == {"error": "resource not found"}
            assert "HTTP 404" in (result.error or "")

    def test_204_no_content_empty_body(self):
        executor = HTTPExecutor(url="https://api.example.com/delete", method="DELETE")

        mock_resp = MagicMock()
        mock_resp.status = 204
        mock_resp.read.return_value = b""
        mock_resp.headers.items.return_value = []
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = executor.execute({})
            assert result.success is True
            assert result.status_code == 204
            assert result.body == {}


class TestMCPToolExecutor:
    def test_successful_mcp_call_handshake(self):
        executor = MCPToolExecutor(
            server_command=["python", "fake_server.py"],
            tool_name="calculate_sum",
            arguments={"a": 10, "b": 20},
        )

        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()

        # Responses for: 1. initialize, 2. tools/call
        init_resp = (
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}})
            + "\n"
        )
        call_resp = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "content": [{"type": "text", "text": "Result is 30"}],
                        "isError": False,
                    },
                }
            )
            + "\n"
        )

        mock_proc.stdout.readline.side_effect = [init_resp, call_resp]

        with patch("subprocess.Popen", return_value=mock_proc):
            result = executor.execute({})
            assert isinstance(result, MCPToolResult)
            assert result.success is True
            assert result.text == "Result is 30"
            assert result["text"] == "Result is 30"
            assert len(result.content) == 1


class TestLoopEngineToolIntegration:
    def test_engine_executes_tool_steps_and_emits_telemetry(self):
        reset_telemetry()
        exporter = InMemorySpanExporter()
        provider = TelemetryProvider(exporter=exporter)
        set_global_provider(provider)

        @Loop(name="tool_pipeline")
        def pipeline(ctx):
            Step(
                "build_project",
                executor=ShellExecutor(
                    command=[sys.executable, "-c", "import sys; sys.stdout.write('BUILD_SUCCESS')"]
                ),
            )
            Step(
                "format_notification",
                prompt="Status of build was: {build_project.stdout}",
            )

        engine = LoopEngine()
        result = engine.run(pipeline)

        assert result.success is True
        assert "build_project" in result.results
        assert result.results["build_project"].success is True
        assert result.results["build_project"].output.stdout == "BUILD_SUCCESS"

        # Check telemetry spans
        spans = exporter.get_finished_spans()
        assert any(s.name == "tool.shell" and s.kind == SpanKind.CLIENT for s in spans)
        shell_span = next(s for s in spans if s.name == "tool.shell")
        assert shell_span.attributes["process.exit_code"] == 0
        reset_telemetry()

    def test_http_allowed_status_and_mcp_text_context(self):
        executor = HTTPExecutor(
            url="https://api.example.com/check",
            allowed_status=[200, 404],
        )

        http_err = urllib.error.HTTPError(
            url="https://api.example.com/check",
            code=404,
            msg="Not Found",
            hdrs={},  # type: ignore[arg-type]
            fp=MagicMock(read=lambda: b'{"status": "not found but expected"}'),
        )

        with patch("urllib.request.urlopen", side_effect=http_err):
            result = executor.execute({})
            assert result.status_code == 404
            assert result.success is True
            assert result.error is None

        @Loop(name="mcp_pipeline")
        def mcp_pipe(ctx):
            Step(
                "run_mcp",
                executor=MCPToolExecutor(server_command="fake", tool_name="echo_tool"),
            )
            Step("format_out", prompt="Extracted: {run_mcp.text}")

        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        init_resp = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}}) + "\n"
        call_resp = (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "result": {
                        "content": [{"type": "text", "text": "MCP_OK"}],
                        "isError": False,
                    },
                }
            )
            + "\n"
        )
        mock_proc.stdout.readline.side_effect = [init_resp, call_resp]

        engine = LoopEngine()
        with patch("subprocess.Popen", return_value=mock_proc):
            run_res = engine.run(mcp_pipe)

        assert run_res.success is True
        assert run_res.results["format_out"].output == "Extracted: MCP_OK"
