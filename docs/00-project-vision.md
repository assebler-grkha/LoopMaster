# LoopMaster — Project Vision

## What is LoopMaster?

LoopMaster is a **loop engine** for AI agent systems. It is a Python library + thin CLI that lets developers define, validate, execute, and debug AI agent loops — sequences of LLM calls, tool invocations, and control flow that accomplish complex tasks.

## The Problem

AI agents (OpenCode, Claude Code, Cursor, Codex, etc.) are powerful but stateless per-session. When an agent needs to perform a multi-step task — research → analyze → synthesize → verify → iterate — the developer must manually orchestrate each step, handle errors, track costs, and resume on failure. There is no standard way to define these loops, no way to reuse them across sessions, and no way to observe what happened.

## The Solution

LoopMaster provides:

1. **Python DSL** for defining loops with `@Loop`, `Step()`, `Parallel()`, and native Python control flow
2. **Runtime engine** that executes loops with built-in checkpointing, error recovery, cost tracking
3. **Agent interaction layer** that safely reads/writes agent configuration files and system prompts
4. **Metrics and observability** for measuring loop efficiency and optimizing performance
5. **Interruption protection** for resuming after context overflow, crashes, or agent restarts

## Core Principle

> The agent writes **WHAT** to do (Python DSL). The master handles **HOW** (execution, checkpoints, errors, cost, recovery).

## What LoopMaster is NOT

- Not an agent framework (doesn't provide LLM clients, tool registries, or memory systems)
- Not a workflow engine (no DAGs, no visual editors, no YAML-first approach)
- Not a monitoring tool (emits events; external systems aggregate)
- Not distributed (v1 is local-only; distributed execution is a v2 consideration)

## Target Users

- Developers building AI agent systems who need reusable, debuggable loops
- Teams running production agent pipelines that need cost visibility and error recovery
- Agent developers who want to add loop capabilities to their agents without reimplementing checkpointing, error handling, and metrics from scratch

## Success Criteria

1. A developer can define and run a reflection loop in under 5 minutes
2. A 50-step loop runs to completion without manual intervention (with interruption protection)
3. Cost tracking is within 5% of actual API billing
4. On failure, the loop resumes from the last checkpoint without re-executing completed steps
5. The library works with any LLM provider (OpenAI, Anthropic, Google, local models) without modification
