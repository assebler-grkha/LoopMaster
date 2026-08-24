"""Scenario 13: Automated Refactoring Loop.

Scans codebase for quality issues, prioritizes by impact, reads target code,
produces refactored version, writes to file, runs tests, compares scores.

Phases:
  1. Scan — run aislop scan to get baseline score and findings
  2. Auto-fix — apply aislop_fix for mechanical fixes
  3. Prioritize — LLM picks one finding to refactor manually
  4. Read — get source code of the target function/class
  5. Refactor — LLM produces refactored version
  6. Apply — write refactored code to file (with backup)
  7. Verify — run tests + compare aislop scores
  8. Decide — keep if improved, revert if not

Trigger: user asks for refactoring, code cleanup, or quality improvement.

Usage:
  loop_run("refactor_loop", context={"project": "...", "path": "src/"})
"""

import sys
from pathlib import Path

from loopmaster import Budget, Loop, ShellExecutor, Step
from loopmaster.core.types import Conditional

MODEL = "stealth/ox-alpha"
BRIDGE = str(Path(__file__).resolve().parent.parent / "tests" / "mcp_tool.py")
PYTHON = sys.executable


def _cmd(*args: str) -> list[str]:
    """Build a shell command list: python mcp_tool.py <args>."""
    return [PYTHON, BRIDGE, *args]


# ── Prompts ─────────────────────────────────────────────────────

PROMPT_PRIORITIZE = """\
You are a code quality expert. Analyze these aislop findings and pick THE SINGLE
MOST IMPACTFUL finding to fix manually (aislop_fix already handled auto-fixable ones).

FINDINGS:
{aislop_scan_after_fix.stdout}

Project path: {path}

Pick ONE finding that:
1. Has the highest impact on code quality
2. Is safe to refactor (won't break behavior)
3. Is feasible to do in a single step

Return EXACTLY in this format:
TARGET: <qualified_name or file_path>
REASON: <why this finding>
EXPECTED_BENEFIT: <what improves>"""

PROMPT_REFACTOR = """\
You are a senior software engineer. Refactor this code to fix the identified issue.

ISSUE: {prioritize.REASON}
TARGET: {prioritize.TARGET}

CURRENT CODE:
{read_source.stdout}

Requirements:
1. Fix the identified issue
2. Preserve ALL existing behavior (no semantic changes)
3. Follow existing code style and conventions
4. Keep the same public API (function signatures, class interfaces)
5. Add/fix type hints if applicable
6. Remove dead code, simplify complexity

Output ONLY the refactored code. No explanation, no markdown fences.
Start with the first line of code."""

PROMPT_COMPARE = """\
You are a code quality analyst. Compare these aislop scan results.

BASELINE SCORE: {aislop_scan_baseline.stdout}
AFTER REFACTOR: {aislop_scan_after_refactor.stdout}

Did the score improve?
- If score improved → return: VERDICT: IMPROVED
- If score unchanged → return: VERDICT: UNCHANGED
- If score degraded → return: VERDICT: DEGRADED

Also note the key differences in findings.
Return EXACTLY: VERDICT: <IMPROVED|UNCHANGED|DEGRADED>"""


# ── Loop definition ─────────────────────────────────────────────


@Loop(
    name="refactor_loop",
    version="1.0.0",
    budget=Budget(max_steps=25),
)
def refactor_loop(ctx):
    # ── Phase 1: Scan ──

    Step(
        "get_timestamp",
        executor=ShellExecutor(command=[PYTHON, "-c", "import time; print(int(time.time()))"]),
    )

    Step(
        "aislop_scan_baseline",
        executor=ShellExecutor(command=_cmd("aislop_scan", "--path", "{path}")),
    )

    # ── Phase 2: Auto-fix ──

    Step(
        "aislop_fix",
        executor=ShellExecutor(command=_cmd("aislop_fix", "--path", "{path}")),
    )

    Step(
        "aislop_scan_after_fix",
        executor=ShellExecutor(command=_cmd("aislop_scan", "--path", "{path}")),
    )

    # ── Phase 3: Prioritize ──

    Step(
        "prioritize",
        model=MODEL,
        prompt=PROMPT_PRIORITIZE,
    )

    # ── Phase 4: Read target ──

    Step(
        "get_target_name",
        model=MODEL,
        prompt=(
            "Extract the TARGET value from this text. Return ONLY the "
            "qualified name or file path, nothing else:\n\n{prioritize}"
        ),
    )

    Step(
        "read_source",
        executor=ShellExecutor(
            command=_cmd(
                "read_file",
                "--file_path",
                "{get_target_name}",
            )
        ),
    )

    # ── Phase 5: Refactor ──

    Step(
        "refactor_code",
        model=MODEL,
        prompt=PROMPT_REFACTOR,
    )

    # ── Phase 6: Apply ──

    Step(
        "write_refactored",
        executor=ShellExecutor(
            command=_cmd(
                "write_file",
                "--file_path",
                "{get_target_name}",
                "--content",
                "{refactor_code}",
                "--backup",
                "true",
            )
        ),
    )

    # ── Phase 7: Verify ──

    Step(
        "run_tests",
        executor=ShellExecutor(command=_cmd("run_tests", "--test_path", "{path}")),
    )

    Step(
        "aislop_scan_after_refactor",
        executor=ShellExecutor(command=_cmd("aislop_scan", "--path", "{path}")),
    )

    Step(
        "compare_scores",
        model=MODEL,
        prompt=PROMPT_COMPARE,
    )

    # ── Phase 8: Decide ──

    Step(
        "has_improved",
        model=MODEL,
        prompt=(
            'Return ONLY "yes" or "no". '
            'Does the text below contain "VERDICT: IMPROVED"?\n\n{compare_scores}'
        ),
    )

    Conditional(
        condition="has_improved == 'yes'",
        then_steps=[
            Step(
                "store_result",
                executor=ShellExecutor(
                    command=_cmd(
                        "agentdb_store",
                        "--doc_id",
                        "refactor-{path}-{get_timestamp.stdout}",
                        "--domain",
                        "refactor_loop",
                        "--content",
                        "{compare_scores}",
                    )
                ),
            ),
        ],
        else_steps=[
            Step(
                "revert_code",
                executor=ShellExecutor(
                    command=[
                        PYTHON,
                        "-c",
                        (
                            "from pathlib import Path; "
                            "import sys; "
                            "target = sys.argv[1]; "
                            "bak = Path(target + '.bak'); "
                            "if bak.exists(): "
                            "    data = bak.read_text(encoding='utf-8'); "
                            "    Path(target).write_text(data, encoding='utf-8'); "
                            "    print(f'Reverted {target}'); "
                            "else: "
                            "    print(f'No backup for {target}')"
                        ),
                        "{get_target_name}",
                    ]
                ),
            ),
        ],
    )

    Step(
        "summary",
        model=MODEL,
        prompt=(
            "Summarize this refactoring session in 2-3 sentences.\n\n"
            "Target: {get_target_name}\n"
            "Baseline score: {aislop_scan_baseline.stdout}\n"
            "After refactor: {aislop_scan_after_refactor.stdout}\n"
            "Result: {compare_scores}\n"
            "Tests: {run_tests.stdout}\n\n"
            "Provide a concise summary."
        ),
    )

    return ctx
