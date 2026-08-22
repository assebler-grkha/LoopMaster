# ADR-008: Agent Adapter Pattern

**Status:** Accepted

## Context

Agent interaction was proposed as a monolithic "agent manager." Adversarial review identified that each agent has different config file locations, formats, and conventions.

## Decision

AgentAdapter base class with concrete implementations per agent type. AgentRegistry for auto-discovery. ConfigManager for safe modification (snapshot → atomic write → verify → rollback).

## Consequences

- (+) Each agent type is isolated
- (+) Safe modification with rollback
- (+) Extensible (new agents = new adapter)
- (-) Must maintain adapters per agent type
