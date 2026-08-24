"""Scenario 12: Architectural Debate Loop.

Two AI agents (Architect A and Architect B) debate an architectural proposal.
A neutral Judge produces an Architecture Decision Record (ADR).

Phases:
  1. Context Gathering — search codebase, read sources, check previous ADRs
  2. Architect A Proposal — creative/bold proposal (may ask clarifying questions)
  3. Architect B Critique — critical/conservative critique with score
  4. Judge Verdict — produces final ADR, stores in agentdb

Trigger: user asks for architectural review, technology choice, or design decision.

Usage:
  loop_run("arch_debate", context={"goal": "...", "project": "..."})
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

PROMPT_A_PREPARE = """\
You are Architect A — a creative, bold architect who favors innovation.

GOAL: {goal}
PROJECT: {project}

CODEBASE CONTEXT:
{gather_context.stdout}

SOURCE CODE (if available):
{read_sources.stdout}

PREVIOUS ADRs (if any):
{check_adrs.stdout}

Based on this context, prepare your analysis.
If you need more information, ask a specific clarifying question.

Start your response with either:
- "PROPOSAL:" if you have enough context to propose a solution
- "QUESTION:" if you need clarification from the user

If asking a question, be specific about what you need to know.
Keep questions short (1-2 sentences)."""

PROMPT_A_PROPOSE = """\
You are Architect A — creative and bold.

GOAL: {goal}

Available context (from preparation):
{architect_a_prepare}

Now produce a CONCRETE architectural proposal. Address:
1. What solution do you propose?
2. Why is this the best approach?
3. What are the tradeoffs?
4. Estimated implementation cost: S (< 1 day), M (1-3 days), L (> 3 days)

Output format:
PROPOSAL: <your concrete proposal>
RATIONALE: <why this approach>
TRADEOFFS: <what you gain and lose>
COST: <S/M/L>"""

PROMPT_B_CRITIQUE = """\
You are Architect B — a critical, conservative architect who finds risks and flaws.

GOAL: {goal}

PROPOSAL from Architect A:
{architect_a_propose}

SOURCE CODE:
{read_sources.stdout}

Critique this proposal thoroughly. Specifically address:
1. Risks and failure modes
2. Missing edge cases or scenarios
3. Simpler alternatives that achieve the same goal
4. Scalability and maintainability concerns
5. Cost estimate accuracy

Score the proposal 0-100 (0 = fundamentally flawed, 100 = perfect).
End your response with EXACTLY ONE of these words on a new line:
APPROVE
REJECT
APPROVE_WITH_CHANGES"""

PROMPT_A_REVISE = """\
You are Architect A. Architect B rejected your proposal.

ORIGINAL PROPOSAL:
{architect_a_propose}

CRITICISM from Architect B:
{architect_b_critique}

Revise your proposal addressing ALL of Architect B's concerns.
Keep what's good, fix what's broken.

Output format:
REVISED PROPOSAL: <your revised proposal>
CHANGES MADE: <what you changed and why>
COST: <revised S/M/L estimate>"""

PROMPT_B_RECHECK = """\
You are Architect B. Architect A has revised their proposal.

REVISED PROPOSAL:
{architect_a_revise}

Original critique:
{architect_b_critique}

Re-evaluate the revised proposal:
1. Were all your concerns addressed?
2. Are there remaining issues?
3. Is this now acceptable?

Score 0-100.
End with EXACTLY ONE of: APPROVE or REJECT"""

PROMPT_SET_REVISION_YES = """\
The following is a revised architectural proposal. Return ONLY the text
"REVISED PROPOSAL EXISTS" followed by a colon and a one-line summary.

{architect_a_revise}"""

PROMPT_SET_REVISION_NO = """\
No revision was needed (the original proposal was approved).
Return ONLY the text: "No revision — original proposal approved." """

PROMPT_HAS_QUESTION = """\
Return ONLY "yes" or "no".
Does the text below start with "QUESTION:" (after stripping whitespace)?

{architect_a_prepare}"""

PROMPT_HAS_REJECT = """\
Return ONLY "yes" or "no".
Does the text below contain the word REJECT as a standalone word (not part of another word)?

{architect_b_critique}"""

PROMPT_JUDGE = """\
You are a neutral Architectural Judge. Produce the final decision.

GOAL: {goal}

ARCHITECT A PROPOSAL:
{architect_a_propose}

ARCHITECT B CRITIQUE:
{architect_b_critique}

REVISION STATUS:
{revision_context}

Produce an Architecture Decision Record (ADR):

# ADR: {goal}
## Status: Accepted
## Context
<describe the problem being solved>
## Decision
<state the final architectural decision, incorporating the best elements>
## Alternatives Considered
<list alternatives discussed>
## Consequences
<positive and negative consequences>
## Implementation Cost
<S/M/L with justification>
## Final Score
<0-100>"""


# ── Loop definition ─────────────────────────────────────────────


@Loop(
    name="arch_debate",
    version="1.0.0",
    budget=Budget(max_steps=20),
)
def arch_debate(ctx):
    # ── Phase 1: Context Gathering ──

    Step(
        "get_timestamp",
        executor=ShellExecutor(command=[PYTHON, "-c", "import time; print(int(time.time()))"]),
    )

    Step(
        "gather_context",
        executor=ShellExecutor(
            command=_cmd(
                "search_graph",
                "--query",
                "{goal}",
                "--project",
                "{project}",
                "--limit",
                "10",
            )
        ),
    )

    Conditional(
        condition="len('{{gather_context.stdout}}') > 50",
        then_steps=[],
        else_steps=[
            Step(
                "ask_context_question",
                model=MODEL,
                prompt=(
                    "The codebase search returned minimal results for project "
                    "{project}. Ask ONE specific clarifying question about what "
                    "code or components are relevant to this goal: {goal}"
                ),
            ),
            Step(
                "store_context_question",
                executor=ShellExecutor(
                    command=_cmd(
                        "agentdb_store",
                        "--doc_id",
                        "arch_question-{project}-{get_timestamp.stdout}",
                        "--domain",
                        "arch_debate_questions",
                        "--content",
                        "{ask_context_question}",
                    )
                ),
            ),
        ],
    )

    Step(
        "extract_first_name",
        model=MODEL,
        prompt=(
            "Given this JSON search result, extract the FIRST qualified_name "
            "field. Return ONLY the qualified name string, nothing else.\n\n"
            "{gather_context.stdout}"
        ),
    )

    Step(
        "read_sources",
        executor=ShellExecutor(
            command=_cmd(
                "get_snippet",
                "--qualified_name",
                "{extract_first_name}",
                "--project",
                "{project}",
            )
        ),
    )

    Step(
        "check_adrs",
        executor=ShellExecutor(
            command=_cmd(
                "agentdb_search",
                "--query",
                "adr {project}",
                "--domain",
                "arch_debate",
            )
        ),
    )

    # ── Phase 2: Architect A — Proposal ──

    Step(
        "architect_a_prepare",
        model=MODEL,
        prompt=PROMPT_A_PREPARE,
    )

    Step(
        "has_question",
        model=MODEL,
        prompt=PROMPT_HAS_QUESTION,
    )

    Conditional(
        condition="has_question == 'yes'",
        then_steps=[
            Step(
                "store_question",
                executor=ShellExecutor(
                    command=_cmd(
                        "agentdb_store",
                        "--doc_id",
                        "arch_question-{project}-{get_timestamp.stdout}",
                        "--domain",
                        "arch_debate_questions",
                        "--content",
                        "{architect_a_prepare}",
                    )
                ),
            ),
        ],
        else_steps=[],
    )

    Step(
        "architect_a_propose",
        model=MODEL,
        prompt=PROMPT_A_PROPOSE,
    )

    # ── Phase 3: Architect B — Critique ──

    Step(
        "architect_b_critique",
        model=MODEL,
        prompt=PROMPT_B_CRITIQUE,
    )

    Step(
        "has_reject",
        model=MODEL,
        prompt=PROMPT_HAS_REJECT,
    )

    Conditional(
        condition="has_reject == 'yes'",
        then_steps=[
            Step(
                "architect_a_revise",
                model=MODEL,
                prompt=PROMPT_A_REVISE,
            ),
            Step(
                "architect_b_recheck",
                model=MODEL,
                prompt=PROMPT_B_RECHECK,
            ),
            Step(
                "revision_context",
                model=MODEL,
                prompt=PROMPT_SET_REVISION_YES,
            ),
        ],
        else_steps=[
            Step(
                "revision_context",
                model=MODEL,
                prompt=PROMPT_SET_REVISION_NO,
            ),
        ],
    )

    # ── Phase 4: Judge + Storage ──

    Step(
        "judge_verdict",
        model=MODEL,
        prompt=PROMPT_JUDGE,
    )

    Step(
        "store_adr",
        executor=ShellExecutor(
            command=_cmd(
                "agentdb_store",
                "--doc_id",
                "adr-{project}-{get_timestamp.stdout}",
                "--domain",
                "arch_debate",
                "--content",
                "{judge_verdict}",
            )
        ),
    )

    Step(
        "summary",
        model=MODEL,
        prompt=(
            "Summarize this architectural debate in 3-5 sentences.\n\n"
            "Goal: {goal}\n"
            "Final ADR:\n{judge_verdict}\n\n"
            "Storage: {store_adr.stdout}\n\n"
            "Provide a concise summary with the final score and "
            "implementation cost estimate."
        ),
    )

    return ctx
