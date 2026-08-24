# Loop Architecture: arch_debate

## Problem it solves

Architectural decisions are often made by a single person without structured push-back. This leads to:
- Over-engineering (someone proposes a complex solution when simpler exists)
- Under-engineering (someone misses edge cases)
- Bias (one person's preferred stack clouds judgment)
- Lost context (decisions made verbally, never documented)

**arch_debate** simulates a structured architectural debate between two AI agents (Architect A and Architect B) with a neutral Judge, producing an Architecture Decision Record (ADR) as the final output.

---

## Design Decisions (approved by user)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Debate rounds | **1 round** | Keep it fast; multiple rounds add cost without proportional value |
| Style selection | **LLM decides** based on task complexity | Creative/bold for greenfield; critical/conservative for legacy |
| Step storage | **Every step** saved to agentdb | Full audit trail for analysis |
| Open questions | **I react** between sessions | agentdb_search for `status: open` questions |
| Condition safety | **Boolean flag steps** | Avoids AST parse errors from apostrophes in LLM output |

---

## Architecture (14-18 steps + 3 conditionals)

```
Phase 1: Context Gathering
═══════════════════════════

Step 1: get_timestamp (ShellExecutor)
  Command: python -c "import time; print(int(time.time()))"
  Purpose: Generate unique timestamp for doc_id

Step 2: gather_context (ShellExecutor -> mcp_tool.py search_graph)
  Command: python mcp_tool.py search_graph --query "{goal}" --project {project} --limit 10
  Purpose: Find relevant code structures (functions, classes, routes)
  Output: {gather_context.stdout} -- JSON with qualified_names

Conditional 1: check_context ("len('{{gather_context.stdout}}') > 50")
  Then: proceed
  Else: Step 2b -- ask clarifying question + store in agentdb

Step 3: extract_first_name (LLMStep)
  Purpose: Extract first qualified_name from search results

Step 4: read_sources (ShellExecutor -> mcp_tool.py get_snippet)
  Command: python mcp_tool.py get_snippet --qualified_name "{extracted_name}" --project {project}
  Purpose: Read actual source code for context

Step 5: check_adrs (ShellExecutor -> mcp_tool.py agentdb_search)
  Command: python mcp_tool.py agentdb_search --query "adr {project}" --domain "arch_debate"
  Purpose: Find previous architectural decisions


Phase 2: Architect A -- Proposal
════════════════════════════════

Step 6: architect_a_prepare (LLMStep)
  Purpose: Prepare analysis, may ask clarifying question
  Output: text starting with PROPOSAL: or QUESTION:

Step 7: has_question (LLMStep)
  Prompt: Return ONLY "yes" or "no": does the text start with "QUESTION:"?

Conditional 2: check_question ("has_question == 'yes'")
  Then: Step 8 -- store_question (agentdb_store)
  Else: (skip)

Step 9: architect_a_propose (LLMStep)
  Purpose: Produce concrete architectural proposal


Phase 3: Architect B -- Critique
════════════════════════════════

Step 10: architect_b_critique (LLMStep)
  Purpose: Critique proposal, find risks and flaws
  Output: score 0-100 + APPROVE/REJECT/APPROVE_WITH_CHANGES

Step 11: has_reject (LLMStep)
  Prompt: Return ONLY "yes" or "no": does the text contain REJECT?

Conditional 3: check_reject ("has_reject == 'yes'")
  Then:
    Step 12: architect_a_revise -- revise proposal
    Step 13: architect_b_recheck -- re-evaluate
    Step 14: revision_context -- summarize revision
  Else:
    Step 14: revision_context -- "No revision needed"


Phase 4: Judge Verdict + Storage
════════════════════════════════

Step 15: judge_verdict (LLMStep)
  Purpose: Produce final Architecture Decision Record (ADR)

Step 16: store_adr (ShellExecutor -> mcp_tool.py agentdb_store)
  Purpose: Persist the ADR for future reference

Step 17: summary (LLMStep)
  Purpose: Summarize debate in 3-5 sentences
```

### Step count by execution path

| Path | Context | QUESTION | REJECT | Steps |
|------|---------|----------|--------|-------|
| A (happy) | long | no | no | 14 |
| B | short | no | no | 16 |
| C | long | no | yes | 16 |
| D | long | yes | no | 15 |
| E | short | yes | no | 17 |
| F | short | no | yes | 17 |
| G | long | yes | yes | 18 |
| H | short | yes | yes | 19 |

---

## Bug fixes applied

| Bug | Root cause | Fix |
|-----|-----------|-----|
| C1: undefined `{revision_context}` | Steps named `set_revision_yes/no` | Renamed both to `revision_context` |
| C2: conditions break on apostrophes | `'X' in '{{var}}'` -- apostrophe in LLM text breaks AST | Added boolean flag steps (`has_question`, `has_reject`) |
| C3: budget overflow | Budget(max_steps=14) < max path (19) | Changed to Budget(max_steps=20) |
| C4: ask_context_question discarded | No agentdb_store after question generation | Added `store_context_question` step |
| W2: no ErrorPolicy | Doc said ErrorPolicy, code didn't | Added ErrorPolicy(retry=2, FALLBACK, @smart) |

---

## AgentDB Storage Standard

### ADR Document
```
doc_id:    adr-{project_name}-{timestamp}
domain:    arch_debate
content:   Full ADR text (markdown)
```

### Open Questions
```
doc_id:    arch_question-{project_name}-{timestamp}
domain:    arch_debate_questions
content:   Full question text from Architect A
```

### Search Patterns
```bash
# Find ADRs for a project
python tests/mcp_tool.py agentdb_search --query "adr project-name" --domain "arch_debate"

# Find open questions
python tests/mcp_tool.py agentdb_search --query "arch_question" --domain "arch_debate_questions"
```

---

## When to trigger

| Trigger | Example |
|---------|---------|
| New feature design | "Спроектируй систему аутентификации для нашего API" |
| Refactoring decision | "Стоит ли выносить парсинг в отдельный модуль?" |
| Technology choice | "PostgreSQL или MongoDB для этого модуля?" |
| Architecture review | "Проведи архитектурное ревью модуля X" |
| Technical debt | "Как лучше рефакторить payment processing?" |

---

## Budget & Error Handling

```python
@Loop(
    name="arch_debate",
    version="1.0.0",
    budget=Budget(max_steps=20),
    error_policy=ErrorPolicy(
        retry=2,
        on_failure=RecoveryAction.FALLBACK,
        fallback_model="@smart",
    ),
)
```

---

## Key Project Paths

| Resource | Path |
|----------|------|
| LoopMaster project | `C:/Projects/Ideas/LoopMaster` |
| codebase-memory project | `C-Projects-Ideas-LoopMaster` |
| agentdb SQLite | `C:/Users/Gregory/.opencode-mcp/pathfinder-app/.agentdb/pathfinder.db` |
| MCP bridge | `tests/mcp_bridge.py` |
| CLI wrapper | `tests/mcp_tool.py` |
| OpenRouter env | `LOOPMASTER_LLM_PROVIDER=openrouter`, model `stealth/ox-alpha` |
