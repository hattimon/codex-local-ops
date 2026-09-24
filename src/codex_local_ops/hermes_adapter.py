"""Hermes execution adapter for the free agent gateway.

The adapter is intentionally thin: provider selection lives in ``agent_core``
and process lifecycle lives in ``processes`` and ``jobs``.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Iterable

from . import jobs
from .agent_core import AgentPolicy, AgentPolicyError, AgentProvider, default_local_providers
from .config import load_config
from .processes import run
from .safety import assert_trusted_path, sanitize


def _known_executable_locations() -> tuple[Path, ...]:
    """Optional per-platform user install locations; PATH remains preferred."""
    home = Path.home()
    if os.name == "nt":
        local_app_data = Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local"))
        return (local_app_data / "hermes" / "hermes-agent" / "venv" / "Scripts" / "hermes.exe",)
    if os.sys.platform == "darwin":
        return (home / ".local" / "bin" / "hermes",)
    return (home / ".local" / "bin" / "hermes",)


def discover_executable(configured_path: str | Path | None = None) -> Path | None:
    """Find Hermes without binding the gateway to a single machine's layout."""
    if configured_path:
        configured = Path(configured_path).expanduser()
        if configured.is_file():
            return configured
    on_path = shutil.which("hermes")
    if on_path:
        return Path(on_path)
    for candidate in _known_executable_locations():
        if candidate.is_file():
            return candidate
    return None


def parse_usage_file(path: Path) -> dict[str, Any]:
    """Parse optional Hermes usage output without making it an execution dependency."""
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return sanitize(parsed) if isinstance(parsed, dict) else {}


class HermesAgentAdapter:
    """Run an explicitly selected, policy-approved Hermes local provider."""

    def __init__(
        self,
        providers: Iterable[AgentProvider] | None = None,
        policy: AgentPolicy | None = None,
        executable: str | Path | None = None,
    ) -> None:
        config = load_config()
        hermes_config = config.get("agents", {}).get("hermes", {})
        self.providers = tuple(default_local_providers() if providers is None else providers)
        self.policy = policy or AgentPolicy.from_config(config)
        self.executable = executable if executable is not None else hermes_config.get("executable")
        # Preserve an existing explicit ``timeout`` setting while preferring the
        # local-model policy introduced for CPU-bound Ollama workloads.
        self.local_timeout = int(hermes_config.get("local_timeout", hermes_config.get("timeout", 600)))
        self.external_timeout = int(hermes_config.get("external_timeout", 120))

    def executable_path(self) -> Path | None:
        return discover_executable(self.executable)

    def availability(self) -> dict[str, Any]:
        executable = self.executable_path()
        return {
            "status": "OK" if executable else "CAPABILITY_UNAVAILABLE",
            "available": executable is not None,
            "executable": str(executable) if executable else None,
        }

    def version(self) -> dict[str, Any]:
        executable = self.executable_path()
        if executable is None:
            return {"status": "CAPABILITY_UNAVAILABLE", "version": None}
        result = run([str(executable), "--version"], timeout=30)
        return {
            "status": "OK" if result.get("exit_code") == 0 else "FAILED",
            "version": result.get("stdout", "").strip() or None,
            "stderr": result.get("stderr", ""),
            "exit_code": result.get("exit_code"),
        }

    def _provider(self, provider_id: str, model: str) -> AgentProvider:
        for profile in self.providers:
            if profile.provider_id == provider_id and profile.model == model:
                self.policy.validate(profile, "private")
                if not profile.available:
                    raise AgentPolicyError("PROVIDER_UNAVAILABLE")
                return profile
        # An undeclared provider/model must be treated as unknown, never as free.
        unknown = AgentProvider(provider_id, model, local=False, cost="unknown", available=False)
        self.policy.validate(unknown, "private")
        raise AgentPolicyError("PROVIDER_NOT_CONFIGURED")

    def _trusted_cwd(self, repo: str | Path) -> Path:
        cwd = assert_trusted_path(repo, must_exist=True)
        if not cwd.is_dir():
            raise NotADirectoryError(str(cwd))
        return cwd

    def _timeout(self, provider: AgentProvider, explicit_timeout: int | None) -> int:
        if explicit_timeout is not None:
            return int(explicit_timeout)
        return self.local_timeout if provider.local else self.external_timeout

    @staticmethod
    def _argv(executable: Path, task: str, provider: AgentProvider) -> list[str]:
        if not task:
            raise ValueError("task must not be empty")
        return [
            str(executable),
            "-z",
            task,
            "--provider",
            provider.provider_id,
            "--model",
            provider.model,
        ]

    def _prepare(
        self,
        task: str,
        repo: str | Path,
        provider_id: str,
        model: str,
    ) -> tuple[Path, list[str], AgentProvider]:
        profile = self._provider(provider_id, model)
        cwd = self._trusted_cwd(repo)
        executable = self.executable_path()
        if executable is None:
            raise FileNotFoundError("Hermes executable was not found")
        return cwd, self._argv(executable, task, profile), profile

    @staticmethod
    def _result(process_result: dict[str, Any], provider: AgentProvider) -> dict[str, Any]:
        if process_result.get("timed_out"):
            status, error = "TIMEOUT", "Hermes task exceeded its timeout"
        elif process_result.get("exit_code") == 0:
            status, error = "COMPLETED", None
        else:
            status, error = "FAILED", "Hermes exited with a non-zero exit code"
        usage: dict[str, Any] = {}
        return {
            "status": status,
            "provider": provider.provider_id,
            "model": provider.model,
            "exit_code": process_result.get("exit_code"),
            "stdout": process_result.get("stdout", ""),
            "stderr": process_result.get("stderr", ""),
            "duration_ms": process_result.get("duration_ms"),
            "usage": usage,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
            "estimated_cost_usd": usage.get("estimated_cost_usd"),
            "session_id": None,
            "error": error,
        }

    def run(
        self,
        task: str,
        repo: str | Path,
        *,
        provider: str = "ollama",
        model: str = "qwen3-coder:30b",
        timeout: int | None = None,
    ) -> dict[str, Any]:
        cwd, argv, profile = self._prepare(task, repo, provider, model)
        process_result = run(
            argv,
            cwd=cwd,
            timeout=self._timeout(profile, timeout),
        )
        return sanitize(self._result(process_result, profile))

    def start(
        self,
        task: str,
        repo: str | Path,
        *,
        provider: str = "ollama",
        model: str = "qwen3-coder:30b",
        timeout: int | None = None,
    ) -> dict[str, Any]:
        cwd, argv, profile = self._prepare(task, repo, provider, model)
        # ``jobs`` owns process-tree cancellation and bounded/redacted output.
        started = jobs.start(
            argv,
            cwd=cwd,
            label=f"hermes:{profile.provider_id}/{profile.model}",
            timeout=self._timeout(profile, timeout),
        )
        return sanitize(
            {
                **started,
                "provider": profile.provider_id,
                "model": profile.model,
                "timeout_seconds": self._timeout(profile, timeout),
            }
        )

    def cancel(self, session_id: str) -> dict[str, Any]:
        return jobs.cancel(session_id)
