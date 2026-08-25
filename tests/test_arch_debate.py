"""Test script for arch_debate loop."""

import os
import sys


if __name__ == "__main__":
    # Set environment variables
    os.environ["LOOPMASTER_LLM_PROVIDER"] = "openrouter"
    os.environ["LOOPMASTER_LLM_MODEL"] = "stealth/ox-alpha"

    # Add src to path
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

    from loopmaster.core.engine import LoopEngine
    from loopmaster.core.types import ErrorPolicy, RecoveryAction

    # Import the loop definition
    spec = __import__("importlib").util.spec_from_file_location(
        "arch_debate_loop",
        os.path.join(
            os.path.dirname(__file__), "..", "loops", "scenario12_arch_debate.py"
        ),
    )
    module = __import__("importlib").util.module_from_spec(spec)
    spec.loader.exec_module(module)

    loop_def = getattr(module, "arch_debate", None)
    if loop_def is None:
        print("ERROR: arch_debate not found in module")
        sys.exit(1)

    from loopmaster.core.types import LoopDef

    if not isinstance(loop_def, LoopDef):
        print(f"ERROR: arch_debate is {type(loop_def)}, not LoopDef")
        sys.exit(1)

    print(f"Found loop: {loop_def.name} v{loop_def.version}")

    # Create engine and run
    engine = LoopEngine(
        error_policy=ErrorPolicy(
            retry=2,
            on_failure=RecoveryAction.FALLBACK,
            fallback_model="@smart",
        ),
    )
    engine.register(loop_def)

    # Test with a simple goal
    context = {
        "goal": "Optimize database queries for the user authentication module",
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
