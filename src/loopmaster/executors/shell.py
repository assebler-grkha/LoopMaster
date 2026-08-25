"""Subprocess shell execution tool with timeout management and OTel tracing."""

from __future__ import annotations

import contextlib
import logging
import os
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import BaseExecutor, build_minimal_env, resolve_template_value

logger = logging.getLogger("loopmaster.executors.shell")


@dataclass
class ShellResult:
    """Structured result of a shell process execution."""

    returncode: int
    stdout: str
    stderr: str
    success: bool
    error: str | None = None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def to_dict(self) -> dict[str, Any]:
        return {
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "success": self.success,
            "error": self.error,
        }


def _kill_process_tree(proc: subprocess.Popen[Any]) -> None:
    """Terminate the process and any spawned child processes across platforms."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            pgid = os.getpgid(proc.pid)
            if pgid != os.getpgrp():
                os.killpg(pgid, signal.SIGKILL)
            else:
                proc.kill()
    except Exception as exc:
        logger.debug("Failed to kill process tree for PID %s: %s", proc.pid, exc)
        with contextlib.suppress(Exception):
            proc.kill()


class ShellExecutor(BaseExecutor):
    """Executes a system shell command with templating, timeout, and tracing."""

    def __init__(
        self,
        command: str | list[str],
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 60.0,
        capture_output: bool = True,
        check: bool = False,
        shell: bool = False,
        env_inherit: bool = False,
    ) -> None:
        self.command = command
        self.cwd = str(cwd) if cwd is not None else None
        self.env = env
        self.timeout = timeout
        self.capture_output = capture_output
        self.check = check
        self.shell = shell
        self.env_inherit = bool(env_inherit)

    def _build_command_args(self, ctx_data: dict[str, Any]) -> str | list[str]:
        """Resolve templated variables and format command arguments safely."""
        if isinstance(self.command, list):
            return [str(resolve_template_value(arg, ctx_data)) for arg in self.command]

        resolved = str(resolve_template_value(self.command, ctx_data))
        if self.shell:
            return resolved
        return shlex.split(resolved, posix=sys.platform != "win32")

    def _run_subprocess(
        self, cmd_args: str | list[str], custom_env: dict[str, str] | None
    ) -> ShellResult:
        """Run the subprocess with timeout and process tree cleanup."""
        stdout_dest = subprocess.PIPE if self.capture_output else subprocess.DEVNULL
        stderr_dest = subprocess.PIPE if self.capture_output else subprocess.DEVNULL

        preexec = None
        if sys.platform != "win32":
            preexec = os.setsid

        proc = subprocess.Popen(
            cmd_args,
            cwd=self.cwd,
            env=custom_env,
            shell=self.shell,
            stdout=stdout_dest,
            stderr=stderr_dest,
            text=True,
            encoding="utf-8",
            errors="replace",
            preexec_fn=preexec,
        )

        try:
            stdout, stderr = proc.communicate(timeout=self.timeout)
            out_str = stdout.strip() if stdout else ""
            err_str = stderr.strip() if stderr else ""
            rc = proc.returncode
            success = rc == 0
            err_msg = err_str if not success else None
            if self.check and not success:
                raise subprocess.CalledProcessError(rc, cmd_args, output=out_str, stderr=err_str)
            return ShellResult(
                returncode=rc, stdout=out_str, stderr=err_str, success=success, error=err_msg
            )
        except subprocess.TimeoutExpired as exc:
            _kill_process_tree(proc)
            proc.communicate()
            err = f"Command timed out after {self.timeout}s: {exc}"
            if self.check:
                raise TimeoutError(err) from exc
            return ShellResult(returncode=-1, stdout="", stderr=err, success=False, error=err)

    def execute(self, ctx_data: dict[str, Any]) -> ShellResult:
        """Execute the command within a CLIENT OTel span."""
        cmd_args = self._build_command_args(ctx_data)
        if self.env_inherit:
            # Explicit opt-in: pass the full host environment to the child.
            custom_env = dict(os.environ)
        else:
            custom_env = build_minimal_env(allow=list(self.env or {}))
        if self.env:
            for k, v in self.env.items():
                custom_env[k] = str(resolve_template_value(v, ctx_data))

        cmd_display = self.command if isinstance(self.command, str) else " ".join(self.command)

        from ..telemetry import SpanKind, SpanStatusCode, get_tracer

        tracer = get_tracer()
        with tracer.start_as_current_span(
            "tool.shell",
            kind=SpanKind.CLIENT,
            attributes={"process.command": cmd_display, "process.cwd": self.cwd or ""},
        ) as span:
            result = self._run_subprocess(cmd_args, custom_env)
            span.set_attribute("process.exit_code", result.returncode)
            if not result.success:
                span.set_status(
                    SpanStatusCode.ERROR, result.error or f"Exit code {result.returncode}"
                )
            return result

    def to_dict(self) -> dict[str, Any]:
        """Serialize configuration for export."""
        d: dict[str, Any] = {"type": "shell", "command": self.command, "timeout": self.timeout}
        if self.cwd:
            d["cwd"] = self.cwd
        if self.shell:
            d["shell"] = True
        return d
