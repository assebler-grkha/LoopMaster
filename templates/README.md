# LoopMaster Templates

Ready-to-use loop templates that you can customize for your project.

## Available Templates

### arch_debate (Architectural Debate)

Two AI agents debate an architectural proposal, producing an Architecture Decision Record (ADR).

**Files:**
- `scenario12_arch_debate.py` — the loop definition
- `storage_tool.py` — file-based document storage (replaces AgentDB)

## Quick Start

```bash
# 1. Copy templates to your project
cp -r templates/ my-project/loops/

# 2. Set your LLM model
export LOOPMASTER_OPENROUTER_MODEL="gpt-4o-mini"  # or your preferred model

# 3. Run the loop
loopmaster run my-project/loops/scenario12_arch_debate.py
```

Or via Python:

```python
from loopmaster import LoopEngine
from loops.scenario12_arch_debate import arch_debate

engine = LoopEngine()
engine.register(arch_debate)
result = engine.run(arch_debate, initial_context={
    "goal": "Design a caching layer for our API",
    "project": "my-project",
})
```

## Customization

### 1. Change the LLM Model

Edit the `MODEL` variable in the loop file, or set the environment variable:

```bash
export LOOPMASTER_OPENROUTER_MODEL="anthropic/claude-3-opus"
```

### 2. Connect Your Codebase Search

Edit the `_search_codebase()` function in the loop file. Options:

**Option A: codebase-memory MCP** (if you have it installed)
```python
def _search_codebase(query, project):
    return ["python", "mcp_tool.py", "search_graph",
            "--query", query, "--project", project, "--limit", "10"]
```

**Option B: ripgrep** (fast text search)
```python
def _search_codebase(query, project):
    return ["rg", "--json", "-l", query, f"src/"]
```

**Option C: Custom API**
```python
def _search_codebase(query, project):
    return ["curl", "-s", f"https://your-api/search?q={query}"]
```

### 3. Connect Your Source Reader

Edit the `_read_source()` function. Options:

**Option A: codebase-memory MCP**
```python
def _read_source(qualified_name, project):
    return ["python", "mcp_tool.py", "get_snippet",
            "--qualified_name", qualified_name, "--project", project]
```

**Option B: cat** (simple file read)
```python
def _read_source(qualified_name, project):
    path = qualified_name.replace(".", "/") + ".py"
    return ["cat", f"src/{path}"]
```

### 4. Customize ADR Format

Edit `PROMPT_JUDGE` to change the output format. The default produces:

```markdown
# ADR: <title>
## Status: Accepted
## Context
## Decision
## Alternatives Considered
## Consequences
## Implementation Cost
## Final Score
```

### 5. Use External Database

Replace `storage_tool.py` calls with your own database:

```python
def _store_doc(doc_id, domain, content):
    # MongoDB
    return ["python", "-c", f"db.docs.insert_one({{...'\\}})"]
    # PostgreSQL
    return ["psql", "-c", f"INSERT INTO docs VALUES ('{doc_id}', ...)"]
    # REST API
    return ["curl", "-X", "POST", "https://your-api/docs", "-d", "..."]
```

## Storage

By default, documents are stored as JSON files in `.loopmaster/decisions/`:

```
.loopmaster/decisions/
  adr-my-project-1234567890.json
  arch_question-my-project-1234567890.json
```

Each file contains:
```json
{
  "id": "adr-my-project-1234567890",
  "domain": "arch_debate",
  "content": "# ADR: ...",
  "metadata": {},
  "created_at": "2026-08-24T12:00:00"
}
```

To search stored documents:

```bash
python templates/storage_tool.py search --query "auth" --domain "arch_debate"
python templates/storage_tool.py get --doc_id "adr-my-project-1234567890"
```

## Architecture

```
Phase 1: Context Gathering (Steps 1-5)
  ├── get_timestamp
  ├── gather_context (your search tool)
  ├── Conditional: enough context?
  │   └── Else: ask question + store
  ├── extract_first_name
  ├── read_sources (your source reader)
  └── check_adrs (file storage)

Phase 2: Architect A (Steps 6-9)
  ├── architect_a_prepare
  ├── has_question (boolean flag)
  ├── Conditional: question asked?
  │   └── Then: store question
  └── architect_a_propose

Phase 3: Architect B (Steps 10-14)
  ├── architect_b_critique
  ├── has_reject (boolean flag)
  └── Conditional: rejected?
      ├── Then: revise + recheck + context
      └── Else: context (no revision)

Phase 4: Judge (Steps 15-17)
  ├── judge_verdict
  ├── store_adr (file storage)
  └── summary
```

## Requirements

- Python 3.10+
- LoopMaster installed (`pip install loopmaster`)
- OpenRouter API key (for LLM calls)

```bash
export LOOPMASTER_LLM_PROVIDER=openrouter
export LOOPMASTER_OPENROUTER_API_KEY=sk-or-v1-...
```
