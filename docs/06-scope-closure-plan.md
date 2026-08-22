# LoopMaster — Scope Closure Plan

## Status: Working Alpha → Production Beta

This document enumerates every remaining gap between the current implementation and the architecture spec. Items are grouped by priority and module.

---

## P0 — Blocking (Must Fix for Release)

### 1. Dead Dependencies in `pyproject.toml`
- `pydantic>=2.0` — listed but **never imported**. All dataclasses use stdlib `@dataclass`.
- `structlog>=23.0` — listed but **never imported**. Code uses stdlib `logging`.
- **Action:** Either remove these deps (cleanest) or migrate dataclasses to Pydantic models and logging to structlog. Decision needed.

### 2. Missing `cli/__init__.py`
- The `cli/` package has no `__init__.py`. The entry point `loopmaster.cli.app:main` works via `pyproject.toml` direct reference, but `import loopmaster.cli` may fail depending on packaging.
- **Action:** Create `src/loopmaster/cli/__init__.py` with a re-export of `main`.

### 3. Missing `README.md`
- `pyproject.toml` references `readme = "README.md"` but the file doesn't exist.
- **Action:** Create `README.md` with installation, quickstart, CLI usage, and links to docs.

---

## P1 — Core Feature Gaps

### 4. Events Module (currently empty stub)
- **Planned in:** `01-architecture.md`, `03-metrics.md`
- **What exists:** `src/loopmaster/events/__init__.py` — 1-line docstring only.
- **What's needed:**
  - `events/models.py` — `LoopEvent` Pydantic/dataclass model (event_type, loop_name, step_name, timestamp, payload, metadata).
  - `events/emitter.py` — `EventEmitter` class: subscribe/dispatch pattern, optional OTel span integration.
  - Integration with `engine.py` — emit events on step_start, step_end, step_retry, loop_start, loop_end, checkpoint_save, error.
  - Integration with `metrics/collector.py` — emitter feeds metrics collector.

### 5. Utils Module (currently empty stub)
- **Planned in:** `01-architecture.md`
- **What exists:** `src/loopmaster/utils/__init__.py` — 1-line docstring only.
- **What's needed:**
  - `utils/llm.py` — LLM provider abstraction: `LLMProvider` ABC with `call(prompt, model, **kwargs) -> LLMResponse`. Concrete implementations: `OpenAIProvider`, `AnthropicProvider`, `MockProvider` (for testing).
  - `utils/serialization.py` — helpers for StepOutput/context serialization: `serialize_step_output()`, `deserialize_step_output()`, JSON-safe encoders for datetime/Path/etc.
  - `utils/hashing.py` — content hashing for checkpoint filenames (currently inline in checkpoint manager).

### 6. Recovery Module (currently empty stub)
- **Planned in:** `01-architecture.md`
- **What exists:** `src/loopmaster/recovery/__init__.py` — 1-line docstring only.
- **What's needed:**
  - Recovery logic currently lives in `core/types.py` (ErrorPolicy, RecoveryAction) and `core/engine.py` (retry loop, interruption handler).
  - **Option A:** Extract into `recovery/` for cleaner separation. `recovery/error_policy.py` = ErrorPolicy + classify logic. `recovery/interruption.py` = InterruptionProtection + heartbeat.
  - **Option B:** Keep consolidated in core, delete the stub. The current structure works fine.
  - **Decision needed:** Which approach?

### 7. CursorAdapter
- **Planned in:** `04-agent-interaction.md`
- **What exists:** `OpenCodeAdapter`, `ClaudeCodeAdapter`, `CustomAdapter` — but no `CursorAdapter`.
- **What's needed:**
  - `agents/cursor_adapter.py` — `CursorAdapter(AgentAdapter)` implementing discover (find `.cursor/` dir), read_config (`.cursor/settings.json`), read_system_prompt (`.cursorrules`), write_config, inject_loop_context, validate_config, restore_original.
  - Register in `AgentRegistry`.

### 8. Template Code Generation
- **Planned in:** `01-architecture.md`
- **What exists:** `templates/__init__.py` has `TEMPLATES` dict with 7 string descriptions only. No actual code generation.
- **What's needed:**
  - Each template should produce a complete, runnable Python file with `@Loop` decorator, steps, context, etc.
  - Templates: `reflection`, `tool_use`, `planning`, `multi_agent`, `critique`, `escalation`, `hybrid`.
  - `get_template(name)` should return executable Python source code, not just a description.
  - CLI `init` command should use these templates instead of hardcoded inline string.

---

## P2 — Observability & Export

### 9. SQLite Metrics Exporter
- **Planned in:** `03-metrics.md`
- **What exists:** `MetricsCollector` saves to JSON only.
- **What's needed:**
  - `metrics/sqlite_exporter.py` — `SQLiteExporter(collector, db_path)`: creates tables (loops, steps, retries), batch insert, query helpers (by_loop, by_model, time_range, cost_trend).
  - CLI integration: `loop-engine metrics export --format sqlite`.

### 10. OTel/Prometheus Exporters
- **Planned in:** `03-metrics.md`, `events/`
- **What exists:** Nothing. `opentelemetry` is listed as optional dep but unused.
- **What's needed:**
  - `metrics/otel_exporter.py` — `OTelExporter(collector)`: creates OTel spans for loops/steps, sets attributes (cost, tokens, model), records metrics via OTel Meter.
  - `metrics/prometheus_exporter.py` — `PrometheusExporter(collector)`: exposes `/metrics` endpoint with counters (steps_total, retries_total), histograms (step_duration_ms), gauges (active_loops, total_cost).
  - Events module integration: OTel span creation from LoopEvent.

### 11. YAML Export Improvements
- **Planned in:** `02-dsl-spec.md`
- **What exists:** Basic YAML export via `LoopDef.to_yaml()`.
- **What's needed:**
  - Support Parallel groups in YAML.
  - Support ErrorPolicy and budget in YAML.
  - CLI command: `loop-engine export --format yaml`.
  - Import from YAML (currently export-only).

---

## P3 — CLI Enhancements

### 12. CLI `docs` Command
- **Planned in:** `01-architecture.md`
- **What exists:** No `docs` command.
- **What's needed:**
  - `loop-engine docs` — opens generated HTML docs in browser, or prints markdown to stdout.
  - `loop-engine docs --format html --output ./docs/` — generates static site.

### 13. CLI `commands/` Subpackage
- **Planned in:** `01-architecture.md`
- **What exists:** All CLI logic in single `app.py` (261 lines).
- **What's needed (optional, low priority):**
  - Refactor into `cli/commands/init.py`, `validate.py`, `run.py`, `checkpoints.py`, `templates.py`, `docs.py`.
  - Each command in its own file, imported by `app.py`.
  - Add `progress.py` for Rich progress bars during long runs.

### 14. CLI Progress Bars
- **Planned in:** `01-architecture.md`
- **What exists:** No progress indication during `loop-engine run`.
- **What's needed:**
  - Rich progress bar showing: current step / total steps, elapsed time, estimated cost.
  - Integration with `MetricsCollector` for real-time cost display.

---

## P4 — Documentation & ADRs

### 15. ADR Individual Files
- **Planned in:** `01-architecture.md`
- **What exists:** `docs/adr/README.md` with 10 ADRs in summary form.
- **What's needed:**
  - Create individual files: `docs/adr/001-python-dsl.md`, `002-runtime-interpreter.md`, `003-data-only-checkpoints.md`, etc.
  - Each with Status, Context, Decision, Consequences, Alternatives Considered.

### 16. API Reference Docs
- **What's needed:**
  - Auto-generated from docstrings via `mkdocs` + `mkdocstrings` or `sphinx`.
  - Add to pyproject.toml optional deps.

---

## P5 — Testing Gaps

### 17. Tests for Stub/Empty Modules
- `events/` — no tests (no code to test yet).
- `utils/` — no tests (no code to test yet).
- `recovery/` — no tests (no code to test yet).
- **Action:** Write tests alongside implementation.

### 18. Integration Tests
- **What exists:** Unit tests for each module.
- **What's needed:**
  - End-to-end test: define loop → export YAML → run → checkpoint → resume → verify.
  - Agent adapter integration test: mock file system, run adapter operations, verify config files.
  - MCP protocol test: client connects, calls tools, receives events.

### 19. CLI Tests
- **What exists:** No CLI tests.
- **What's needed:**
  - Test each CLI command via `typer.testing.CliRunner`.
  - Test `init` creates correct file structure.
  - Test `validate` catches bad loop definitions.
  - Test `run --resume` loads checkpoint and continues.

---

## P6 — Code Quality

### 20. Remove Dead Imports/Dependencies
- After P0 fix (pydantic/structlog), scan for any other unused imports.
- `ruff check --select F401` to find unused imports.

### 21. Type Coverage
- Currently mypy passes but some functions use `Any` freely.
- Add stricter type annotations to public APIs.
- Consider adding `py.typed` marker.

### 22. Docstrings
- Many functions lack docstrings.
- Add Google-style docstrings to all public classes and methods.

---

## Execution Order

| Phase | Items | Estimated Effort |
|-------|-------|-----------------|
| **Phase 1: Polish** | #1, #2, #3, #20 | 1 hour |
| **Phase 2: Events + Utils** | #4, #5, #17 | 3-4 hours |
| **Phase 3: Agents + Templates** | #7, #8 | 2-3 hours |
| **Phase 4: Observability** | #9, #10, #11 | 3-4 hours |
| **Phase 5: CLI** | #12, #13, #14, #19 | 2-3 hours |
| **Phase 6: Docs** | #15, #16, #22 | 2 hours |
| **Phase 7: Integration Tests** | #18 | 2 hours |

**Total estimated: 15-20 hours of focused work.**

---

## Open Decisions (Need User Input)

1. **pydantic/structlog:** Remove or migrate to? (P0 #1)
2. **Recovery module:** Extract from core or keep consolidated? (P1 #6)
3. **Scope:** Implement everything, or ship alpha with just P0 fixes? (P1-P6 are enhancements)
4. **LLM abstraction:** Is `utils/llm.py` in scope, or will LoopMaster remain LLM-agnostic (user provides their own)? (P1 #5)
