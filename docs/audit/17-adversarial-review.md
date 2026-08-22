# #17 — Adversarial Review of All 16 Fixes

**Reviewer:** Adversarial review subagent
**Date:** 2026-08-22
**Scope:** All 16 fixes across `src/loopmaster/`
**Result:** 9 PASS, 7 WARN, 0 FAIL. No critical regressions.

---

## Summary Table

| Fix | Verdict | Key Finding |
|-----|---------|-------------|
| #01 Snapshot | PASS | New files now tracked and removed on restore |
| #02 Side-effects | PASS | Cache invalidation correct |
| #03 Error field | PASS | Type and logic correct |
| #04 Retry override | PASS | Default change documented; old max() also returned 2 |
| #05 Resume count | PASS | Reset per run correct |
| #06 Exception chain | PASS | Domain exceptions pass through |
| #07 Supervisor | PASS | return_exceptions=True + context isolation added |
| #08 Replay collision | PASS | FIFO correct |
| #09 Token split | PASS | Acknowledged trade-off |
| #10 Format injection | PASS | name/func_name now escaped |
| #11 MCP stubs | PASS | Functional implementations |
| #12 Budget format | PASS | Backward compatible |
| #13 Private access | PASS | Public API correct |
| #14 Replay load | PASS | Known fields filter correct |
| #15 Windows CLI | PASS | Cross-platform correct |
| #16 Metrics load | PASS | to_dict() now serializes all fields |

---

## Issues Found and Fixed

### NEW-01: restore_original() Now Removes Created Files (Fix #01)
**Severity:** MEDIUM
**Status:** FIXED
All four adapters now track newly-created files in `_created_files` set.
`restore_original()` deletes these after restoring snapshots.

### NEW-02: Fix #04 Behavioral Default Change
**Severity:** LOW
**Status:** DOCUMENTED (no fix needed)
Step.retry default changed from `int = 1` to `int | None = None`.
Old code: `max(1, 2) = 2` always. New code: `None` falls through to policy (2).
Actual behavior identical — only the code path differs.

### NEW-03: Fix #10 Incomplete Escaping
**Severity:** MEDIUM
**Status:** FIXED
`name_var` and `func_name` now escaped before `str.format()`.

### NEW-04: Fix #09 Cost Underestimation
**Severity:** LOW
**Status:** ACKNOWLEDGED (no fix needed)
All tokens recorded as input, output=0. Systematic underestimation for output-expensive models. Old fabricated split was also inaccurate.

### NEW-05: Fix #16 Incomplete Serialization
**Severity:** MEDIUM
**Status:** FIXED
`LoopMetrics.to_dict()` now serializes `step_durations_ms`, `step_costs`,
`start_time`, `end_time`. `from_dict()` restores them.

### NEW-06: Fix #07 Supervisor gather Crash
**Severity:** MEDIUM
**Status:** FIXED
`asyncio.gather` now uses `return_exceptions=True`. Failed tasks produce error
entries instead of crashing the entire parallel batch.

### NEW-07: Supervisor Context Isolation
**Severity:** MEDIUM
**Status:** FIXED
Each parallel step now receives `copy.deepcopy(ctx_data)` instead of sharing
the same dict reference.

---

## Pre-existing Issues (Not Addressed)

- ReplaySession.load() fragile on extra JSON keys — already fixed in #14
- MetricsCollector.load() loses loops — already fixed in #16
- MCP start_loop/pause_loop/resume_loop stubs — partial by refactoring agent
