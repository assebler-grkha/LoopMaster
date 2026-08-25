# Skill: cost-optimizer

---
name: cost-optimizer
description: Pick the cheapest capable model for LLM steps before launching expensive loops.
---

Optimize loop cost before launch.

## Steps

1. Inventory available models: `model_list()` — shows approved models, semantic aliases (@fast/@smart/@coding/@cheap/@reasoning/@fallback) and pricing.
2. For the loop's task profile ask for a recommendation: `model_recommend(task=<description>, prompt_tokens=<estimate>)`.
3. Prefer semantic aliases over pinned model ids so policy changes apply later without spec edits.
4. Set a spend ceiling in the spec: top-level `"budget": {"max_cost": ..., "max_tokens": ..., "max_steps": ...}`; enforce budget presence via the budget-guard hook or env LM_REQUIRE_BUDGET=1.
5. Route heavy reasoning to @smart/@reasoning only where needed; mechanical steps get @fast/@cheap. On fallback_model in error_policy use @smart.
6. After the run compare actuals: `loop_status` payload metrics carry total_cost/total_tokens per job.
