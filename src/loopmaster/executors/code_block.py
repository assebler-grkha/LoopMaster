"""Subprocess-isolated executor for DB-persisted code blocks.

Blocks are NEVER imported/executed inside the engine process: the source is
extracted into a content-addressed temp directory and run as a subprocess that
speaks a tiny JSON contract over stdin/stdout.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loopmaster.executors.base import BaseExecutor, resolve_template_value
from loopmaster.executors.shell import _kill_process_tree

STDOUT_LIMIT = 1024 * 1024  # 1 MiB cap on block stdout


@dataclass
class CodeBlockResult:
    """Structured result of a code block subprocess run."""

    returncode: int = -1
    ok: bool = False
    output: Any = None
    logs: list[str] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    success: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "returncode": self.returncode,
            "ok": self.ok,
            "output": self.output,
            "logs": self.logs,
            "stdout": self.stdout[-STDOUT_LIMIT:],
            "stderr": self.stderr[-STDOUT_LIMIT:],
            "success": self.success,
            "error": self.error,
        }


def _base_env(allow: list[str]) -> dict[str, str]:
    """Minimal environment: OS plumbing plus explicitly allowed variables."""
    keys = ["PATH", "PYTHONIOENCODING"]
    if sys.platform == "win32":
        keys += ["SYSTEMROOT", "COMSPEC", "TEMP", "TMP", "PATHEXT"]
    else:
        keys += ["HOME", "TMPDIR", "LANG"]
    env = {k: os.environ[k] for k in keys if k in os.environ}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    for name in allow:
        if name in os.environ:
            env[name] = os.environ[name]
    return env


class CodeBlockExecutor(BaseExecutor):
    """Runs a code block from the store as an isolated subprocess.

    Contract: stdin JSON ``{"input": ..., "context": ...}``; stdout JSON
    ``{"ok": bool, "output": {...}, "logs": [...]}``. A non-zero exit or
    ``ok=false`` is a step failure handled by ErrorPolicy.
    """

    def __init__(
        self,
        ref: str,
        sha256: str | None = None,
        input: dict[str, Any] | None = None,
        timeout: float = 60.0,
        env_allow: list[str] | None = None,
        deny_capabilities: list[str] | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        self.ref = str(ref)
        self.sha256 = sha256
        self.input = dict(input or {})
        self.timeout = float(timeout)
        self.env_allow = [str(v) for v in (env_allow or [])]
        self.deny_capabilities = [str(c) for c in (deny_capabilities or [])]
        self.db_path = db_path
        self._store: Any = None

    @property
    def _cache_root(self) -> Path:
        root = Path(tempfile.gettempdir()) / "loopmaster-blocks"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _get_store(self) -> Any:
        if self._store is None:
            from loopmaster.mcp import job_store as js

            self._store = js.JobStore(db_path=self.db_path) if self.db_path else js.get_job_store()
        return self._store

    def _load_block(self) -> tuple[Any | None, str | None]:
        try:
            block = self._get_store().get_code_block(self.ref)
        except Exception as exc:  # pragma: no cover - defensive
            return None, f"failed to load code block '{self.ref}': {exc}"
        if block is None:
            return None, f"unknown code block '{self.ref}'"
        if self.sha256 and block.sha256 != self.sha256:
            return (
                None,
                f"sha256 mismatch for '{self.ref}': pinned {self.sha256[:12]}… "
                f"but store has {block.sha256[:12]}…",
            )
        return block, None

    def _check_capabilities(self, capabilities: list[str]) -> str | None:
        denied = set(capabilities) & set(self.deny_capabilities)
        if denied:
            return (
                f"code block '{self.ref}' requests denied capabilities: "
                f"{sorted(denied)} (spec deny_capabilities)"
            )
        return None

    def _extract(self, source: str, digest: str, filename: str) -> Path:
        target_dir = self._cache_root / digest
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            target.write_text(source, encoding="utf-8")
        return target_dir

    @staticmethod
    def _script_name(language: str, entrypoint: str) -> str:
        if language == "python":
            return f"{entrypoint}.py"
        return entrypoint

    @staticmethod
    def _command_for(block: Any, script_dir: Path) -> list[str]:
        if block.language == "python":
            script = script_dir / f"{block.entrypoint}.py"
            return [sys.executable, str(script)]
        return ["bash", "-e", str(script_dir / block.entrypoint)]

    def execute(self, ctx_data: dict[str, Any]) -> CodeBlockResult:
        """Extract the block and run it as a subprocess with a JSON handshake."""
        block, err = self._load_block()
        if err is not None:
            return CodeBlockResult(error=err)
        assert block is not None

        err = self._check_capabilities(list(getattr(block, "capabilities", [])))
        if err is not None:
            return CodeBlockResult(error=err)

        payload = {
            "input": resolve_template_value(self.input, ctx_data),
            "context": ctx_data,
        }

        script_dir = self._extract(
            block.source,
            hashlib.sha256(block.source.encode("utf-8")).hexdigest(),
            self._script_name(block.language, getattr(block, "entrypoint", "main")),
        )
        cmd = self._command_for(block, script_dir)

        popen_kwargs: dict[str, Any] = {}
        if sys.platform != "win32":
            popen_kwargs["preexec_fn"] = os.setsid
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(script_dir),
                env=_base_env(self.env_allow),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                **popen_kwargs,
            )
        except OSError as exc:
            return CodeBlockResult(error=f"failed to spawn code block '{self.ref}': {exc}")

        try:
            stdout, stderr = proc.communicate(
                json.dumps(payload, default=str), timeout=self.timeout
            )
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            return CodeBlockResult(error=f"code block '{self.ref}' timed out after {self.timeout}s")

        if len(stdout) > STDOUT_LIMIT:
            return CodeBlockResult(
                stdout=stdout[:STDOUT_LIMIT],
                stderr=stderr,
                returncode=proc.returncode,
                error=(
                    f"code block '{self.ref}' produced {len(stdout)} bytes of stdout "
                    f"(limit {STDOUT_LIMIT})"
                ),
            )

        try:
            reply = json.loads(stdout) if stdout.strip() else {}
        except json.JSONDecodeError as exc:
            return CodeBlockResult(
                stdout=stdout[-STDOUT_LIMIT:],
                stderr=stderr,
                returncode=proc.returncode,
                error=f"code block '{self.ref}' returned invalid JSON on stdout: {exc}",
            )

        output = reply.get("output")
        logs = [str(item) for item in reply.get("logs", [])]

        if proc.returncode != 0:
            tail = (reply.get("error") or stderr.strip() or "non-zero exit")[-2000:]
            return CodeBlockResult(
                returncode=proc.returncode,
                ok=bool(reply.get("ok")),
                output=output,
                logs=logs,
                stdout=stdout,
                stderr=stderr,
                error=f"code block '{self.ref}' failed (exit {proc.returncode}): {tail}",
            )

        if not reply.get("ok"):
            reason = str(reply.get("error") or "block reported ok=false")
            return CodeBlockResult(
                returncode=proc.returncode,
                output=output,
                logs=logs,
                stdout=stdout,
                stderr=stderr,
                error=f"code block '{self.ref}' reported failure: {reason}",
            )

        return CodeBlockResult(
            returncode=0,
            ok=True,
            output=output,
            logs=logs,
            stdout=stdout,
            stderr=stderr,
            success=True,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize executor configuration."""
        return {
            "executor": "code_block",
            "ref": self.ref,
            "sha256": self.sha256,
            "input": self.input,
            "timeout": self.timeout,
            "deny_capabilities": self.deny_capabilities,
        }
