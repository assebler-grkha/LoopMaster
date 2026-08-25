# Skill: loop-debugger

---
name: loop-debugger
description: Diagnose failed, stale, or stuck loops and re-drive them safely.
---

Diagnose and recover failing loops.

## Steps

1. Inspect: `loop_status(job_id=...)` — read `status`, `error`, `results`, and `metrics` (duration_ms, total_cost, total_tokens). Stale/owner-dead jobs are flagged by the status tool itself.
2. Classify:
   - Step failure → look up `results.<step>.error`; fix root cause before retrying.
   - `waiting_input` → not broken: answer via hitl-responder flow.
   - Stale (>15 min no heartbeat, non-agent) or owner-dead → the process is gone; the job is safe to abandon.
3. Stop a runaway/stuck job: `loop_cancel(job_id=...)`.
4. Re-drive: submit a fresh `loop_run(spec_json=...)` (new job_id); resubmitting the same explicit job_id upserts and restarts it. Persisted loops can be relaunched via `loop_run(loop_name=...)`.
5. Check notifications for failure context: `critical loop_failed` events carry the error summary; `loop_inbox` keeps them readable.
6. For repeated failures at the same step, validate the spec (`validate_loop_spec` semantics: unknown {var} refs, disallowed conditions, block sha pins) before retrying.
