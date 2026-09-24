"""Internal orchestration for policy-approved local coding-agent runs."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from .agent_core import (
    AgentHandoff,
    AgentJobMetadata,
    AgentPolicy,
    AgentPolicyError,
    AgentProvider,
    AgentRoute,
    AgentRouter,
    AgentRouterError,
    Sensitivity,
    default_local_providers,
)
from .hermes_adapter import HermesAgentAdapter
from .repo_safety import RepoBusyError, RepositoryMutationLock, capture_snapshot, change_capture
from .safety import assert_trusted_path, sanitize


class AgentAdapter(Protocol):
    def run(
        self,
        task: str,
        repo: str | Path,
        *,
        provider: str,
        model: str,
        timeout: int | None = None,
    ) -> dict[str, Any]: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentRuntime:
    """Run each eligible provider at most once, using ``AgentRouter`` for fallback."""

    NON_RETRYABLE_MARKERS = frozenset(
        {
            "cancelled",
            "paid_provider_not_allowed",
            "provider_cost_unknown",
            "provider_sensitivity_not_allowed",
            "sensitive_data_requires_local_provider",
            "private_external_provider_not_allowed",
            "permission_denied",
            "untrusted",
            "validation",
            "security",
            "user_rejection",
        }
    )

    def __init__(
        self,
        adapter: AgentAdapter | None = None,
        providers: Iterable[AgentProvider] | None = None,
        policy: AgentPolicy | None = None,
    ) -> None:
        self.providers = tuple(default_local_providers() if providers is None else providers)
        self.policy = policy or AgentPolicy.from_config()
        self.router = AgentRouter(self.providers, self.policy)
        self.adapter = adapter or HermesAgentAdapter(providers=self.providers, policy=self.policy)

    def _preferred_route(
        self,
        sensitivity: Sensitivity | str,
        provider_id: str | None,
        model: str | None,
    ) -> AgentRoute:
        if provider_id is None and model is None:
            return self.router.route(sensitivity)
        matches = [
            provider
            for provider in self.router.providers
            if (provider_id is None or provider.provider_id == provider_id)
            and (model is None or provider.model == model)
        ]
        if not matches:
            unknown = AgentProvider(provider_id or "unknown", model or "unknown", False, "unknown")
            self.policy.validate(unknown, sensitivity)
            raise AgentPolicyError("PROVIDER_NOT_CONFIGURED")
        selected = matches[0]
        self.policy.validate(selected, sensitivity)
        if not selected.available:
            raise AgentPolicyError("PROVIDER_UNAVAILABLE")
        return AgentRoute(provider=selected, attempt=1)

    @classmethod
    def _failure_reason(cls, result: dict[str, Any]) -> str | None:
        status = str(result.get("status", "")).upper()
        message = " ".join(
            str(result.get(key, "")) for key in ("error", "stderr", "reason")
        ).lower()
        if status == "COMPLETED":
            return None
        if status == "TIMEOUT":
            return "timeout"
        if status == "CANCELLED" or any(marker in message for marker in cls.NON_RETRYABLE_MARKERS):
            return None
        if "provider unavailable" in message or "unavailable" in message:
            return "provider_unavailable"
        if "auth" in message:
            return "auth"
        if "429" in message:
            return "429"
        if "quota" in message:
            return "quota"
        if "5xx" in message or any(f"{code}" in message for code in range(500, 600)):
            return "5xx"
        if "invalid response" in message:
            return "invalid_response"
        if result.get("exit_code") is None:
            return "process_launch_failure"
        if status == "FAILED":
            return "nonzero_exit"
        return None

    @staticmethod
    def _attempt(route: AgentRoute, started_at: str, finished_at: str, result: dict[str, Any], reason: str | None) -> dict[str, Any]:
        return sanitize(
            {
                "provider": route.provider.provider_id,
                "model": route.provider.model,
                "started_at": started_at,
                "finished_at": finished_at,
                "status": result.get("status", "FAILED"),
                "reason": reason,
                "duration_ms": result.get("duration_ms"),
                "exit_code": result.get("exit_code"),
                "error": result.get("error") or result.get("stderr") or None,
            }
        )

    @staticmethod
    def _result(job: AgentJobMetadata, latest: dict[str, Any]) -> dict[str, Any]:
        duration = sum(
            item["duration_ms"]
            for item in job.provider_attempts
            if isinstance(item.get("duration_ms"), (int, float))
        )
        return sanitize(
            {
                "job_id": job.id,
                "status": job.status,
                "selected_provider": job.provider,
                "selected_model": job.provider_attempts[-1]["model"] if job.provider_attempts else None,
                "attempts": job.provider_attempts,
                "fallback_history": job.fallback_history,
                "stdout": latest.get("stdout", ""),
                "stderr": latest.get("stderr", ""),
                "duration_ms": duration,
                "usage": latest.get("usage", {}),
                "input_tokens": latest.get("input_tokens"),
                "output_tokens": latest.get("output_tokens"),
                "total_tokens": latest.get("total_tokens"),
                "error": job.error,
                "base_head": job.base_head,
                "branch": job.branch,
                "changed_files": job.changed_files,
                "added_files": job.added_files,
                "modified_files": job.modified_files,
                "deleted_files": job.deleted_files,
                "untracked_files": job.untracked_files,
                "preexisting_files": job.preexisting_files,
                "diff_summary": job.diff_summary,
                "repo_lock_status": job.repo_lock_status,
                "git_capture_status": job.git_capture_status,
            }
        )

    @staticmethod
    def _capture_changes(job: AgentJobMetadata, before: Any, repo_path: Path) -> None:
        captured = change_capture(before, repo_path)
        job.base_head = captured["base_head"]
        job.branch = captured["branch"]
        job.changed_files = captured["changed_files"]
        job.added_files = captured["added_files"]
        job.modified_files = captured["modified_files"]
        job.deleted_files = captured["deleted_files"]
        job.untracked_files = captured["untracked_files"]
        job.preexisting_files = captured["preexisting_files"]
        job.diff_summary = captured["diff_summary"]
        job.git_capture_status = captured["git_capture_status"]

    def run(
        self,
        task: str,
        repo: str | Path,
        *,
        sensitivity: Sensitivity | str = Sensitivity.PRIVATE,
        preferred_provider: str | None = None,
        preferred_model: str | None = None,
        timeout: int | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        repo_path = assert_trusted_path(repo, must_exist=True)
        if not repo_path.is_dir():
            raise NotADirectoryError(str(repo_path))
        job = AgentJobMetadata(id=f"agent_{uuid.uuid4().hex}", repo=str(repo_path), task=task, status="PENDING")
        latest: dict[str, Any] = {"stdout": "", "stderr": "", "usage": {}}
        cancelled = is_cancelled or (lambda: False)
        lock = RepositoryMutationLock(repo_path)
        try:
            lock.acquire()
        except RepoBusyError:
            job.status, job.error, job.repo_lock_status = "FAILED", "REPO_BUSY", "REPO_BUSY"
            job.finished_at = _now()
            return self._result(job, latest)
        job.repo_lock_status = "ACQUIRED"
        before = capture_snapshot(repo_path)
        captured = False

        def complete() -> dict[str, Any]:
            nonlocal captured
            self._capture_changes(job, before, repo_path)
            captured = True
            return self._result(job, latest)

        try:
            route = self._preferred_route(sensitivity, preferred_provider, preferred_model)
        except (AgentPolicyError, AgentRouterError) as exc:
            job.status = "FAILED"
            job.error = getattr(exc, "code", str(exc))
            job.finished_at = _now()
            try:
                return complete()
            finally:
                lock.release()

        try:
            job.status = "RUNNING"
            job.started_at = _now()
            current_task = task
            while True:
                if cancelled():
                    job.status, job.error, job.finished_at = "CANCELLED", "CANCELLED", _now()
                    return complete()
                started_at = _now()
                try:
                    latest = sanitize(self.adapter.run(current_task, repo_path, provider=route.provider.provider_id, model=route.provider.model, timeout=timeout))
                except AgentPolicyError as exc:
                    latest = {"status": "FAILED", "error": exc.code, "stdout": "", "stderr": ""}
                except OSError as exc:
                    latest = {"status": "FAILED", "error": "process launch failure", "stderr": str(exc)}
                except Exception as exc:
                    latest = {"status": "FAILED", "error": "agent adapter exception", "stderr": str(exc)}
                finished_at = _now()
                reason = self._failure_reason(latest)
                job.provider = route.provider.provider_id
                job.provider_attempts.append(self._attempt(route, started_at, finished_at, latest, reason))
                if str(latest.get("status", "")).upper() == "COMPLETED":
                    job.status, job.finished_at = "COMPLETED", finished_at
                    return complete()
                if cancelled() or str(latest.get("status", "")).upper() == "CANCELLED":
                    job.status, job.error, job.finished_at = "CANCELLED", "CANCELLED", finished_at
                    return complete()
                if reason is None:
                    job.status, job.error, job.finished_at = "FAILED", latest.get("error"), finished_at
                    return complete()
                try:
                    next_route = self.router.fallback(route, reason, sensitivity)
                except AgentRouterError as exc:
                    job.status, job.error, job.finished_at = "FAILED", exc.code, finished_at
                    return complete()
                handoff = AgentHandoff(task=task, repo=str(repo_path), diff_summary="No repository changes are assumed during provider fallback.", remaining_objective=task, previous_provider=f"{route.provider.provider_id}/{route.provider.model}", previous_error=str(latest.get("error") or latest.get("stderr") or reason)[:1000]).as_dict()
                job.fallback_history.append({"from_provider": route.provider.provider_id, "from_model": route.provider.model, "to_provider": next_route.provider.provider_id, "to_model": next_route.provider.model, "reason": reason, "timestamp": _now()})
                current_task = "Compact handoff:\n" + json.dumps(handoff, ensure_ascii=False, separators=(",", ":"))
                route = next_route
        finally:
            if not captured:
                try:
                    self._capture_changes(job, before, repo_path)
                finally:
                    lock.release()
            else:
                lock.release()
