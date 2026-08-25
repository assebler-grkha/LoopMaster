# Skill: inbox-monitor

---
name: inbox-monitor
description: Poll the loop notification inbox and act on pending notifications by priority.
---

Monitor and triage loop notifications.

## Steps

1. Check the `pending_notifications` marker attached to every MCP tool response: `{info: N, needs_input: N, critical: N}`. If all zero — nothing to do.
2. Fetch details: `loop_inbox(unread_only=true, limit=20, mark_read=true)`.
3. Triage by priority:
   - `info` (loop_started/loop_completed) — acknowledge, no action needed.
   - `needs_input` (waiting_input) — switch to the hitl-responder flow (`loop_questions` → `loop_respond`).
   - `critical` (loop_failed / escalation) — inspect `loop_status(job_id)` for error/metrics, then debug or re-drive (see loop-debugger skill).
4. Critical notifications are additionally mirrored to `.loopmaster/inbox/critical.json` for out-of-band pickup.
5. Read notifications are retained 7 days; unread ones never expire.
