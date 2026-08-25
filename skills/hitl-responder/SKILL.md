# Skill: hitl-responder

---
name: hitl-responder
description: Detect loops waiting for human input and answer their questions via loop_respond.
---

Handle HITL questions from running loops.

## Steps

1. Watch for `waiting_input` status in `loop_status(job_id=...)` responses, or poll `loop_questions()` (optionally filtered by `job_id`).
2. Each pending question carries: `msg_id`, `job_id`, `from_addr`, text, options, `default_answer`, and expiry.
3. Answer on the user's behalf only when confident; otherwise surface the question to the user verbatim.
4. Respond: `loop_respond(job_id=<same job>, msg_id=<from question>, answer="yes")` — job_id must match the message's own job (mismatch is rejected).
5. The loop resumes automatically; confirm via `loop_status` until terminal (`completed`/`failed`). Results land under `results.<step_name>.answer`.
