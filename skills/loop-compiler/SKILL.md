# Skill: loop-compiler

---
name: loop-compiler
description: Convert existing Python DSL loops to LoopSpec v1 JSON with the built-in compiler.
---

Convert Python `@Loop` definitions to JSON.

## Steps

1. Compile: `loop-engine export loops/<name>.py --format json -o loops/<name>.json`
2. Review the emitted spec — unsupported constructs (per-step retry/on_error, tool= steps, callable conditions) raise a CompileError instead of silently dropping.
3. Validate the result: `loop-engine validate loops/<name>.json`
4. Optionally persist: read the JSON file and call `loop_save(loop_name, spec_json=...)`.
5. Note: parallel groups get auto names `group-N`; conditional conditions must be string expressions.
