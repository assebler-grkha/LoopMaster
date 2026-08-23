"""Central Model Registry — Single Source of Truth for model metadata, aliases, and pricing."""

from __future__ import annotations

import logging

from ..core.exceptions import ModelPolicyError, UnapprovedModelError
from .types import ModelPolicy, ModelPolicyMode, ModelRecommendation, ModelSpec

logger = logging.getLogger("loopmaster.models.registry")

_DEFAULT_SPECS: list[ModelSpec] = [
    # OpenAI
    ModelSpec(
        name="gpt-4o-mini",
        provider="openai",
        aliases=["@fast", "@cheap", "@default", "@fallback"],
        cost_input_1m=0.15,
        cost_output_1m=0.60,
        context_window=128000,
        max_output_tokens=16384,
        tags=["fast", "cheap", "default"],
        api_key_env="OPENAI_API_KEY",
    ),
    ModelSpec(
        name="gpt-4o",
        provider="openai",
        aliases=["@smart", "@vision"],
        cost_input_1m=2.50,
        cost_output_1m=10.00,
        context_window=128000,
        max_output_tokens=16384,
        tags=["smart", "general", "vision"],
        api_key_env="OPENAI_API_KEY",
    ),
    ModelSpec(
        name="o1-mini",
        provider="openai",
        aliases=["@reasoning"],
        cost_input_1m=3.00,
        cost_output_1m=12.00,
        context_window=128000,
        max_output_tokens=65536,
        tags=["reasoning", "math", "stem"],
        api_key_env="OPENAI_API_KEY",
    ),
    # Anthropic
    ModelSpec(
        name="claude-3-5-sonnet",
        provider="anthropic",
        aliases=["@coding", "@smart_anthropic"],
        cost_input_1m=3.00,
        cost_output_1m=15.00,
        context_window=200000,
        max_output_tokens=8192,
        tags=["coding", "smart", "reasoning"],
        api_key_env="ANTHROPIC_API_KEY",
    ),
    ModelSpec(
        name="claude-3-5-haiku",
        provider="anthropic",
        aliases=["@fast_anthropic"],
        cost_input_1m=0.80,
        cost_output_1m=4.00,
        context_window=200000,
        max_output_tokens=8192,
        tags=["fast", "cheap"],
        api_key_env="ANTHROPIC_API_KEY",
    ),
    ModelSpec(
        name="claude-3-opus",
        provider="anthropic",
        aliases=["@heavy"],
        cost_input_1m=15.00,
        cost_output_1m=75.00,
        context_window=200000,
        max_output_tokens=4096,
        tags=["heavy", "analysis"],
        api_key_env="ANTHROPIC_API_KEY",
    ),
    # Google
    ModelSpec(
        name="gemini-1.5-flash",
        provider="google",
        aliases=["@fast_google"],
        cost_input_1m=0.075,
        cost_output_1m=0.30,
        context_window=1000000,
        max_output_tokens=8192,
        tags=["fast", "cheap", "long_context"],
        api_key_env="GOOGLE_API_KEY",
    ),
    ModelSpec(
        name="gemini-1.5-pro",
        provider="google",
        aliases=["@large_context"],
        cost_input_1m=1.25,
        cost_output_1m=5.00,
        context_window=2000000,
        max_output_tokens=8192,
        tags=["smart", "ultra_long_context"],
        api_key_env="GOOGLE_API_KEY",
    ),
    # Local
    ModelSpec(
        name="local-default",
        provider="local",
        aliases=["@local"],
        cost_input_1m=0.0,
        cost_output_1m=0.0,
        context_window=32768,
        max_output_tokens=4096,
        tags=["local", "free", "offline"],
        base_url="http://localhost:11434/v1",
        requires_api_key=False,
    ),
]


class ModelRegistry:
    """Central repository of registered LLM models, pricing, and alias resolution."""

    def __init__(self, specs: list[ModelSpec] | None = None) -> None:
        self._models: dict[str, ModelSpec] = {}
        self._aliases: dict[str, str] = {}
        initial = specs if specs is not None else _DEFAULT_SPECS
        for spec in initial:
            self.register(spec)

    def register(self, spec: ModelSpec) -> None:
        """Register or update a model specification."""
        import dataclasses

        cloned = dataclasses.replace(spec, aliases=list(spec.aliases), tags=list(spec.tags))
        self._models[cloned.name] = cloned
        for alias in cloned.aliases:
            self._aliases[alias] = cloned.name

    def approve(self, name: str) -> None:
        """Approve a model for execution."""
        if name in self._models:
            self._models[name].approved = True

    def disallow(self, name: str) -> None:
        """Disallow a model from execution."""
        if name in self._models:
            self._models[name].approved = False

    def resolve(self, model_or_alias: str | None) -> ModelSpec:
        """Resolve a model name or semantic alias (@fast, @smart) to ModelSpec."""
        key = (model_or_alias or "@default").strip()
        if key == "@auto":
            from .router import auto_route_model

            rec = auto_route_model(registry=self)
            return rec.model

        if key.startswith("@") and key in self._aliases:
            target_name = self._aliases[key]
            return self._models[target_name]

        if key in self._models:
            return self._models[key]

        # Dynamic fallback for unknown models in permissive mode
        fallback_spec = ModelSpec(
            name=key,
            provider="custom",
            aliases=[],
            cost_input_1m=2.50,
            cost_output_1m=10.00,
            approved=True,
        )
        self._models[key] = fallback_spec
        return fallback_spec

    def calculate_cost(self, model_name: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate cost in dollars for token consumption."""
        spec = self.resolve(model_name)
        return spec.calculate_cost(input_tokens, output_tokens)

    def get_approved_models(self) -> list[ModelSpec]:
        """Return list of all approved models."""
        return [m for m in self._models.values() if m.approved]

    def recommend(
        self,
        task_hint: str = "",
        prompt_tokens: int = 0,
        remaining_budget: float | None = None,
    ) -> ModelRecommendation:
        """Recommend optimal model using auto-router heuristic."""
        from .router import auto_route_model

        return auto_route_model(
            task_hint=task_hint,
            prompt_tokens=prompt_tokens,
            remaining_budget=remaining_budget,
            registry=self,
        )

    def validate_execution(
        self,
        model_or_alias: str | None,
        policy: ModelPolicy | None,
        estimated_prompt_tokens: int = 0,
    ) -> ModelSpec:
        """Validate model execution against configured ModelPolicy rules."""
        raw_key = model_or_alias or "@default"
        pol = policy or ModelPolicy()

        if pol.mode == ModelPolicyMode.ALIAS_ONLY and not raw_key.startswith("@"):
            raise UnapprovedModelError(
                f"ModelPolicy is in ALIAS_ONLY mode: raw model name '{raw_key}' is forbidden. "
                "Use semantic aliases (@fast, @smart, @coding, @cheap)."
            )

        spec = self.resolve(raw_key)

        if pol.mode == ModelPolicyMode.STRICT and not spec.approved:
            raise UnapprovedModelError(
                f"Model '{spec.name}' is not approved for execution. "
                f"Approved models: {[m.name for m in self.get_approved_models()]}"
            )

        if pol.max_cost_per_step is not None and estimated_prompt_tokens > 0:
            est_cost = spec.calculate_cost(estimated_prompt_tokens, 0)
            if est_cost > pol.max_cost_per_step:
                raise ModelPolicyError(
                    f"Estimated input cost (${est_cost:.4f}) for model '{spec.name}' exceeds "
                    f"max_cost_per_step limit (${pol.max_cost_per_step:.4f})."
                )

        return spec


_global_registry = ModelRegistry()


def get_default_registry() -> ModelRegistry:
    """Return the global default ModelRegistry singleton."""
    return _global_registry
