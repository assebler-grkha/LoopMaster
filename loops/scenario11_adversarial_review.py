"""Scenario 11: Adversarial Code Review Loop.

Uses MCP tools (codebase-memory, aislop, agentdb) to:
1. Search codebase for a function/class
2. Read its source code
3. Scan for quality issues
4. LLM performs adversarial analysis
5. Store findings in agentdb
6. Generate report

Trigger: user asks for code review of a specific component.
"""

import sys
from pathlib import Path

from loopmaster import Loop, ShellExecutor, Step

MODEL = "stealth/ox-alpha"
BRIDGE = str(Path(__file__).resolve().parent.parent / "tests" / "mcp_tool.py")
PROJECT = "C-Projects-Ideas-LoopMaster"
ACTUAL_DIR = str(Path(__file__).resolve().parent.parent)

SEARCH_CMD = [
    sys.executable,
    BRIDGE,
    "search_graph",
    "--query",
    "{search_code}",
    "--project",
    PROJECT,
    "--limit",
    "5",
]

SNIPPET_CMD = [
    sys.executable,
    BRIDGE,
    "get_snippet",
    "--qualified_name",
    "{extract_name}",
    "--project",
    PROJECT,
]

SCAN_CMD = [
    sys.executable,
    BRIDGE,
    "aislop_scan",
    "--path",
    ACTUAL_DIR,
]

STORE_CMD = [
    sys.executable,
    BRIDGE,
    "agentdb_store",
    "--doc_id",
    "review-{extract_name}",
    "--domain",
    "code-review",
    "--content",
    "{analyze}",
]


@Loop(name="test_adversarial_review", version="1.0.0")
def test_adversarial_review(ctx):
    Step(
        "search_code",
        model=MODEL,
        prompt=(
            "Return ONLY a single search query (1-3 words) for finding a "
            "function or class to review in a Python codebase. Examples: "
            "'resolve_prompt', 'LoopEngine', 'ErrorPolicy'. "
            "No explanation, just the search term."
        ),
    )

    Step(
        "find_in_graph",
        executor=ShellExecutor(command=SEARCH_CMD),
    )

    Step(
        "extract_name",
        model=MODEL,
        prompt=(
            "Given this JSON search result, extract the FIRST qualified_name "
            "field. Return ONLY the qualified name string, nothing else.\n\n"
            "{find_in_graph.stdout}"
        ),
    )

    Step(
        "read_code",
        executor=ShellExecutor(command=SNIPPET_CMD),
    )

    Step(
        "scan_quality",
        executor=ShellExecutor(command=SCAN_CMD),
    )

    Step(
        "analyze",
        model=MODEL,
        prompt=(
            "You are an adversarial code reviewer. Given the source code and "
            "quality scan results below, produce a structured review.\n\n"
            "SOURCE CODE ({extract_name}):\n{read_code.stdout}\n\n"
            "QUALITY SCAN:\n{scan_quality.stdout}\n\n"
            "Format your response as:\n"
            "SCORE: <0-100>\n"
            "ISSUES: <count>\n"
            "FINDINGS:\n"
            "- [SEVERITY: HIGH|MEDIUM|LOW] <description>\n"
            "VERDICT: <APPROVE|REJECT|APPROVE_WITH_CHANGES>"
        ),
    )

    Step(
        "store_findings",
        executor=ShellExecutor(command=STORE_CMD),
    )

    Step(
        "report",
        model=MODEL,
        prompt=(
            "Summarize this adversarial code review in 3-5 sentences.\n\n"
            "Function reviewed: {extract_name}\n"
            "Review results:\n{analyze}\n"
            "Storage: {store_findings.stdout}\n\n"
            "Provide a concise verdict."
        ),
    )

    return ctx
