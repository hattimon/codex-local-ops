from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import psutil

from .safety import sanitize
from .setup_state import load_setup_state, save_setup_state, utc_timestamp

CODEX_WEB_REPOSITORY = "miuuyy/codex-chatgpt-web"
AUDITED_RELEASE = "6.1.1"
AUDITED_RELEASE_DATE = "2026-09-26"
WINDOWS_LAUNCHER_GUID = "d1a6026a-6210-588e-9a2b-da3936f94e02"
WINDOWS_INSTALLER_COMMAND = (
    "irm https://github.com/miuuyy/codex-chatgpt-web/releases/latest/download/install-launcher.ps1 | iex"
)
WINDOWS_INSTALLER_URL = (
    "https://github.com/miuuyy/codex-chatgpt-web/releases/latest/download/install-launcher.ps1"
)
RELEASES_URL = "https://github.com/miuuyy/codex-chatgpt-web/releases/latest"
CONNECTOR_NAME = "Codex Native2"
CONNECTOR_AUTHENTICATION = "None"
CONNECTOR_PERMISSION = "Allow all actions"

AUTHORIZATION_DENIAL_CODES = frozenset(
    {
        "AUTHORIZATION_REQUIRED",
        "PERMISSION_REQUIRED",
        "APPROVAL_REQUIRED",
        "SECURITY_DENIED",
        "POLICY_DENIED",
    }
)

LAUNCHER_STATE_KEYS = frozenset(
    {
        "version",
        "onboardingComplete",
        "browserSmokePassed",
        "browserSmokeVersion",
        "coreSetupComplete",
        "codexCatalogVerified",
        "codexRestartRequired",
        "mcpSetupComplete",
        "mcpRuntimeInstalled",
        "mcpGuideStep",
    }
)

CHAIN_KEYS = frozenset(
    {
        "FULL_HARNESS_TO_CODEX",
        "CODEX_TO_CODEXLOCALOPS",
        "WINDOWS_HOST_VISIBLE_THROUGH_LOCALOPS",
    }
)


@dataclass(frozen=True, slots=True)
class WebStep:
    name: str
    status: str
    detail: str
    instruction: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return sanitize(asdict(self))


@dataclass(frozen=True, slots=True)
class WebInstallation:
    status: str
    installed: bool
    install_path: str | None
    executable: str | None
    version: str | None
    version_source: str | None
    launcher_running: bool
    profile_present: bool
    launcher_state_path: str

    def to_dict(self) -> dict[str, Any]:
        return sanitize(asdict(self))


def _version_key(value: str | None) -> tuple[int, ...] | None:
    if not value:
        return None
    normalized = value.strip().lower().removeprefix("v")
    parts = normalized.split(".")
    numbers: list[int] = []
    for part in parts:
        digits = "".join(char for char in part if char.isdigit())
        if not digits:
            break
        numbers.append(int(digits))
    return tuple(numbers) if numbers else None


def version_is_older(installed: str | None, latest: str | None) -> bool | None:
    left = _version_key(installed)
    right = _version_key(latest)
    if left is None or right is None:
        return None
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) < right + (0,) * (width - len(right))


def launcher_state_path(
    *,
    user_home: Path | None = None,
    appdata: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    environment = os.environ if env is None else env
    override = environment.get("CODEX_WEB_GPT_LAUNCHER_DATA_DIR", "").strip()
    if override:
        candidate = Path(override).expanduser()
        if not candidate.is_absolute() and user_home is not None:
            candidate = Path(user_home) / candidate
        return candidate / "launcher-state.json"
    if appdata is not None:
        return Path(appdata) / "Codex Web GPT" / "launcher-state.json"
    raw_appdata = environment.get("APPDATA", "").strip()
    if raw_appdata:
        return Path(raw_appdata) / "Codex Web GPT" / "launcher-state.json"
    home = Path(user_home) if user_home is not None else Path.home()
    return home / "AppData" / "Roaming" / "Codex Web GPT" / "launcher-state.json"


def core_home(*, user_home: Path | None = None, env: Mapping[str, str] | None = None) -> Path:
    environment = os.environ if env is None else env
    override = environment.get("CODEX_CHATGPT_WEB_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    home = Path(user_home) if user_home is not None else Path.home()
    return home / ".codex-chatgpt-web"


def read_launcher_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {key: parsed[key] for key in LAUNCHER_STATE_KEYS if key in parsed}


def _registry_values() -> dict[str, str]:
    if os.name != "nt":
        return {}
    try:
        import winreg
    except ImportError:
        return {}
    keys = (
        rf"Software\{WINDOWS_LAUNCHER_GUID}",
        rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{WINDOWS_LAUNCHER_GUID}",
        rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{{{WINDOWS_LAUNCHER_GUID}}}",
    )
    found: dict[str, str] = {}
    for key_name in keys:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name) as handle:
                for value_name in ("InstallLocation", "DisplayVersion"):
                    try:
                        value, _ = winreg.QueryValueEx(handle, value_name)
                    except OSError:
                        continue
                    if isinstance(value, str) and value.strip() and value_name not in found:
                        found[value_name] = value.strip()
        except OSError:
            continue
    if "DisplayVersion" not in found or "InstallLocation" not in found:
        uninstall = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall) as root:
                index = 0
                while True:
                    try:
                        child_name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        with winreg.OpenKey(root, child_name) as child:
                            display_name, _ = winreg.QueryValueEx(child, "DisplayName")
                            if str(display_name).strip().casefold() != "codex web gpt":
                                continue
                            for value_name in ("InstallLocation", "DisplayVersion"):
                                if value_name in found:
                                    continue
                                try:
                                    value, _ = winreg.QueryValueEx(child, value_name)
                                except OSError:
                                    continue
                                if isinstance(value, str) and value.strip():
                                    found[value_name] = value.strip()
                            break
                    except OSError:
                        continue
        except OSError:
            pass
    return found


def _launcher_process() -> tuple[bool, str | None]:
    for process in psutil.process_iter(["name", "exe"]):
        try:
            name = str(process.info.get("name") or "").casefold()
            if name in {"codex web gpt.exe", "codex web gpt"}:
                executable = process.info.get("exe")
                return True, str(executable) if executable else None
        except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return False, None


def detect_windows_installation(
    *,
    user_home: Path | None = None,
    appdata: Path | None = None,
    env: Mapping[str, str] | None = None,
    registry_reader: Callable[[], dict[str, str]] | None = None,
    process_probe: Callable[[], tuple[bool, str | None]] | None = None,
) -> WebInstallation:
    state_path = launcher_state_path(user_home=user_home, appdata=appdata, env=env)
    profile_present = state_path.is_file()
    registry = (registry_reader or _registry_values)()
    running, process_executable = (process_probe or _launcher_process)()
    install_location = registry.get("InstallLocation")
    executable: Path | None = None
    if process_executable:
        executable = Path(process_executable)
        if install_location is None:
            install_location = str(executable.parent)
    elif install_location:
        executable = Path(install_location) / "Codex Web GPT.exe"
    installed = bool(running or (executable is not None and executable.is_file()))
    version = registry.get("DisplayVersion")
    return WebInstallation(
        status="PASS" if installed else "NOT_CONFIGURED",
        installed=installed,
        install_path=install_location,
        executable=str(executable) if executable is not None else None,
        version=version,
        version_source="registry" if version else None,
        launcher_running=running,
        profile_present=profile_present,
        launcher_state_path=str(state_path),
    )


def dev_interpreter_path(user_home: Path | None = None) -> Path:
    home = Path(user_home) if user_home is not None else Path.home()
    return home / ".codex-local-ops.venv" / "Scripts" / "python.exe"


def manual_validation_commands(project_path: Path) -> str:
    project = str(Path(project_path))
    return "\n".join(
        (
            f'Set-Location -LiteralPath "{project}"',
            '& "$env:USERPROFILE\\.codex-local-ops.venv\\Scripts\\python.exe" -m pytest -q',
            '& "$env:USERPROFILE\\.codex-local-ops.venv\\Scripts\\python.exe" -m ruff check .',
            '& "$env:USERPROFILE\\.codex-local-ops.venv\\Scripts\\python.exe" -m compileall -q src tests',
            "git diff --check",
        )
    )


def classify_execution_failure(code: str | None, detail: str | None = None) -> dict[str, Any]:
    normalized = str(code or "").strip().upper()
    if normalized in AUTHORIZATION_DENIAL_CODES:
        return {
            "status": "WAITING_FOR_USER",
            "classification": "authorization_denial",
            "detail": str(sanitize(detail or normalized)),
            "retry_through_other_executor": False,
        }
    return {
        "status": "WARNING",
        "classification": "harness_execution_limitation",
        "detail": str(sanitize(detail or normalized or "Web/Native2 execution failed")),
        "retry_through_other_executor": False,
    }


def _step(name: str, status: str, detail: str, instruction: str | None = None, **data: Any) -> WebStep:
    return WebStep(name, status, detail, instruction, data)


def guided_web_steps(launcher_state: Mapping[str, Any], *, latest_version: str = AUDITED_RELEASE) -> tuple[WebStep, ...]:
    smoke = launcher_state.get("browserSmokePassed") is True
    smoke_version = launcher_state.get("browserSmokeVersion")
    smoke_stale = smoke and version_is_older(str(smoke_version or ""), latest_version) is True
    core_ready = launcher_state.get("coreSetupComplete") is True
    catalog_ready = launcher_state.get("codexCatalogVerified") is True
    restart_required = launcher_state.get("codexRestartRequired") is True
    runtime_installed = launcher_state.get("mcpRuntimeInstalled") is True
    runtime_verified = launcher_state.get("mcpSetupComplete") is True

    smoke_status = "WARNING" if smoke_stale else "PASS" if smoke else "WAITING_FOR_USER"
    models_status = "PASS" if core_ready and catalog_ready and not restart_required else "WAITING_FOR_USER"
    harness_status = "PASS" if runtime_installed and runtime_verified else "WAITING_FOR_USER"
    verify_status = "PASS" if runtime_verified else "WAITING_FOR_USER"
    return (
        _step(
            "CHATGPT_LOGIN",
            "PASS" if smoke else "WAITING_FOR_USER",
            "Browser smoke test proves the launcher can use the signed-in ChatGPT session" if smoke else "ChatGPT sign-in is not yet proven",
            "Launch Codex Web GPT and sign in to ChatGPT in its embedded browser.",
        ),
        _step(
            "BROWSER_SMOKE_TEST",
            smoke_status,
            f"Browser smoke test passed for {smoke_version}" if smoke else "Browser smoke test has not passed",
            "Run Setup → browser smoke test in Codex Web GPT.",
            browser_smoke_version=smoke_version,
        ),
        _step(
            "WEB_MODELS",
            models_status,
            "Codex Web models are installed and catalog-verified" if models_status == "PASS" else "Install models and fully restart Codex until the catalog is verified",
            "Use Setup → Install models. Fully quit Codex, including background processes, then reopen it while the launcher is running.",
            restart_required=restart_required,
        ),
        _step(
            "FULL_HARNESS",
            harness_status,
            "Full Harness is installed and Verify runtime completed" if harness_status == "PASS" else "Full Harness setup is incomplete",
            "Open MCP, create the OpenAI tunnel and regular API key, then choose Connect harness.",
        ),
        _step(
            "CODEX_NATIVE2",
            verify_status,
            f"{CONNECTOR_NAME} was verified by the launcher" if runtime_verified else f"The exact connector {CONNECTOR_NAME} is not yet verified",
            f"Enable ChatGPT Developer Mode. Create a NEW connector named exactly {CONNECTOR_NAME}; Authentication: {CONNECTOR_AUTHENTICATION}; Permissions: {CONNECTOR_PERMISSION}.",
            connector_name=CONNECTOR_NAME,
            authentication=CONNECTOR_AUTHENTICATION,
            permission=CONNECTOR_PERMISSION,
        ),
        _step(
            "VERIFY_RUNTIME",
            verify_status,
            "Launcher Verify runtime succeeded" if runtime_verified else "Verify runtime has not completed",
            "After Connect harness and connector creation, run MCP → Verify runtime.",
        ),
    )


def inspect_chatgpt_web(
    *,
    installation: WebInstallation,
    launcher_state: Mapping[str, Any],
    latest_version: str = AUDITED_RELEASE,
    local_ops_status: str = "NOT_CONFIGURED",
    codex_local_ops_status: str = "NOT_CONFIGURED",
    connection_chain: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    chain = dict(connection_chain or {})
    steps = list(guided_web_steps(launcher_state, latest_version=latest_version))
    if installation.installed:
        older = version_is_older(installation.version, latest_version)
        if older is True:
            install_status = "WARNING"
            install_detail = f"Update available: {installation.version} → {latest_version}"
        elif installation.version is None:
            install_status = "WARNING"
            install_detail = "Launcher is installed but its exact installed version could not be proven"
        else:
            install_status = "PASS"
            install_detail = f"Codex Web GPT {installation.version} is installed"
    else:
        install_status = "NOT_CONFIGURED"
        install_detail = "Codex Web GPT is not installed"
    steps.insert(
        0,
        _step(
            "CODEX_WEB_GPT",
            install_status,
            install_detail,
            "Install or update with the current official Windows launcher installer.",
            installation=installation.to_dict(),
            audited_release=latest_version,
        ),
    )

    verify_runtime = next(step for step in steps if step.name == "VERIFY_RUNTIME")
    full_harness_status = "PASS" if verify_runtime.status == "PASS" else "WAITING_FOR_USER"
    local_chain_status = "PASS" if local_ops_status == "PASS" and codex_local_ops_status == "PASS" else "FAIL"
    saved_e2e = chain.get("WINDOWS_HOST_VISIBLE_THROUGH_LOCALOPS")
    e2e_status = "PASS" if saved_e2e == "PASS" else "WAITING_FOR_USER"
    chain_steps = (
        _step(
            "FULL_HARNESS_TO_CODEX",
            full_harness_status,
            "Full Harness to Codex was verified" if full_harness_status == "PASS" else "Complete Full Harness and Verify runtime",
        ),
        _step(
            "CODEX_TO_CODEXLOCALOPS",
            local_chain_status,
            "Codex and codexLocalOps are healthy" if local_chain_status == "PASS" else "Local Codex/codexLocalOps health is incomplete",
        ),
        _step(
            "WINDOWS_HOST_VISIBLE_THROUGH_LOCALOPS",
            e2e_status,
            "End-to-end Web → Windows proof was recorded" if e2e_status == "PASS" else "A live read-only Web → Native2 → Full Harness → Codex → codexLocalOps → Windows proof is still required",
            "Run a read-only Native2 call that returns Windows host evidence through codexLocalOps, then record the successful result.",
        ),
    )
    steps.extend(chain_steps)
    required = [step for step in steps if step.status != "PASS"]
    return sanitize(
        {
            "mode": "WEB_STATUS",
            "status": "READY" if not required else required[0].status,
            "ready": not required,
            "incomplete_step": required[0].name if required else None,
            "latest_audited_release": latest_version,
            "release_audit_date": AUDITED_RELEASE_DATE,
            "steps": [step.to_dict() for step in steps],
        }
    )


def plan_web_install_update(status: Mapping[str, Any]) -> dict[str, Any]:
    codex_web = next(
        (item for item in status.get("steps", []) if isinstance(item, dict) and item.get("name") == "CODEX_WEB_GPT"),
        {},
    )
    step_status = codex_web.get("status")
    action = "INSTALL" if step_status == "NOT_CONFIGURED" else "UPDATE" if step_status == "WARNING" else "NONE"
    return sanitize(
        {
            "mode": "WEB_PLAN",
            "status": "PASS" if action == "NONE" else "WAITING_FOR_USER",
            "action": action,
            "official_repository": CODEX_WEB_REPOSITORY,
            "release": status.get("latest_audited_release", AUDITED_RELEASE),
            "installer_url": WINDOWS_INSTALLER_URL,
            "powershell": WINDOWS_INSTALLER_COMMAND,
            "installer_behavior": {
                "official_release_assets": True,
                "sha256_manifest_verified": True,
                "per_user_install": True,
                "preserve_launcher_settings": True,
                "preserve_chatgpt_profile": True,
                "uninstall_first": False,
            },
            "guidance": [step for step in status.get("steps", []) if isinstance(step, dict)],
        }
    )


def detect_web_repairs(status: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    findings: list[dict[str, Any]] = []
    for step in status.get("steps", []):
        if not isinstance(step, dict) or step.get("status") == "PASS":
            continue
        name = str(step.get("name") or "WEB_INTEGRATION")
        findings.append(
            {
                "code": name,
                "status": step.get("status"),
                "classification": "user action required",
                "detail": step.get("detail"),
                "instruction": step.get("instruction"),
                "repair_scope": "chatgpt-web",
            }
        )
    return tuple(sanitize(findings))


def record_connection_verification(*, state_path: Path, results: Mapping[str, str]) -> dict[str, Any]:
    unknown = set(results) - CHAIN_KEYS
    if unknown:
        raise ValueError(f"Unsupported connection verification keys: {', '.join(sorted(unknown))}")
    normalized: dict[str, str] = {}
    for key, value in results.items():
        status = str(value).upper()
        if status not in {"PASS", "FAIL", "WAITING_FOR_USER"}:
            raise ValueError(f"Unsupported verification status for {key}: {value}")
        normalized[key] = status
    state = load_setup_state(state_path)
    for key, status in normalized.items():
        state.connection_chain[key] = status
        state.connection_chain[f"{key}_verified_at"] = utc_timestamp()
    save_setup_state(state, state_path)
    return sanitize({"status": "PASS", "recorded": normalized})
