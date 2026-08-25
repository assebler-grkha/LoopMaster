"""Test script for refactor_loop."""

import os
import sys

if __name__ == "__main__":
    os.environ["LOOPMASTER_LLM_PROVIDER"] = "openrouter"
    os.environ["LOOPMASTER_LLM_MODEL"] = "stealth/ox-alpha"

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

    from loopmaster.core.engine import LoopEngine
    from loopmaster.core.types import ErrorPolicy, RecoveryAction

    spec = __import__("importlib").util.spec_from_file_location(
        "refactor_loop",
        os.path.join(os.path.dirname(__file__), "..", "loops", "scenario13_refactor_loop.py"),
    )
    module = __import__("importlib").util.module_from_spec(spec)
    spec.loader.exec_module(module)

    loop_def = getattr(module, "refactor_loop", None)
    if loop_def is None:
        print("ERROR: refactor_loop not found")
        sys.exit(1)

    from loopmaster.core.types import LoopDef

    if not isinstance(loop_def, LoopDef):
        print(f"ERROR: refactor_loop is {type(loop_def)}, not LoopDef")
        sys.exit(1)

    print(f"Found loop: {loop_def.name} v{loop_def.version}")

    engine = LoopEngine(
        error_policy=ErrorPolicy(
            retry=2,
            on_failure=RecoveryAction.FALLBACK,
            fallback_model="@smart",
        ),
    )
    engine.register(loop_def)

    context = {
        "path": "tests/test_target_refactor.py",
        "project": "C-Projects-Ideas-LoopMaster",
    }

    print(f"\nRunning with context: {context}")
    print("=" * 60)

    result = engine.run(loop_def, context)

    print("=" * 60)
    if result.success:
        print("SUCCESS!")
        print(f"Cost: ${result.total_cost:.4f}")
        print(f"Tokens: {result.total_tokens}")
        print(f"Steps executed: {', '.join(result.steps_executed)}")
    else:
        print(f"FAILED: {result.error}")
