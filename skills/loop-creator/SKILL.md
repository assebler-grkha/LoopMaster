# Skill: loop-creator

---
name: loop-creator
description: Create a LoopSpec v1 JSON loop from a task description, validate it, and save it to the database.
---

Create JSON loops for the user.

## Steps

1. Draft a LoopSpec v1 document. Required shape:

```json
{
  "loopmaster": "1.0",
  "name": "my-loop",
  "version": "1.0.0",
  "execution": "engine",
  "context": {"goal": "..."},
  "steps": [
    {"type": "llm", "name": "step-1", "prompt": "..."},
    {"type": "shell", "name": "run-x", "command": ["python", "-c", "..."]},
    {"type": "parallel", "steps": [{"type": "llm", "name": "a", "prompt": "..."}]},
    {"type": "conditional", "name": "route", "condition": "{flag} == 'yes'", "then": [], "else": []},
    {"type": "code", "name": "transform", "ref": "block@1.0.0", "input": {}},
    {"type": "human", "name": "confirm", "question": "...?", "default_answer": "no"}
  ]
}
```

2. Rules: names are kebab/snake lowercase; `{var}` placeholders must reference context keys or earlier step names; conditions support comparisons/and/or/not only; shell steps need `["python","-c","..."]` style commands on Windows.
3. Validate before saving: `loop-engine validate my-loop.json` (or run through `loop_save` which rejects invalid specs).
4. Save with `loop_save(loop_name="my-loop", spec_json=<json string>)`.
5. Run: ask the user whether detached (`loop_run(spec_json=..., mode="detached")`) or agent execution is wanted.
