# LoopMaster Loop Catalog

## Overview

Six production-ready loops for software engineering projects. Each loop solves a specific class of problems using multi-step LLM pipelines with tool execution, conditional branching, and error handling.

---

## 1. `security_audit` — Security Audit

### Problem it solves

Developers ship code with hardcoded secrets, outdated dependencies, and known vulnerabilities. Manual security review is slow, inconsistent, and misses edge cases. By the time a human reviews, the code is already in production.

### What it does

1. Scans source files for hardcoded secrets (passwords, API keys, tokens)
2. Checks dependencies for known CVEs (`pip-audit` / `npm audit`)
3. Runs quality scanner (`aislop scan`) for security-relevant patterns
4. LLM classifies findings by severity (critical / high / medium / low)
5. Generates actionable report with remediation steps
6. Stores results in AgentDB for historical tracking

### When to trigger

- Before `git push` or CI/CD pipeline
- After adding new dependencies
- Periodic (weekly/monthly) project health check
- Before production deployment

### Conditional logic

- If critical findings → extra step with detailed exploitation analysis
- If zero findings → "clean" record stored

### Key tools used

`ShellExecutor` (grep, pip-audit, aislop), `LLMStep` (classification, report), `agentdb_store`

---

## 2. `doc_generator` — Documentation Generator

### Problem it solves

API documentation drifts out of sync with code. Developers forget to update README, docstrings become stale, and new contributors can't understand the codebase. Writing docs manually is tedious and often skipped.

### What it does

1. Discovers all public functions/classes via codebase-memory graph
2. Reads source code and existing docstrings
3. LLM generates or updates markdown documentation
4. Writes to `docs/api/` directory
5. Validates output format

### When to trigger

- After merging PRs that change public APIs
- Before releases
- On-demand when docs are requested

### Conditional logic

- If docstring exists → update (preserve intent, add examples)
- If docstring missing → generate from scratch
- If file exists → diff and merge; if not → create new

### Key tools used

`ShellExecutor` (codebase-memory CLI), `LLMStep` (doc generation), file write

---

## 3. `changelog_gen` — Changelog Generator

### Problem it solves

Maintaining CHANGELOG.md manually is forgotten or done poorly. Users and contributors can't see what changed between versions. Release notes are either missing or copy-pasted from git log without context.

### What it does

1. Reads recent git history (`git log --oneline -50`)
2. Gets diff statistics (`git diff --stat`)
3. LLM groups commits by type (feat / fix / chore / docs / refactor)
4. Formats into Keep-a-Changelog markdown
5. Writes or appends to CHANGELOG.md

### When to trigger

- Before creating a git tag / release
- After merging a batch of PRs
- On-demand

### Conditional logic

- If CHANGELOG.md exists → append new version section at top
- If not → create with proper header and previous versions

### Key tools used

`ShellExecutor` (git), `LLMStep` (grouping, formatting), file write

---

## 4. `arch_debate` — Architectural Debate

### Problem it solves

Architecture decisions are made by one person without adversarial critique. Bad patterns propagate. Design flaws are found in production, not in design phase. Solo developers lack a "second opinion" on structural decisions.

### What it does

1. Agent A analyzes codebase and proposes architecture changes
2. Agent B critiques the proposal (risks, alternatives, counter-arguments)
3. Agent A responds to criticism, produces revised proposal
4. Agent B validates revision or raises final objections
5. Judge agent delivers verdict and action items

### When to trigger

- Before major refactoring
- When adding new module/service
- When onboarding to unfamiliar codebase
- Periodic architecture health check

### Conditional logic

- If objections above threshold → loop back to revision (max 3 rounds)
- If consensus reached → store decision in agentdb as ADR

### Key tools used

`LLMStep` (debate agents), `ShellExecutor` (codebase-memory for context), `agentdb_store` (ADR storage)

### Budget protection

`Budget(max_steps=10)` prevents infinite debate loops.

---

## 5. `full_tester` — Full Test Suite Runner

### Problem it solves

Running tests manually and interpreting failures is time-consuming. Flaky tests obscure real bugs. Developers skip tests because fixing them feels overwhelming. There's no systematic way to triage test failures.

### What it does

1. Collects test inventory (`pytest --collect-only`)
2. Runs unit tests with fail-fast (`pytest -x`)
3. Runs quality scan (`aislop scan`)
4. If failures exist → LLM diagnoses root cause
5. Runs failing tests again with verbose output for details
6. LLM recommends specific fixes

### When to trigger

- Before commit / push
- After code changes
- CI pipeline failure triage

### Conditional logic

- If all tests pass → store "clean" record, generate summary
- If tests fail → enter diagnosis loop (max 3 iterations)

### Budget protection

`Budget(max_steps=15)` prevents infinite diagnosis loops.

### Key tools used

`ShellExecutor` (pytest, aislop), `LLMStep` (diagnosis, recommendations)

---

## 6. `refactor_loop` — Automated Refactoring

### Problem it solves

Code accumulates complexity, duplicates, and anti-patterns. Developers know refactoring is needed but avoid it because it's risky and tedious. There's no automated way to safely improve code quality incrementally.

### What it does

1. Runs `aislop scan` to identify findings (complexity, duplicates, naming)
2. LLM prioritizes by impact and risk
3. Reads target function/class via codebase-memory
4. LLM produces refactored version
5. Writes to file
6. Runs tests to verify nothing broke
7. Compares aislop score before/after
8. If score improved → keep; if not → revert

### When to trigger

- Periodic code health sprints
- Before release (quality gate)
- When aislop score drops below threshold

### Conditional logic

- If score improved → commit refactored code
- If score unchanged → skip, try next finding
- If score degraded → revert and try different approach
- If all findings addressed → store summary

### Budget protection

`Budget(max_steps=25)` limits iterations.

### Key tools used

`ShellExecutor` (aislop, pytest, codebase-memory), `LLMStep` (analysis, refactoring), file read/write

---

## Cross-cutting concerns

| Concern | How handled |
|---|---|
| Error recovery | `ErrorPolicy(retry=2, on_failure=FALLBACK)` |
| Cost control | `Budget(max_steps, max_cost)` per loop |
| Persistence | AgentDB stores all findings, decisions, reports |
| Observability | OTel spans on every step |
| Context flow | Template variables: `{step.output}`, `{step.stdout}` |

---

## Usage from MCP

All loops are discovered automatically by `loop_list`. Execute via:

```
loop_run("security_audit", context={"path": "/path/to/project"})
```

Proactive triggering rules are defined in `docs/AGENT_GUIDE.md` section 2.

### JSON (LoopSpec v1) versions

Loops can also be stored as declarative JSON specs (`LoopSpec v1`, ADR-011) and run detached via `loop_run(spec_json=..., mode="detached")`. Any Python loop can be compiled: `loop-engine export loop.py --format json -o loop.json`.

Compiled examples shipped in `loops/`:

| File | Compiled from | Notes |
|------|---------------|-------|
| `scenario1_simple_pipeline.json` | `scenario1_simple_pipeline.py` | 3 LLM steps, validates clean |
| `scenario7_shell_pipeline.json` | `scenario7_shell_pipeline.py` | Shell-only — runs without any LLM keys |

---

## Templates

Ready-to-use loop templates in `templates/` directory. Customize for your project.

| Template | Description | Customization |
|----------|-------------|---------------|
| `scenario12_arch_debate.py` | Architectural debate producing ADRs | Connect your codebase search and source reader |

### Quick start

```bash
cp templates/scenario12_arch_debate.py my-project/loops/
# Edit _search_codebase() and _read_source() in the copied file
loopmaster run my-project/loops/scenario12_arch_debate.py
```

See `templates/README.md` for full customization guide.
