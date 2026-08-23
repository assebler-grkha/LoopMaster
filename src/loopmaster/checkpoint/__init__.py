"""Checkpoint manager — save/load checkpoints to/from disk with migration support."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..core.exceptions import CheckpointError
from ..core.types import CheckpointData
from .migration import (
    CompatibilityPolicy,
    MigrationRegistry,
    SemVer,
    check_and_migrate_checkpoint,
    migrate_checkpoint,
    migration_registry,
    register_migration,
    rename_checkpoint_step,
)

__all__ = [
    "CheckpointManager",
    "CompatibilityPolicy",
    "MigrationRegistry",
    "SemVer",
    "check_and_migrate_checkpoint",
    "migrate_checkpoint",
    "migration_registry",
    "register_migration",
    "rename_checkpoint_step",
]


class CheckpointManager:
    """Manages checkpoint persistence and migrations.

    Checkpoints are stored as JSON files in a directory, named by loop_name + hash.
    """

    def __init__(self, checkpoint_dir: str | Path = ".loopmaster/checkpoints") -> None:
        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def save(self, checkpoint: CheckpointData) -> Path:
        """Save checkpoint to disk. Returns file path."""
        data = asdict(checkpoint)
        data["_format_version"] = 1

        content = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:12]

        filename = f"{checkpoint.loop_name}_{content_hash}.json"
        filepath = self.checkpoint_dir / filename

        filepath.write_text(content, encoding="utf-8")

        latest = self.checkpoint_dir / f"{checkpoint.loop_name}_latest.json"
        latest.write_text(content, encoding="utf-8")

        return filepath

    def load_latest(self, loop_name: str) -> CheckpointData | None:
        """Load the most recent checkpoint for a loop."""
        latest = self.checkpoint_dir / f"{loop_name}_latest.json"
        if not latest.exists():
            return None
        return self._load_file(latest)

    def load(self, filepath: Path) -> CheckpointData:
        """Load a specific checkpoint file."""
        if not filepath.exists():
            raise CheckpointError(f"Checkpoint file not found: {filepath}")
        return self._load_file(filepath)

    def load_and_migrate(
        self,
        filepath: Path,
        target_loop_def: Any,
        policy: CompatibilityPolicy = CompatibilityPolicy.SEMVER_COMPATIBLE,
        registry: MigrationRegistry | None = None,
    ) -> CheckpointData:
        """Load checkpoint file and validate/migrate against target loop definition."""
        checkpoint = self.load(filepath)
        return check_and_migrate_checkpoint(
            checkpoint=checkpoint,
            loop_def=target_loop_def,
            policy=policy,
            registry=registry,
        )

    def load_latest_and_migrate(
        self,
        loop_name: str,
        target_loop_def: Any,
        policy: CompatibilityPolicy = CompatibilityPolicy.SEMVER_COMPATIBLE,
        registry: MigrationRegistry | None = None,
    ) -> CheckpointData | None:
        """Load latest checkpoint and validate/migrate against target loop definition."""
        checkpoint = self.load_latest(loop_name)
        if checkpoint is None:
            return None
        return check_and_migrate_checkpoint(
            checkpoint=checkpoint,
            loop_def=target_loop_def,
            policy=policy,
            registry=registry,
        )

    def list_checkpoints(self, loop_name: str | None = None) -> list[dict[str, Any]]:
        """List available checkpoints, optionally filtered by loop name."""
        checkpoints = []
        for f in sorted(self.checkpoint_dir.glob("*.json")):
            if "_latest.json" in f.name:
                continue
            if loop_name and not f.name.startswith(loop_name):
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                checkpoints.append(
                    {
                        "path": str(f),
                        "loop_name": data.get("loop_name", "unknown"),
                        "loop_version": data.get("loop_version", "unknown"),
                        "step_index": data.get("step_index", 0),
                        "executed_step_names": data.get("executed_step_names", []),
                    }
                )
            except (json.JSONDecodeError, KeyError):
                continue
        return checkpoints

    def delete(self, loop_name: str) -> int:
        """Delete all checkpoints for a loop. Returns count deleted."""
        count = 0
        for f in self.checkpoint_dir.glob(f"{loop_name}_*.json"):
            f.unlink()
            count += 1
        return count

    def _load_file(self, filepath: Path) -> CheckpointData:
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            data.pop("_format_version", None)
            return CheckpointData(**data)
        except json.JSONDecodeError as e:
            raise CheckpointError(f"Invalid checkpoint file: {e}") from e
        except TypeError as e:
            raise CheckpointError(f"Checkpoint data mismatch: {e}") from e
