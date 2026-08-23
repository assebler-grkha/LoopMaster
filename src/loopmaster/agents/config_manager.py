"""ConfigManager — safe config modification with snapshot and rollback.

Safety guarantees:
1. Snapshot ALL agent files before any change
2. Atomic writes: write to temp file → rename → verify
3. Rollback on failure: any error restores original state
4. Dry-run preview: show diff before applying
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .base import AgentAdapter


class ConfigError(Exception):
    """Raised when a config operation fails."""


class ConfigManager:
    """Safe config modification with snapshot and rollback.

    Usage:
        adapter = OpenCodeAdapter()
        mgr = ConfigManager(adapter)
        mgr.snapshot_all()
        # ... make changes ...
        # On error, mgr.rollback() restores everything.
    """

    def __init__(self, adapter: AgentAdapter) -> None:
        self.adapter = adapter
        self._snapshots: dict[Path, bytes] = {}
        self._modified_files: list[Path] = []

    def snapshot_all(self) -> dict[Path, bytes]:
        """Snapshot ALL agent files before any change.

        Returns a copy of the snapshot map for inspection.
        """
        self._snapshots.clear()
        for file_path in self.adapter.config_files:
            if file_path.exists():
                self._snapshots[file_path] = file_path.read_bytes()
        return dict(self._snapshots)

    def atomic_write(self, file_path: Path, content: str | bytes) -> None:
        """Write to temp file, then atomic rename. Verify after write."""
        if isinstance(content, str):
            content = content.encode("utf-8")

        file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            with tempfile.NamedTemporaryFile(
                dir=file_path.parent,
                delete=False,
                suffix=file_path.suffix,
            ) as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)

            tmp_path.rename(file_path)

            if file_path.read_bytes() != content:
                self.rollback()
                msg = f"Write verification failed for {file_path}"
                raise ConfigError(msg)

            self._modified_files.append(file_path)
        except Exception as e:
            if isinstance(e, ConfigError):
                raise
            self.rollback()
            msg = f"Atomic write failed for {file_path}: {e}"
            raise ConfigError(msg) from e

    def rollback(self) -> None:
        """Restore all files from snapshots."""
        for file_path, original_content in self._snapshots.items():
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(original_content)
        self._modified_files.clear()

    def dry_run_diff(self) -> dict[str, str]:
        """Show what would change without making changes.

        Returns dict of file_path → unified diff string.
        """
        diffs: dict[str, str] = {}
        for file_path, original in self._snapshots.items():
            if file_path.exists():
                current = file_path.read_bytes()
                if current != original:
                    dec = "utf-8"
                    err = "replace"
                    orig_lines = original.decode(dec, errors=err).splitlines(keepends=True)
                    curr_lines = current.decode(dec, errors=err).splitlines(keepends=True)
                    import difflib

                    diff = difflib.unified_diff(
                        orig_lines,
                        curr_lines,
                        fromfile=f"original/{file_path.name}",
                        tofile=f"modified/{file_path.name}",
                    )
                    diffs[str(file_path)] = "".join(diff)
        return diffs

    def save_snapshot_to(self, directory: Path) -> None:
        """Persist snapshots to disk for later rollback."""
        directory.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, str] = {}
        for file_path, content in self._snapshots.items():
            safe_name = str(file_path).replace("/", "_").replace("\\", "_").replace(":", "_")
            snapshot_path = directory / safe_name
            snapshot_path.write_bytes(content)
            manifest[str(file_path)] = safe_name
        manifest_path = directory / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def restore_from_snapshot(self, directory: Path) -> None:
        """Restore files from persisted snapshots."""
        manifest_path = directory / "manifest.json"
        if not manifest_path.exists():
            return
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for file_path_str, snapshot_name in manifest.items():
            file_path = Path(file_path_str)
            snapshot_path = directory / snapshot_name
            if snapshot_path.exists():
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(snapshot_path.read_bytes())

    @property
    def has_snapshots(self) -> bool:
        return len(self._snapshots) > 0
