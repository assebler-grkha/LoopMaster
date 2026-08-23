"""Unit and integration tests for Model Registry, Aliases, ModelPolicy, and Auto-Routing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from loopmaster import (
    ErrorPolicy,
    Loop,
    LoopEngine,
    ModelPolicy,
    ModelPolicyMode,
    ModelRegistry,
    ModelSpec,
    RecoveryAction,
    Step,
    auto_route_model,
)
from loopmaster.core.exceptions import ModelPolicyError, UnapprovedModelError
from loopmaster.mcp.models_tools import handle_model_list, handle_model_recommend


class TestModelRegistryAndAliases:
    def test_default_aliases_resolution(self):
        reg = ModelRegistry()
        fast_spec = reg.resolve("@fast")
        assert fast_spec.name == "gpt-4o-mini"
        assert fast_spec.provider == "openai"
        assert fast_spec.cost_input_1m == 0.15

        smart_spec = reg.resolve("@smart")
        assert smart_spec.name == "gpt-4o"

        coding_spec = reg.resolve("@coding")
        assert coding_spec.name == "claude-3-5-sonnet"
        assert coding_spec.provider == "anthropic"

    def test_custom_model_registration_and_alias_override(self):
        reg = ModelRegistry([])
        custom_spec = ModelSpec(
            name="deepseek-v3",
            provider="deepseek",
            aliases=["@fast", "@coding"],
            cost_input_1m=0.14,
            cost_output_1m=0.28,
            context_window=64000,
        )
        reg.register(custom_spec)

        resolved = reg.resolve("@coding")
        assert resolved.name == "deepseek-v3"
        assert resolved.provider == "deepseek"
        assert reg.calculate_cost("deepseek-v3", 1_000_000, 1_000_000) == pytest.approx(0.42)

    def test_permissive_mode_dynamic_registration(self):
        reg = ModelRegistry()
        spec = reg.resolve("custom-finetuned-llama")
        assert spec.name == "custom-finetuned-llama"
        assert spec.provider == "custom"
        assert spec.approved is True


class TestModelPolicyEnforcement:
    def test_strict_mode_rejects_unapproved_models(self):
        reg = ModelRegistry()
        reg.disallow("gpt-4o")

        policy = ModelPolicy(mode=ModelPolicyMode.STRICT)

        with pytest.raises(UnapprovedModelError, match="not approved for execution"):
            reg.validate_execution("gpt-4o", policy)

        # Approved model passes
        spec = reg.validate_execution("@fast", policy)
        assert spec.name == "gpt-4o-mini"

    def test_alias_only_mode_rejects_raw_model_names(self):
        reg = ModelRegistry()
        policy = ModelPolicy(mode=ModelPolicyMode.ALIAS_ONLY)

        with pytest.raises(UnapprovedModelError, match="raw model name 'gpt-4o-mini' is forbidden"):
            reg.validate_execution("gpt-4o-mini", policy)

        # Using alias passes
        spec = reg.validate_execution("@fast", policy)
        assert spec.name == "gpt-4o-mini"

    def test_max_cost_per_step_ceiling(self):
        reg = ModelRegistry()
        policy = ModelPolicy(max_cost_per_step=0.01)

        # Estimated cost for 100k tokens on claude-3-opus ($15/1M) is $1.50 > $0.01
        with pytest.raises(ModelPolicyError, match="exceeds max_cost_per_step limit"):
            reg.validate_execution("@heavy", policy, estimated_prompt_tokens=100000)


class TestAutoRouting:
    def test_route_complex_task_to_smart_tier(self):
        rec = auto_route_model(
            task_hint="Architect and refactor distributed lock manager", prompt_tokens=1000
        )
        assert rec.model.name in ("gpt-4o", "claude-3-5-sonnet")
        assert "smart" in rec.reason.lower() or "complex" in rec.reason.lower()

    def test_route_coding_task_to_coding_tier(self):
        rec = auto_route_model(task_hint="Write python unit tests for parser", prompt_tokens=500)
        assert rec.model.name == "claude-3-5-sonnet"
        assert "coding" in rec.reason.lower()

    def test_route_low_budget_to_fast_tier(self):
        rec = auto_route_model(
            task_hint="Complex reasoning", prompt_tokens=1000, remaining_budget=0.05
        )
        assert rec.model.name == "gpt-4o-mini"
        assert "budget" in rec.reason.lower()


class TestMCPModelTools:
    def test_handle_model_list_contains_no_secrets(self):
        res = handle_model_list()
        assert res["count"] > 0
        assert res["approved_count"] > 0
        for m in res["models"]:
            assert "name" in m
            assert "provider" in m
            assert "cost_input_1m" in m
            assert "api_key" not in m  # No secret leakage

    def test_handle_model_recommend(self):
        rec = handle_model_recommend(task="Simple text classification", prompt_tokens=200)
        assert "recommended_model" in rec
        assert "reason" in rec
        assert "estimated_cost" in rec


class TestEngineIntegrationWithModelRegistry:
    def test_engine_resolves_aliases_and_executes_fallback(self):
        mock_client = MagicMock()
        # First call fails, second (fallback) call succeeds
        mock_fail_resp = MagicMock()
        mock_fail_resp.content = ""
        mock_ok_resp = MagicMock()
        mock_ok_resp.content = "Fallback OK"
        mock_ok_resp.model = "gpt-4o-mini"
        mock_ok_resp.prompt_tokens = 10
        mock_ok_resp.completion_tokens = 5
        mock_ok_resp.total_tokens = 15

        mock_client.complete.side_effect = [Exception("Rate limit 429"), mock_ok_resp]

        @Loop(name="fallback_loop")
        def pipe(ctx):
            Step(
                "classify",
                model="@smart",
                prompt="Classify {input}",
                on_error=ErrorPolicy(
                    retry=1,
                    on_failure=RecoveryAction.FALLBACK,
                    fallback_model="@fast",
                ),
            )

        reg = ModelRegistry()
        engine = LoopEngine(
            model_registry=reg,
            model_policy=ModelPolicy(mode=ModelPolicyMode.STRICT),
            llm_client=mock_client,
        )

        result = engine.run(pipe, {"input": "test request"})
        assert result.success is True
        assert result.results["classify"].output == "Fallback OK"
