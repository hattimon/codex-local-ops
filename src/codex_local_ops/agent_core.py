"""Provider-neutral models and routing policy for the free agent gateway.

This module deliberately has no subprocess or network dependency.  Execution
adapters are a later concern; the core only decides which declared provider is
eligible and records deterministic fallback state.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable

from .config import load_config
from .safety import sanitize


class ProviderCost(str, Enum):
    FREE = "free"
    PAID = "paid"
    UNKNOWN = "unknown"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    SENSITIVE = "sensitive"


class AgentPolicyError(ValueError):
    """Stable policy failure suitable for a future service boundary."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message or code)


class AgentRouterError(RuntimeError):
    def __init__(self, code: str = "ALL_FREE_PROVIDERS_UNAVAILABLE") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AgentProvider:
    provider_id: str
    model: str
    local: bool
    cost: ProviderCost | str
    auth_type: str = "none"
    available: bool = True
    supports_tools: bool = False
    sensitivity_eligibility: tuple[Sensitivity | str, ...] = (
        Sensitivity.PUBLIC,
        Sensitivity.PRIVATE,
        Sensitivity.SENSITIVE,
    )
    priority: int = 100
    endpoint: str | None = None

    @property
    def normalized_cost(self) -> ProviderCost:
        try:
            return ProviderCost(self.cost)
        except ValueError:
            return ProviderCost.UNKNOWN

    def supports_sensitivity(self, sensitivity: Sensitivity) -> bool:
        supported = {
            value.value if isinstance(value, Sensitivity) else str(value)
            for value in self.sensitivity_eligibility
        }
        return sensitivity.value in supported


def default_local_providers(endpoint: str | None = None) -> tuple[AgentProvider, AgentProvider]:
    """The local profiles currently supported by the planned Hermes adapter."""
    configured = load_config().get("agents", {})
    resolved_endpoint = endpoint or str(configured.get("endpoint", "http://127.0.0.1:11434/v1"))
    return (
        AgentProvider(
            provider_id="ollama",
            model="qwen3-coder:30b",
            local=True,
            cost=ProviderCost.FREE,
            supports_tools=True,
            priority=10,
            endpoint=resolved_endpoint,
        ),
        AgentProvider(
            provider_id="ollama",
            model="devstral:24b",
            local=True,
            cost=ProviderCost.FREE,
            supports_tools=True,
            priority=20,
            endpoint=resolved_endpoint,
        ),
    )


@dataclass(frozen=True, slots=True)
class AgentPolicy:
    free_only: bool = True
    allow_external_public: bool = True
    allow_external_private: bool = False

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> "AgentPolicy":
        cfg = load_config() if config is None else config
        agents = cfg.get("agents", {})
        privacy = agents.get("privacy", {})
        return cls(
            free_only=bool(agents.get("free_only", True)),
            allow_external_public=bool(privacy.get("allow_external_public", True)),
            allow_external_private=bool(privacy.get("allow_external_private", False)),
        )

    def validate(self, provider: AgentProvider, sensitivity: Sensitivity | str) -> None:
        level = Sensitivity(sensitivity)
        if self.free_only:
            if provider.normalized_cost is ProviderCost.PAID:
                raise AgentPolicyError("PAID_PROVIDER_NOT_ALLOWED")
            if provider.normalized_cost is ProviderCost.UNKNOWN:
                raise AgentPolicyError("PROVIDER_COST_UNKNOWN")
        if not provider.supports_sensitivity(level):
            raise AgentPolicyError("PROVIDER_SENSITIVITY_NOT_ALLOWED")
        if level is Sensitivity.SENSITIVE and not provider.local:
            raise AgentPolicyError("SENSITIVE_DATA_REQUIRES_LOCAL_PROVIDER")
        if level is Sensitivity.PRIVATE and not provider.local and not self.allow_external_private:
            raise AgentPolicyError("PRIVATE_EXTERNAL_PROVIDER_NOT_ALLOWED")
        if level is Sensitivity.PUBLIC and not provider.local and not self.allow_external_public:
            raise AgentPolicyError("PUBLIC_EXTERNAL_PROVIDER_NOT_ALLOWED")

    def allowed(self, provider: AgentProvider, sensitivity: Sensitivity | str) -> bool:
        try:
            self.validate(provider, sensitivity)
        except AgentPolicyError:
            return False
        return True


@dataclass(frozen=True, slots=True)
class FallbackRecord:
    provider_id: str
    model: str
    attempt: int
    reason: str
    timestamp: str


@dataclass(slots=True)
class AgentRoute:
    provider: AgentProvider
    attempt: int
    fallback_history: list[FallbackRecord] = field(default_factory=list)


class AgentRouter:
    """Pure deterministic router.  Adapters report failure reasons to ``fallback``."""

    VALID_FAILURE_REASONS = frozenset(
        {
            "quota",
            "429",
            "5xx",
            "timeout",
            "auth",
            "provider_unavailable",
            "invalid_response",
            "process_launch_failure",
            "nonzero_exit",
        }
    )

    def __init__(
        self,
        providers: Iterable[AgentProvider] | None = None,
        policy: AgentPolicy | None = None,
    ) -> None:
        self.policy = policy or AgentPolicy.from_config()
        declared = default_local_providers() if providers is None else providers
        self.providers = tuple(
            sorted(declared, key=lambda item: (item.priority, item.provider_id, item.model))
        )

    def _eligible(
        self,
        sensitivity: Sensitivity | str,
        *,
        exclude: set[tuple[str, str]] | None = None,
    ) -> list[AgentProvider]:
        excluded = exclude or set()
        return [
            provider
            for provider in self.providers
            if provider.available
            and (provider.provider_id, provider.model) not in excluded
            and self.policy.allowed(provider, sensitivity)
        ]

    def route(self, sensitivity: Sensitivity | str = Sensitivity.PRIVATE) -> AgentRoute:
        candidates = self._eligible(sensitivity)
        if not candidates:
            raise AgentRouterError()
        return AgentRoute(provider=candidates[0], attempt=1)

    def fallback(
        self,
        route: AgentRoute,
        reason: str,
        sensitivity: Sensitivity | str = Sensitivity.PRIVATE,
    ) -> AgentRoute:
        if reason not in self.VALID_FAILURE_REASONS:
            raise ValueError(f"unsupported provider failure reason: {reason}")
        history = [
            *route.fallback_history,
            FallbackRecord(
                provider_id=route.provider.provider_id,
                model=route.provider.model,
                attempt=route.attempt,
                reason=reason,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
        ]
        tried = {(item.provider_id, item.model) for item in history}
        candidates = self._eligible(sensitivity, exclude=tried)
        if not candidates:
            raise AgentRouterError()
        return AgentRoute(provider=candidates[0], attempt=route.attempt + 1, fallback_history=history)


@dataclass(slots=True)
class AgentJobMetadata:
    """Agent-specific metadata designed to compose with, not replace, ``jobs.py``."""
    id: str
    repo: str
    task: str
    provider: str | None = None
    status: str | None = None
    base_head: str | None = None
    branch: str | None = None
    changed_files: list[str] = field(default_factory=list)
    added_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    deleted_files: list[str] = field(default_factory=list)
    untracked_files: list[str] = field(default_factory=list)
    preexisting_files: list[str] = field(default_factory=list)
    diff_summary: dict[str, Any] = field(default_factory=dict)
    repo_lock_status: str | None = None
    git_capture_status: str | None = None
    validation_status: str | None = None
    provider_attempts: list[dict[str, Any]] = field(default_factory=list)
    fallback_history: list[FallbackRecord] = field(default_factory=list)
    approval: str | None = None
    publish_status: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None


@dataclass(slots=True)
class AgentHandoff:
    task: str
    repo: str
    base_commit: str | None = None
    branch: str | None = None
    changed_files: list[str] = field(default_factory=list)
    diff_summary: str | None = None
    test_results: str | None = None
    remaining_objective: str | None = None
    previous_provider: str | None = None
    previous_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return compact, recursively redacted handoff data for a future adapter."""
        return sanitize(asdict(self))
