from __future__ import annotations

from dataclasses import replace

import pytest

from codex_local_ops.agent_core import (
    AgentHandoff,
    AgentPolicy,
    AgentPolicyError,
    AgentProvider,
    AgentRouter,
    AgentRouterError,
    ProviderCost,
    Sensitivity,
    default_local_providers,
)
from codex_local_ops.config import load_config


def _external(*, cost: ProviderCost = ProviderCost.FREE, available: bool = True) -> AgentProvider:
    return AgentProvider("external", "free-model", False, cost, available=available, priority=1)


def test_free_only_defaults_true() -> None:
    assert load_config()["agents"]["free_only"] is True
    assert AgentPolicy.from_config().free_only is True


def test_local_free_profiles_are_allowed_and_ordered() -> None:
    qwen, devstral = default_local_providers()
    policy = AgentPolicy()
    assert policy.allowed(qwen, Sensitivity.PRIVATE)
    assert policy.allowed(devstral, Sensitivity.PRIVATE)
    assert AgentRouter((devstral, qwen), policy).route().provider.model == "qwen3-coder:30b"


@pytest.mark.parametrize(
    ("cost", "code"),
    [
        (ProviderCost.PAID, "PAID_PROVIDER_NOT_ALLOWED"),
        (ProviderCost.UNKNOWN, "PROVIDER_COST_UNKNOWN"),
    ],
)
def test_nonfree_costs_are_stably_blocked(cost: ProviderCost, code: str) -> None:
    with pytest.raises(AgentPolicyError, match=code) as exc:
        AgentPolicy().validate(_external(cost=cost), Sensitivity.PUBLIC)
    assert exc.value.code == code


@pytest.mark.parametrize("reason", ["timeout", "429", "5xx", "provider_unavailable"])
def test_free_fallback_records_supported_reasons(reason: str) -> None:
    qwen, devstral = default_local_providers()
    routed = AgentRouter((qwen, devstral), AgentPolicy()).route()
    fallback = AgentRouter((qwen, devstral), AgentPolicy()).fallback(routed, reason)
    assert fallback.provider.model == "devstral:24b"
    assert fallback.attempt == 2
    assert fallback.fallback_history[0].reason == reason
    assert fallback.fallback_history[0].provider_id == "ollama"


def test_paid_provider_is_never_a_fallback() -> None:
    qwen, _ = default_local_providers()
    router = AgentRouter((qwen, _external(cost=ProviderCost.PAID)), AgentPolicy())
    route = router.route()
    with pytest.raises(AgentRouterError, match="ALL_FREE_PROVIDERS_UNAVAILABLE"):
        router.fallback(route, "timeout")


def test_all_unavailable_has_stable_error() -> None:
    qwen, devstral = default_local_providers()
    unavailable = [
        replace(qwen, available=False),
        replace(devstral, available=False),
    ]
    with pytest.raises(AgentRouterError, match="ALL_FREE_PROVIDERS_UNAVAILABLE"):
        AgentRouter(unavailable).route()


def test_privacy_rules() -> None:
    external = _external()
    policy = AgentPolicy()
    assert policy.allowed(external, Sensitivity.PUBLIC)
    assert not policy.allowed(external, Sensitivity.PRIVATE)
    assert not policy.allowed(external, Sensitivity.SENSITIVE)
    assert AgentRouter(default_local_providers(), policy).route(Sensitivity.SENSITIVE).provider.local


def test_handoff_is_compact_and_redacted() -> None:
    handoff = AgentHandoff(
        task="Investigate token=super-secret-value",
        repo="example/repo",
        diff_summary="No full conversation is included",
        previous_error="Authorization: Bearer abcdefghijklmnop",
    ).as_dict()
    assert handoff["task"] == "Investigate token=[REDACTED]"
    assert "[REDACTED]" in handoff["previous_error"]
    assert "abcdefghijklmnop" not in handoff["previous_error"]
    assert "conversation" not in handoff
