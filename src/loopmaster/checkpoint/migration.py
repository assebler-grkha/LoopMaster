"""Loop versioning, compatibility policies, and checkpoint migration."""

from __future__ import annotations

import copy
import logging
import re
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..core.exceptions import IncompatibleCheckpointError, MigrationError
from ..core.types import CheckpointData, StepResult

logger = logging.getLogger("loopmaster.checkpoint.migration")

SEMVER_PATTERN = re.compile(
    r"^v?(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?(?:-(?P<prerelease>[0-9A-Za-z.-]+))?$"
)


@dataclass(frozen=True, order=True)
class SemVer:
    """Semantic version representation."""

    major: int
    minor: int = 0
    patch: int = 0
    prerelease: str = ""

    @classmethod
    def parse(cls, version_str: str | None) -> SemVer | None:
        """Parse a version string into a SemVer object."""
        if not version_str or not isinstance(version_str, str):
            return None
        match = SEMVER_PATTERN.match(version_str.strip())
        if not match:
            return None
        major = int(match.group("major"))
        minor = int(match.group("minor") or 0)
        patch = int(match.group("patch") or 0)
        prerelease = match.group("prerelease") or ""
        return cls(major=major, minor=minor, patch=patch, prerelease=prerelease)

    def is_compatible_with(self, target: SemVer) -> bool:
        """Check forward compatibility (self is checkpoint, target is runtime)."""
        if self.major != target.major:
            return False
        if self.major == 0:
            return self.minor == target.minor and self <= target
        return self <= target


class CompatibilityPolicy(StrEnum):
    """Policies for checkpoint version and source hash validation."""

    STRICT = "strict"
    SEMVER_COMPATIBLE = "semver_compatible"
    PERMISSIVE = "permissive"


MigrationFunc = Callable[[CheckpointData], CheckpointData]


class MigrationRegistry:
    """Registry holding loop migration functions."""

    def __init__(self) -> None:
        self._migrations: dict[str, dict[str, dict[str, MigrationFunc]]] = {}

    def register(
        self,
        loop_name: str,
        from_version: str,
        to_version: str,
        fn: MigrationFunc,
    ) -> None:
        """Register a migration step for a specific loop."""
        norm_from = self._normalize_version(from_version)
        norm_to = self._normalize_version(to_version)
        loop_bucket = self._migrations.setdefault(loop_name, {})
        from_bucket = loop_bucket.setdefault(norm_from, {})
        from_bucket[norm_to] = fn

    def find_path(
        self,
        loop_name: str,
        from_version: str,
        to_version: str,
    ) -> list[tuple[str, str, MigrationFunc]] | None:
        """Find the shortest migration path using BFS to avoid cycles."""
        norm_from = self._normalize_version(from_version)
        norm_to = self._normalize_version(to_version)
        if norm_from == norm_to:
            return []

        transitions = self._migrations.get(loop_name, {})
        if not transitions:
            return None

        queue: deque[tuple[str, list[tuple[str, str, MigrationFunc]]]] = deque([(norm_from, [])])
        visited: set[str] = {norm_from}

        while queue:
            current_ver, path = queue.popleft()
            if current_ver == norm_to:
                return path

            for next_ver, fn in transitions.get(current_ver, {}).items():
                if next_ver not in visited:
                    visited.add(next_ver)
                    queue.append((next_ver, [*path, (current_ver, next_ver, fn)]))

        return None

    def clear(self) -> None:
        """Clear all registered migrations."""
        self._migrations.clear()

    @staticmethod
    def _normalize_version(version_str: str) -> str:
        parsed = SemVer.parse(version_str)
        if parsed:
            pre = f"-{parsed.prerelease}" if parsed.prerelease else ""
            return f"{parsed.major}.{parsed.minor}.{parsed.patch}{pre}"
        return version_str.strip().lstrip("v")


migration_registry = MigrationRegistry()


def register_migration(
    loop_name: str,
    from_version: str,
    to_version: str,
    registry: MigrationRegistry | None = None,
) -> Callable[[MigrationFunc], MigrationFunc]:
    """Decorator to register a migration function for a loop."""
    reg = registry or migration_registry

    def decorator(fn: MigrationFunc) -> MigrationFunc:
        reg.register(loop_name, from_version, to_version, fn)
        return fn

    return decorator


def rename_checkpoint_step(
    checkpoint: CheckpointData,
    old_name: str,
    new_name: str,
) -> None:
    """Atomically rename a step across executed_step_names and completed_results."""
    checkpoint.executed_step_names = [
        new_name if name == old_name else name for name in checkpoint.executed_step_names
    ]
    if old_name in checkpoint.completed_results:
        res = checkpoint.completed_results.pop(old_name)
        if isinstance(res, dict):
            res["step_name"] = new_name
            checkpoint.completed_results[new_name] = res
        elif isinstance(res, StepResult):
            res.step_name = new_name
            checkpoint.completed_results[new_name] = res
        else:
            checkpoint.completed_results[new_name] = res

    if old_name in checkpoint.context_data and new_name not in checkpoint.context_data:
        checkpoint.context_data[new_name] = checkpoint.context_data.pop(old_name)


def migrate_checkpoint(
    checkpoint: CheckpointData,
    target_version: str,
    target_source_hash: str = "",
    registry: MigrationRegistry | None = None,
) -> CheckpointData:
    """Apply registered migrations to transform checkpoint to target version."""
    reg = registry or migration_registry
    path = reg.find_path(checkpoint.loop_name, checkpoint.loop_version, target_version)
    if path is None:
        raise MigrationError(
            f"No migration path from '{checkpoint.loop_version}' to '{target_version}' "
            f"for loop '{checkpoint.loop_name}'"
        )
    if not path:
        return checkpoint

    migrated = copy.deepcopy(checkpoint)
    for step_from, step_to, fn in path:
        try:
            res = fn(migrated)
            if not isinstance(res, CheckpointData):
                raise MigrationError(
                    f"Migration {step_from}->{step_to} must return CheckpointData instance"
                )
            migrated = res
            migrated.loop_version = step_to
        except Exception as exc:
            if isinstance(exc, MigrationError):
                raise
            raise MigrationError(
                f"Migration {step_from}->{step_to} for loop '{checkpoint.loop_name}' failed: {exc}"
            ) from exc

    migrated.loop_version = target_version
    migrated.loop_source_hash = target_source_hash or migrated.loop_source_hash
    migrated.step_index = len(migrated.executed_step_names)
    return migrated


def check_and_migrate_checkpoint(
    checkpoint: CheckpointData,
    loop_def: Any,
    policy: CompatibilityPolicy = CompatibilityPolicy.SEMVER_COMPATIBLE,
    registry: MigrationRegistry | None = None,
) -> CheckpointData:
    """Check checkpoint compatibility against loop_def, migrating if needed."""
    reg = registry or migration_registry
    target_name = getattr(loop_def, "name", checkpoint.loop_name)
    target_version = getattr(loop_def, "version", checkpoint.loop_version)
    target_hash = getattr(loop_def, "source_hash", checkpoint.loop_source_hash)

    if checkpoint.loop_name != target_name:
        raise IncompatibleCheckpointError(
            f"Checkpoint loop name '{checkpoint.loop_name}' does not match '{target_name}'"
        )

    # 1. Exact match
    if checkpoint.loop_version == target_version and checkpoint.loop_source_hash == target_hash:
        return checkpoint

    # 2. Check for migration path
    norm_cp_ver = MigrationRegistry._normalize_version(checkpoint.loop_version)
    norm_tgt_ver = MigrationRegistry._normalize_version(target_version)
    if norm_cp_ver != norm_tgt_ver:
        path = reg.find_path(target_name, checkpoint.loop_version, target_version)
        if path:
            return migrate_checkpoint(checkpoint, target_version, target_hash, registry=reg)

    # 3. Policy evaluation when no migration path exists
    if policy == CompatibilityPolicy.STRICT and (
        checkpoint.loop_version != target_version
        or (target_hash and checkpoint.loop_source_hash != target_hash)
    ):
        raise IncompatibleCheckpointError(
            f"Checkpoint (v{checkpoint.loop_version}, hash {checkpoint.loop_source_hash[:8]}) "
            f"is incompatible under STRICT policy with loop '{target_name}' "
            f"(v{target_version}, hash {target_hash[:8] if target_hash else 'N/A'})"
        )

    if policy == CompatibilityPolicy.SEMVER_COMPATIBLE:
        cp_sem = SemVer.parse(checkpoint.loop_version)
        tgt_sem = SemVer.parse(target_version)
        if not cp_sem or not tgt_sem or not cp_sem.is_compatible_with(tgt_sem):
            raise IncompatibleCheckpointError(
                f"Checkpoint version '{checkpoint.loop_version}' is incompatible with "
                f"target loop '{target_name}' version '{target_version}'"
            )
        if target_hash and checkpoint.loop_source_hash != target_hash:
            logger.warning(
                "Source hash mismatch for loop '%s': checkpoint hash %s != current hash %s. "
                "Step logic may have changed since checkpoint.",
                target_name,
                checkpoint.loop_source_hash[:8],
                target_hash[:8],
            )

    return checkpoint
