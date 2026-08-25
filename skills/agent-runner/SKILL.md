# Skill: agent-runner

---
name: agent-runner
description: Execute a loop yourself step by step in agent mode, recording progress via loop_record.
---

Run loops without an external LLM: you ARE the executor.

## Steps

1. Launch: `loop_run(spec_json=<json>, context=<initial context json>, mode="agent")` → returns `{job_id, status:"ready", steps}` plus `context` (the persisted initial values).
2. Read the plan from `loop_get(loop_name=...)` or the returned steps list; resolve `{var}` templates against the initial context and results of prior steps.
3. Execute each leaf step yourself:
   - `llm` — do the task with your own model using the prompt.
   - `shell`/`http`/`mcp` — perform the action directly.
   - `code` — run via a shell step after extracting the block source (`block_get(ref)`), honoring its JSON stdin/stdout contract.
   - `human` — ask the user, then record their answer as output.
4. Record each completed step: `loop_record(job_id, step_name=<exact plan name>, success=true, output=<json>)`. Unknown names are rejected; recording on a terminal job errors with "already terminal".
5. For conditional nodes take exactly one branch based on evaluating the condition against current results.
6. After the last record call `loop_record(job_id, step_name=<last>, finalize=true)` to force completion — required for conditional specs whose total_steps counts both branches.
