# Skill: detached-runner

---
name: detached-runner
description: Launch a loop as a background job and poll it to completion without blocking your own work.
---

Run loops detached so you can keep working while they execute.

## Steps

1. Prepare a LoopSpec v1 JSON (see loop-creator skill) with unique step names.
2. Launch: `loop_run(spec_json=<json>, context=<initial context json>, mode="detached")` → returns `{job_id}` immediately.
3. Continue your own work; do not busy-poll. The job runs on a daemon thread inside the MCP server with checkpoints, heartbeats, and cancel support.
4. When you need progress/results: `loop_status(job_id=...)`:
   - `running`/`in_progress` — still going (payload shows current_step/total_steps).
   - `completed` — results in `payload.results` keyed by step name.
   - `failed` — see payload `error`; use the loop-debugger skill.
   - `waiting_input` — HITL question attached to the status payload; answer via loop_respond.
5. Cancel if needed: `loop_cancel(job_id=...)` — takes effect between steps.
