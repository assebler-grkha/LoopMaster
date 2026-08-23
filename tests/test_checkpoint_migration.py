"""Tests for checkpoint migration, versioning, and compatibility policies."""

from __future__ import annotations

import tempfile

import pytest

from loopmaster import Loop, Step
from loopmaster.checkpoint import (
    CheckpointManager,
    CompatibilityPolicy,
    MigrationRegistry,
    SemVer,
    check_and_migrate_checkpoint,
    migrate_checkpoint,
    register_migration,
    rename_checkpoint_step,
)
from loopmaster.core.engine import LoopEngine
from loopmaster.core.exceptions import IncompatibleCheckpointError, MigrationError
from loopmaster.core.types import CheckpointData, StepResult


def _make_cp(
    name: str = "test_loop",
    version: str = "1.0.0",
    source_hash: str = "hash_v1",
    executed: list[str] | None = None,
    context: dict | None = None,
) -> CheckpointData:
    executed_steps = executed or ["step1"]
    return CheckpointData(
        loop_name=name,
        loop_version=version,
        loop_source_hash=source_hash,
        step_index=len(executed_steps),
        context_data=context or {"raw_text": "hello"},
        completed_results={
            s: StepResult(step_name=s, success=True, output=f"out_{s}") for s in executed_steps
        },
        executed_step_names=executed_steps,
    )


class TestSemVer:
    def test_parse_valid(self):
        v1 = SemVer.parse("1.2.3")
        assert v1 is not None and v1.major == 1 and v1.minor == 2 and v1.patch == 3
        v2 = SemVer.parse("v2.0")
        assert v2 is not None and v2.major == 2 and v2.minor == 0 and v2.patch == 0
        v3 = SemVer.parse("0.1.0-alpha.1")
        assert v3 is not None and v3.major == 0 and v3.prerelease == "alpha.1"

    def test_parse_invalid(self):
        assert SemVer.parse("") is None
        assert SemVer.parse(None) is None  # type: ignore[arg-type]
        assert SemVer.parse("latest") is None
        assert SemVer.parse("abc.def") is None

    def test_semver_compatibility_rules(self):
        # 1.x.y forward compatible
        v1_0 = SemVer.parse("1.0.0")
        v1_2 = SemVer.parse("1.2.0")
        v2_0 = SemVer.parse("2.0.0")
        assert v1_0.is_compatible_with(v1_2) is True  # Forward minor
        assert v1_2.is_compatible_with(v1_0) is False  # Downgrade rejected
        assert v1_0.is_compatible_with(v2_0) is False  # Breaking major

        # 0.x.y rules (minor bumps are breaking in 0.x)
        v0_1_0 = SemVer.parse("0.1.0")
        v0_1_2 = SemVer.parse("0.1.2")
        v0_2_0 = SemVer.parse("0.2.0")
        assert v0_1_0.is_compatible_with(v0_1_2) is True  # Patch in 0.1
        assert v0_1_0.is_compatible_with(v0_2_0) is False  # 0.1 -> 0.2 breaking


class TestCompatibilityPolicies:
    def test_semver_compatible_same_major(self):
        cp = _make_cp(version="1.0.0", source_hash="h1")

        @Loop(name="test_loop", version="1.1.0")
        def target_loop(ctx):
            Step("step1")
            Step("step2")

        # Passes with warning on hash mismatch
        res = check_and_migrate_checkpoint(
            cp, target_loop, policy=CompatibilityPolicy.SEMVER_COMPATIBLE
        )
        assert res.loop_version == "1.0.0"

    def test_semver_compatible_different_major_raises(self):
        cp = _make_cp(version="1.0.0")

        @Loop(name="test_loop", version="2.0.0")
        def target_loop(ctx):
            Step("step1")

        with pytest.raises(IncompatibleCheckpointError, match="incompatible with target loop"):
            check_and_migrate_checkpoint(
                cp, target_loop, policy=CompatibilityPolicy.SEMVER_COMPATIBLE
            )

    def test_strict_policy_rejects_hash_mismatch(self):
        cp = _make_cp(version="1.0.0", source_hash="hash_old")

        @Loop(name="test_loop", version="1.0.0")
        def target_loop(ctx):
            Step("step1")

        with pytest.raises(
            IncompatibleCheckpointError, match="is incompatible under STRICT policy"
        ):
            check_and_migrate_checkpoint(cp, target_loop, policy=CompatibilityPolicy.STRICT)

    def test_permissive_policy_allows_major_jump(self):
        cp = _make_cp(version="1.0.0", source_hash="h1")

        @Loop(name="test_loop", version="3.0.0")
        def target_loop(ctx):
            Step("step1")

        res = check_and_migrate_checkpoint(cp, target_loop, policy=CompatibilityPolicy.PERMISSIVE)
        assert res.loop_version == "1.0.0"

    def test_loop_name_mismatch_raises(self):
        cp = _make_cp(name="loop_a")

        @Loop(name="loop_b", version="1.0.0")
        def target_loop(ctx):
            Step("step1")

        with pytest.raises(IncompatibleCheckpointError, match="does not match"):
            check_and_migrate_checkpoint(cp, target_loop)


class TestMigrationRegistryAndPipeline:
    def test_single_and_chained_migrations(self):
        reg = MigrationRegistry()

        @register_migration("etl_loop", "1.0.0", "1.1.0", registry=reg)
        def v1_to_v1_1(cp: CheckpointData) -> CheckpointData:
            cp.context_data["v1_1"] = True
            rename_checkpoint_step(cp, "step1", "extract_step")
            return cp

        @register_migration("etl_loop", "1.1.0", "2.0.0", registry=reg)
        def v1_1_to_v2(cp: CheckpointData) -> CheckpointData:
            cp.context_data["v2_0"] = True
            return cp

        cp = _make_cp(name="etl_loop", version="1.0.0", executed=["step1"])
        migrated = migrate_checkpoint(
            cp, target_version="2.0.0", target_source_hash="h2", registry=reg
        )

        assert migrated.loop_version == "2.0.0"
        assert migrated.loop_source_hash == "h2"
        assert "extract_step" in migrated.executed_step_names
        assert "step1" not in migrated.executed_step_names
        assert "extract_step" in migrated.completed_results
        assert migrated.context_data.get("v1_1") is True
        assert migrated.context_data.get("v2_0") is True

        # Ensure original checkpoint is untouched
        assert cp.loop_version == "1.0.0"
        assert "step1" in cp.executed_step_names

    def test_shortest_path_selection_and_cycle_handling(self):
        reg = MigrationRegistry()
        # Direct shortcut: 1.0 -> 2.0
        reg.register("loop_x", "1.0.0", "2.0.0", lambda cp: cp)
        # Multi-hop: 1.0 -> 1.1 -> 2.0
        reg.register("loop_x", "1.0.0", "1.1.0", lambda cp: cp)
        reg.register("loop_x", "1.1.0", "2.0.0", lambda cp: cp)
        # Cycle: 1.1 -> 1.0
        reg.register("loop_x", "1.1.0", "1.0.0", lambda cp: cp)

        path = reg.find_path("loop_x", "1.0.0", "2.0.0")
        assert path is not None
        assert len(path) == 1  # BFS chose the direct shortcut!

    def test_migration_failure_atomic(self):
        reg = MigrationRegistry()

        @register_migration("failing_loop", "1.0.0", "2.0.0", registry=reg)
        def faulty(cp: CheckpointData) -> CheckpointData:
            cp.context_data["mutated"] = True
            raise ValueError("Corrupted schema")

        cp = _make_cp(name="failing_loop", version="1.0.0")
        with pytest.raises(MigrationError, match="failed: Corrupted schema"):
            migrate_checkpoint(cp, target_version="2.0.0", registry=reg)

        # Original context was not corrupted
        assert "mutated" not in cp.context_data


class TestCheckpointManagerIntegration:
    def test_load_and_migrate(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            mgr = CheckpointManager(tmpdir)
            reg = MigrationRegistry()

            @register_migration("pipe", "1.0.0", "2.0.0", registry=reg)
            def migrate_pipe(cp: CheckpointData) -> CheckpointData:
                rename_checkpoint_step(cp, "fetch", "fetch_v2")
                return cp

            cp = _make_cp(name="pipe", version="1.0.0", executed=["fetch"])
            path = mgr.save(cp)

            @Loop(name="pipe", version="2.0.0")
            def pipe_v2(ctx):
                Step("fetch_v2")
                Step("process")

            migrated = mgr.load_and_migrate(path, target_loop_def=pipe_v2, registry=reg)
            assert migrated.loop_version == "2.0.0"
            assert "fetch_v2" in migrated.executed_step_names


class TestLoopEngineResumeWithMigration:
    def test_engine_resumes_migrated_loop(self):
        reg = MigrationRegistry()

        @register_migration("engine_test", "1.0.0", "2.0.0", registry=reg)
        def upgrade_loop(cp: CheckpointData) -> CheckpointData:
            rename_checkpoint_step(cp, "old_step1", "step1_v2")
            cp.context_data["step1_output"] = "migrated_output"
            return cp

        # Checkpoint from Loop v1.0.0
        cp = _make_cp(
            name="engine_test",
            version="1.0.0",
            source_hash="old_hash",
            executed=["old_step1"],
            context={"step1_output": "initial"},
        )

        # Loop v2.0.0 definition
        @Loop(name="engine_test", version="2.0.0")
        def loop_v2(ctx):
            Step("step1_v2")  # Should be skipped because migration renamed old_step1 -> step1_v2
            Step("step2", input=ctx.get("step1_output"))

        engine = LoopEngine(checkpoint_dir=None)

        # Using custom registry for isolation
        from loopmaster.checkpoint import migration as mig_mod

        old_reg = mig_mod.migration_registry
        try:
            mig_mod.migration_registry = reg
            result = engine.run(loop_v2, resume_checkpoint=cp)
            assert result.success is True
            assert "step1_v2" in result.steps_executed or "step1_v2" in cp.executed_step_names
            assert "step2" in result.results
            assert result.resume_count == 1
        finally:
            mig_mod.migration_registry = old_reg
