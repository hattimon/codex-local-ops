from __future__ import annotations

import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .agents_config import (
    AgentsConfigError,
    AgentsConfigResult,
    agents_backup_root,
    agents_path,
    inspect_managed_agents,
    restore_managed_agents,
    update_managed_agents,
)
from .backup_ops import BackupError, BackupRecord, TransactionWriteError, create_backup, restore_backup
from .bridge_ops import (
    AUDITED_RELEASE,
    WebInstallation,
    detect_web_repairs,
    detect_windows_installation,
    inspect_chatgpt_web,
    plan_web_install_update,
    read_launcher_state,
    record_connection_verification,
)
from .codex_config import (
    CodexConfigError,
    codex_config_backup_root,
    codex_config_path,
    inspect_codex_mcp,
    restore_codex_config,
    update_codex_mcp,
)
from .config import config_path as local_config_path
from .runtime_ops import (
    ActivationResult,
    CandidateRuntime,
    RuntimeOperationError,
    ValidationCheck,
    ValidationReport,
    activate_candidate,
    cleanup_candidate,
    detect_repair_needs,
    normalize_features,
    rollback_activation,
    runtime_python,
    stage_candidate_runtime,
    validate_candidate,
    validate_runtime,
)
from .safety import sanitize
from .setup_state import (
    BackupReference,
    ManagedAgentsState,
    SetupLock,
    SetupLockError,
    SetupState,
    SetupStateError,
    load_setup_state,
    new_transaction_id,
    save_setup_state,
    setup_root,
    setup_state_path,
    stable_runtime_path,
)
from .trusted_roots import (
    TrustedRootError,
    add_trusted_root,
    inspect_trusted_roots,
    normalize_trusted_root,
    restore_trusted_roots,
    trusted_roots_backup_root,
)

RESULT_STATUSES = {"PASS", "WARNING", "FAIL", "NOT_CONFIGURED", "WAITING_FOR_USER"}
REPAIR_CLASSIFICATIONS = {
    "healthy",
    "automatic repair possible",
    "user action required",
    "unsafe/ambiguous",
}


@dataclass(frozen=True, slots=True)
class SetupContext:
    setup_dir: Path
    state_path: Path
    active_runtime_path: Path
    codex_config_path: Path
    codex_backup_root: Path
    agents_path: Path
    agents_backup_root: Path
    local_config_path: Path
    trusted_roots_backup_root: Path
    state_backup_root: Path


@dataclass(frozen=True, slots=True)
class SetupStep:
    name: str
    status: str
    detail: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return sanitize(asdict(self))


@dataclass(frozen=True, slots=True)
class SetupPlan:
    mode: str
    status: str
    steps: tuple[SetupStep, ...]
    required_inputs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return sanitize(
            {
                "mode": self.mode,
                "status": self.status,
                "steps": [step.to_dict() for step in self.steps],
                "required_inputs": list(self.required_inputs),
            }
        )


@dataclass(frozen=True, slots=True)
class RepairFinding:
    code: str
    status: str
    classification: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return sanitize(asdict(self))


@dataclass(frozen=True, slots=True)
class RepairAssessment:
    status: str
    findings: tuple[RepairFinding, ...]

    def to_dict(self) -> dict[str, Any]:
        return sanitize(
            {
                "status": self.status,
                "findings": [finding.to_dict() for finding in self.findings],
            }
        )


@dataclass(frozen=True, slots=True)
class LifecycleResult:
    mode: str
    status: str
    steps: tuple[SetupStep, ...]
    transaction_id: str | None = None
    activation_record_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return sanitize(
            {
                "mode": self.mode,
                "status": self.status,
                "transaction_id": self.transaction_id,
                "activation_record_path": self.activation_record_path,
                "steps": [step.to_dict() for step in self.steps],
            }
        )


def default_context(user_home: Path | None = None) -> SetupContext:
    home = Path(user_home) if user_home is not None else Path.home()
    if user_home is None:
        root = setup_root()
        local_config = local_config_path()
    else:
        local_ops_home = home / ".codex-local-ops"
        root = setup_root(local_ops_home / "setup")
        local_config = local_ops_home / "config" / "config.yaml"
    return SetupContext(
        setup_dir=root,
        state_path=setup_state_path(root),
        active_runtime_path=stable_runtime_path(home),
        codex_config_path=codex_config_path(home),
        codex_backup_root=codex_config_backup_root(root / "backups" / "codex-config"),
        agents_path=agents_path(home),
        agents_backup_root=agents_backup_root(root / "backups" / "agents"),
        local_config_path=local_config,
        trusted_roots_backup_root=trusted_roots_backup_root(root / "backups" / "local-config"),
        state_backup_root=root / "backups" / "setup-state",
    )


def _overall(statuses: list[str]) -> str:
    priority = {
        "PASS": 0,
        "NOT_CONFIGURED": 1,
        "WARNING": 2,
        "WAITING_FOR_USER": 3,
        "FAIL": 4,
    }
    valid = [status for status in statuses if status in RESULT_STATUSES]
    return max(valid or ["PASS"], key=lambda status: priority[status])


def discover_system() -> SetupStep:
    python_ok = sys.version_info >= (3, 11)
    windows = platform.system() == "Windows"
    status = "PASS" if python_ok and windows else "FAIL" if not python_ok else "WARNING"
    detail = None
    if not python_ok:
        detail = "Python 3.11 or newer is required"
    elif not windows:
        detail = "Setup Assistant is Windows-first; this host is supported for planning only"
    return SetupStep(
        "system_discovery",
        status,
        detail,
        {
            "os": platform.system(),
            "os_version": platform.version(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "python_suitable": python_ok,
        },
    )


def _wheel_step(wheel_path: Path | None) -> SetupStep:
    if wheel_path is None:
        return SetupStep("package_selection", "WAITING_FOR_USER", "Explicit wheel selection is required")
    wheel = Path(wheel_path)
    if not wheel.is_file() or wheel.suffix.lower() != ".whl":
        return SetupStep("package_selection", "FAIL", f"Selected wheel is invalid: {wheel}")
    return SetupStep("package_selection", "PASS", data={"wheel": str(wheel.resolve())})


def _trusted_root_step(trusted_root: Path | None) -> SetupStep:
    if trusted_root is None:
        return SetupStep("trusted_root_setup", "WAITING_FOR_USER", "Explicit project root selection is required")
    try:
        selected = normalize_trusted_root(trusted_root)
    except (TrustedRootError, FileNotFoundError, NotADirectoryError) as exc:
        return SetupStep("trusted_root_setup", "FAIL", str(exc))
    return SetupStep("trusted_root_setup", "PASS", data={"root": str(selected)})


def _planned(name: str, ready: bool, detail: str) -> SetupStep:
    return SetupStep(name, "PASS" if ready else "NOT_CONFIGURED", detail)


def plan_install(
    *,
    wheel_path: Path | None,
    trusted_root: Path | None,
    features: list[str] | tuple[str, ...] | set[str] | None = None,
) -> SetupPlan:
    normalize_features(features)
    system = discover_system()
    wheel = _wheel_step(wheel_path)
    root = _trusted_root_step(trusted_root)
    ready = wheel.status == "PASS" and root.status == "PASS" and system.status != "FAIL"
    steps = (
        system,
        wheel,
        _planned("candidate_staging", wheel.status == "PASS", "Fresh non-editable wheel runtime"),
        _planned("candidate_validation", wheel.status == "PASS", "Validation gate before activation"),
        _planned("activation", wheel.status == "PASS", "Fixed stable runtime path"),
        _planned("mcp_registration", wheel.status == "PASS", "Register codexLocalOps after activation"),
        _planned("managed_agents", wheel.status == "PASS", "Update only the managed AGENTS.md block"),
        root,
        _planned("setup_state_persistence", ready, "Persist lifecycle and rollback metadata"),
    )
    required = tuple(
        item
        for item, status in (("wheel_path", wheel.status), ("trusted_root", root.status))
        if status == "WAITING_FOR_USER"
    )
    return SetupPlan("INSTALL", _overall([system.status, wheel.status, root.status]), steps, required)


def _load_state_for_plan(context: SetupContext) -> tuple[SetupState | None, SetupStep]:
    try:
        state = load_setup_state(context.state_path)
    except SetupStateError as exc:
        return None, SetupStep("setup_state", "FAIL", str(exc))
    if state.active_runtime is None:
        return state, SetupStep("current_runtime", "NOT_CONFIGURED", "No active runtime is recorded")
    if not context.active_runtime_path.is_dir():
        return state, SetupStep("current_runtime", "FAIL", f"Missing {context.active_runtime_path}")
    return state, SetupStep("current_runtime", "PASS", data={"runtime": str(context.active_runtime_path)})


def plan_update(
    *,
    context: SetupContext,
    wheel_path: Path | None,
    features: list[str] | tuple[str, ...] | set[str] | None = None,
) -> SetupPlan:
    normalize_features(features)
    state, current = _load_state_for_plan(context)
    wheel = _wheel_step(wheel_path)
    ready = current.status == "PASS" and wheel.status == "PASS"
    steps = (
        current,
        wheel,
        _planned("candidate_staging", wheel.status == "PASS", "Stage a fresh runtime beside the active runtime"),
        _planned("candidate_validation", wheel.status == "PASS", "Validate before replacing the active runtime"),
        _planned("rollback_point", ready, "Retain the current runtime as the rollback target"),
        _planned("activation", ready, "Swap only after candidate validation passes"),
        _planned("mcp_registration", ready, "Ensure MCP points to the stable runtime path"),
        _planned("active_runtime_validation", ready, "Validate the runtime again after the swap"),
        _planned("setup_state_persistence", ready, "Persist update and rollback metadata"),
    )
    required = ("wheel_path",) if wheel.status == "WAITING_FOR_USER" else ()
    status = _overall([current.status, wheel.status])
    if state is not None and state.status == "RECOVERY_REQUIRED":
        status = "FAIL"
    return SetupPlan("UPDATE", status, steps, required)


def plan_rollback(*, context: SetupContext) -> SetupPlan:
    try:
        state = load_setup_state(context.state_path)
    except SetupStateError as exc:
        return SetupPlan("ROLLBACK", "FAIL", (SetupStep("rollback_state", "FAIL", str(exc)),))
    record = state.rollback.activation_record_path
    eligible = state.rollback.eligible and bool(record)
    if not eligible:
        return SetupPlan(
            "ROLLBACK",
            "NOT_CONFIGURED",
            (SetupStep("rollback_point", "NOT_CONFIGURED", state.rollback.reason or "No rollback point is available"),),
        )
    record_path = Path(record)
    if not record_path.is_file():
        return SetupPlan(
            "ROLLBACK",
            "FAIL",
            (SetupStep("rollback_point", "FAIL", f"Activation record is missing: {record_path}"),),
        )
    steps = (
        SetupStep("rollback_point", "PASS", data={"activation_record": str(record_path)}),
        SetupStep("restore_previous_runtime", "PASS", "Restore retained runtime"),
        SetupStep("restore_mcp_config", "PASS", "Restore MCP configuration from activation backup"),
        SetupStep("restore_setup_state", "PASS", "Restore pre-activation setup state"),
        SetupStep("restore_managed_configuration", "PASS", "Restore tracked AGENTS/local config backups"),
    )
    return SetupPlan("ROLLBACK", "PASS", steps)


def _validation_from_state(state: SetupState) -> ValidationReport | None:
    data = state.validation_summary
    if not isinstance(data, dict) or not data.get("runtime_path"):
        return None
    checks_data = data.get("checks") or []
    if not isinstance(checks_data, list):
        return None
    try:
        checks = tuple(
            ValidationCheck(
                str(item["name"]),
                str(item["status"]),
                item.get("detail"),
                dict(item.get("data") or {}),
            )
            for item in checks_data
            if isinstance(item, dict)
        )
        return ValidationReport(
            str(data.get("status") or "FAIL"),
            str(data["runtime_path"]),
            checks,
            data.get("package_version"),
            data.get("python_version"),
            str(data.get("validated_at") or "unknown"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _repair_classification(code: str) -> tuple[str, str]:
    if code in {"INTERRUPTED_UPDATE", "CODEX_CONFIG_INVALID"}:
        return "FAIL", "unsafe/ambiguous"
    if code in {"MISSING_TRUSTED_ROOT"}:
        return "WAITING_FOR_USER", "user action required"
    return "WARNING", "automatic repair possible"


def detect_repairs(
    *,
    context: SetupContext,
    selected_features: list[str] | tuple[str, ...] | set[str] | None = None,
    platform_name: str | None = None,
) -> RepairAssessment:
    selected = normalize_features(selected_features)
    try:
        state = load_setup_state(context.state_path)
    except SetupStateError as exc:
        finding = RepairFinding("SETUP_STATE_INVALID", "FAIL", "unsafe/ambiguous", str(exc))
        return RepairAssessment("FAIL", (finding,))

    validation = _validation_from_state(state)
    report = detect_repair_needs(
        active_runtime_path=context.active_runtime_path,
        codex_config_path=context.codex_config_path,
        setup_dir=context.setup_dir,
        selected_features=selected,
        validation=validation,
        platform_name=platform_name,
    )
    findings: list[RepairFinding] = []
    for issue in report.issues:
        status, classification = _repair_classification(issue.code)
        findings.append(RepairFinding(issue.code, status, classification, issue.detail))

    agents = inspect_managed_agents(path=context.agents_path)
    if not agents.valid:
        findings.append(RepairFinding("AGENTS_CONFIG_INVALID", "FAIL", "unsafe/ambiguous", agents.error or agents.path))
    elif not agents.current:
        findings.append(
            RepairFinding(
                "MANAGED_AGENTS_MISSING",
                "WARNING",
                "automatic repair possible",
                "Managed AGENTS.md policy is missing or outdated",
            )
        )

    roots = inspect_trusted_roots(config_file=context.local_config_path)
    if not roots.valid:
        findings.append(RepairFinding("LOCAL_CONFIG_INVALID", "FAIL", "unsafe/ambiguous", roots.error or roots.path))
    elif not roots.roots:
        findings.append(
            RepairFinding(
                "MISSING_TRUSTED_ROOT",
                "WAITING_FOR_USER",
                "user action required",
                "A project root must be selected explicitly",
            )
        )

    if not findings:
        return RepairAssessment(
            "PASS",
            (RepairFinding("HEALTHY", "PASS", "healthy", "No repair findings"),),
        )
    return RepairAssessment(_overall([finding.status for finding in findings]), tuple(findings))


def plan_repair(
    *,
    context: SetupContext,
    selected_features: list[str] | tuple[str, ...] | set[str] | None = None,
    platform_name: str | None = None,
) -> SetupPlan:
    assessment = detect_repairs(
        context=context,
        selected_features=selected_features,
        platform_name=platform_name,
    )
    steps = tuple(
        SetupStep(
            finding.code.lower(),
            finding.status,
            finding.detail,
            {"classification": finding.classification},
        )
        for finding in assessment.findings
    )
    return SetupPlan("REPAIR", assessment.status, steps)


def _command_capability(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    return {"status": "PASS" if path else "WARNING", "path": path}


def _stored_check(state: SetupState, name: str) -> dict[str, Any] | None:
    checks = state.validation_summary.get("checks") if isinstance(state.validation_summary, dict) else None
    if not isinstance(checks, list):
        return None
    for item in checks:
        if isinstance(item, dict) and item.get("name") == name:
            return dict(item)
    return None


def _stored_feature_capability(state: SetupState, name: str, *, required: bool) -> dict[str, Any]:
    check = _stored_check(state, name)
    if check is None:
        return {
            "status": "FAIL" if required else "WARNING",
            "source": "not validated",
        }
    passed = check.get("status") == "PASS"
    return {
        "status": "PASS" if passed else "FAIL" if required else "WARNING",
        "source": "stored runtime validation",
        "detail": check.get("detail"),
    }


def run_setup_diagnostics(
    *,
    context: SetupContext,
    selected_root: Path | None = None,
    selected_features: list[str] | tuple[str, ...] | set[str] | None = None,
) -> dict[str, Any]:
    selected = normalize_features(selected_features)
    system_step = discover_system()
    try:
        state = load_setup_state(context.state_path)
        state_error = None
    except SetupStateError as exc:
        state = SetupState(status="RECOVERY_REQUIRED")
        state_error = str(exc)

    active_exists = context.active_runtime_path.is_dir()
    candidates = sorted(str(path) for path in (context.setup_dir / "staging").glob("*") if path.is_dir())
    local_status = "FAIL" if state_error else "PASS" if state.active_runtime and active_exists else "NOT_CONFIGURED"
    local_ops = {
        "status": local_status,
        "setup_state": state.status,
        "state_path": str(context.state_path),
        "state_error": state_error,
        "active_runtime": str(context.active_runtime_path),
        "active_runtime_exists": active_exists,
        "candidate_runtimes": candidates,
        "validation_status": state.validation_summary.get("status") if state.validation_summary else None,
    }

    expected_python = runtime_python(context.active_runtime_path)
    mcp = inspect_codex_mcp(path=context.codex_config_path, expected_runtime_python=expected_python)
    agents = inspect_managed_agents(path=context.agents_path)
    codex_statuses = [
        "FAIL" if not mcp.valid else "PASS" if mcp.matches_expected else "NOT_CONFIGURED",
        "FAIL" if not agents.valid else "PASS" if agents.current else "NOT_CONFIGURED",
    ]
    codex = {
        "status": _overall(codex_statuses),
        "config_toml": mcp.path,
        "config_valid": mcp.valid,
        "codexLocalOps_registered": mcp.registered,
        "expected_runtime_python": str(expected_python),
        "runtime_matches": mcp.matches_expected,
        "agents_path": agents.path,
        "agents_managed": agents.installed,
        "agents_current": agents.current,
        "agents_block_version": agents.block_version,
    }

    roots = inspect_trusted_roots(config_file=context.local_config_path)
    selected_exists = selected_root is not None and Path(selected_root).expanduser().exists()
    workspace_status = "FAIL" if not roots.valid else "PASS" if roots.roots else "WAITING_FOR_USER"
    workspace = {
        "status": workspace_status,
        "trusted_roots": list(roots.roots),
        "selected_root": str(selected_root) if selected_root is not None else None,
        "selected_root_exists": selected_exists if selected_root is not None else None,
    }

    capabilities: dict[str, Any] = {
        "git": _command_capability("git"),
        "github_cli": _command_capability("gh"),
        "docker": _command_capability("docker"),
        "ssh": _command_capability("ssh"),
        "scp": _command_capability("scp"),
        "sftp": _command_capability("sftp"),
        "wsl": _command_capability("wsl"),
        "ffmpeg": _command_capability("ffmpeg"),
        "ffprobe": _command_capability("ffprobe"),
    }
    browser_required = "browser" in selected
    desktop_required = "desktop" in selected
    windows_required = "windows" in selected
    capabilities["browser_package"] = _stored_feature_capability(
        state,
        "feature_browser",
        required=browser_required,
    )
    capabilities["desktop_capture"] = _stored_feature_capability(
        state,
        "feature_desktop",
        required=desktop_required,
    )
    chromium = _stored_check(state, "playwright_chromium")
    chromium_ok = bool(chromium and chromium.get("status") == "PASS")
    capabilities["playwright_chromium"] = {
        "status": "PASS" if chromium_ok else "FAIL" if browser_required else "WARNING",
        "source": "stored runtime validation" if chromium is not None else "not validated",
    }
    capabilities["desktop_ui_automation"] = _stored_feature_capability(
        state,
        "feature_windows",
        required=windows_required,
    )
    capability_status = _overall([item["status"] for item in capabilities.values()])
    if capability_status == "FAIL" and not (browser_required or desktop_required):
        capability_status = "WARNING"
    capabilities_section = {"status": capability_status, "checks": capabilities}

    sections = {
        "SYSTEM": {"status": system_step.status, **system_step.data, "detail": system_step.detail},
        "LOCAL_OPS": local_ops,
        "CODEX": codex,
        "WORKSPACE": workspace,
        "CAPABILITIES": capabilities_section,
    }
    return sanitize(
        {
            "mode": "DIAGNOSTICS",
            "status": _overall([section["status"] for section in sections.values()]),
            "sections": sections,
        }
    )


def run_web_setup_status(
    *,
    context: SetupContext,
    user_home: Path | None = None,
    appdata: Path | None = None,
    latest_version: str = AUDITED_RELEASE,
    installation: WebInstallation | None = None,
    launcher_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the Web/Native2 setup chain without mutating launcher or ChatGPT state."""
    local = run_setup_diagnostics(context=context)
    home = Path(user_home) if user_home is not None else context.active_runtime_path.parent
    detected = installation or detect_windows_installation(user_home=home, appdata=appdata)
    observed_launcher_state = (
        dict(launcher_state)
        if launcher_state is not None
        else read_launcher_state(Path(detected.launcher_state_path))
    )
    try:
        state = load_setup_state(context.state_path)
        connection_chain = state.connection_chain
    except SetupStateError:
        connection_chain = {}
    sections = local.get("sections", {})
    return inspect_chatgpt_web(
        installation=detected,
        launcher_state=observed_launcher_state,
        latest_version=latest_version,
        local_ops_status=str(sections.get("LOCAL_OPS", {}).get("status", "NOT_CONFIGURED")),
        codex_local_ops_status=str(sections.get("CODEX", {}).get("status", "NOT_CONFIGURED")),
        connection_chain=connection_chain,
    )


def plan_web_setup(
    *,
    context: SetupContext,
    user_home: Path | None = None,
    appdata: Path | None = None,
    latest_version: str = AUDITED_RELEASE,
    installation: WebInstallation | None = None,
    launcher_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = run_web_setup_status(
        context=context,
        user_home=user_home,
        appdata=appdata,
        latest_version=latest_version,
        installation=installation,
        launcher_state=launcher_state,
    )
    return plan_web_install_update(status)


def plan_web_repair(
    *,
    context: SetupContext,
    user_home: Path | None = None,
    appdata: Path | None = None,
    latest_version: str = AUDITED_RELEASE,
    installation: WebInstallation | None = None,
    launcher_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status = run_web_setup_status(
        context=context,
        user_home=user_home,
        appdata=appdata,
        latest_version=latest_version,
        installation=installation,
        launcher_state=launcher_state,
    )
    findings = detect_web_repairs(status)
    local = run_setup_diagnostics(context=context)
    return sanitize(
        {
            "mode": "WEB_REPAIR",
            "status": "PASS" if not findings else "WAITING_FOR_USER",
            "local_ops_health": local.get("sections", {}).get("LOCAL_OPS", {}),
            "findings": list(findings),
            "rule": "Web integration findings do not trigger Local Ops runtime reinstall.",
        }
    )


def record_web_verification(*, context: SetupContext, results: dict[str, str]) -> dict[str, Any]:
    """Persist only whitelisted verification outcomes in Setup Assistant state."""
    return record_connection_verification(state_path=context.state_path, results=results)


def _backup_reference(record: BackupRecord) -> BackupReference:
    return BackupReference(
        kind=record.kind,
        backup_id=record.backup_id,
        target_path=record.target_path,
        backup_path=record.backup_path,
        sha256=record.sha256,
        existed=record.existed,
        transaction_id=record.transaction_id,
        size=record.size,
        created_at=record.created_at,
    )


def _backup_record(reference: BackupReference | None) -> BackupRecord | None:
    if reference is None or reference.transaction_id is None or reference.created_at is None:
        return None
    return BackupRecord(
        backup_id=reference.backup_id,
        transaction_id=reference.transaction_id,
        kind=reference.kind,
        target_path=reference.target_path,
        backup_path=reference.backup_path,
        existed=reference.existed,
        sha256=reference.sha256,
        size=reference.size,
        created_at=reference.created_at,
    )


def _find_backup(state: SetupState, backup_id: str | None) -> BackupRecord | None:
    if backup_id is None:
        return None
    for reference in state.config_backups:
        if reference.backup_id == backup_id:
            return _backup_record(reference)
    return None


def _track_backup(state: SetupState, record: BackupRecord | None) -> None:
    if record is None:
        return
    if any(item.backup_id == record.backup_id for item in state.config_backups):
        return
    state.config_backups.append(_backup_reference(record))


def _restore_post_activation(
    activation: ActivationResult,
    context: SetupContext,
    *,
    agents_result: AgentsConfigResult | None,
    trusted_backup: BackupRecord | None,
) -> list[str]:
    errors: list[str] = []
    if trusted_backup is not None:
        try:
            restore_trusted_roots(trusted_backup)
        except (TrustedRootError, BackupError, OSError) as exc:
            errors.append(f"trusted roots: {exc}")
    if agents_result is not None and agents_result.backup is not None:
        try:
            restore_managed_agents(agents_result.backup)
        except (BackupError, OSError, RuntimeError) as exc:
            errors.append(f"AGENTS.md: {exc}")

    try:
        if activation.previous_runtime is not None:
            rollback_activation(Path(activation.activation_record_path), state_path=context.state_path)
        else:
            failed = context.setup_dir / "failed-activations" / activation.transaction_id / "runtime.venv"
            if context.active_runtime_path.exists():
                failed.parent.mkdir(parents=True, exist_ok=True)
                os.replace(context.active_runtime_path, failed)
            if activation.config_backup is not None:
                restore_codex_config(activation.config_backup)
            if activation.state_backup is not None:
                restore_backup(activation.state_backup)
    except (RuntimeOperationError, CodexConfigError, BackupError, OSError) as exc:
        errors.append(f"runtime activation: {exc}")
    return errors


def _execute_candidate_lifecycle(
    mode: str,
    *,
    context: SetupContext,
    base_python: Path,
    wheel_path: Path,
    features: list[str] | tuple[str, ...] | set[str] | None,
    trusted_root: Path | None = None,
    expected_version: str | None = None,
    platform_name: str | None = None,
) -> LifecycleResult:
    selected = normalize_features(features)
    plan = (
        plan_install(wheel_path=wheel_path, trusted_root=trusted_root, features=selected)
        if mode == "INSTALL"
        else plan_update(context=context, wheel_path=wheel_path, features=selected)
    )
    if plan.status in {"FAIL", "WAITING_FOR_USER", "NOT_CONFIGURED"}:
        return LifecycleResult(mode, plan.status, plan.steps)

    tx = new_transaction_id(mode)
    steps: list[SetupStep] = [SetupStep("system_discovery", "PASS")]
    activation: ActivationResult | None = None
    agents_result: AgentsConfigResult | None = None
    trusted_backup: BackupRecord | None = None
    candidate: CandidateRuntime | None = None
    with SetupLock(context.setup_dir / "setup.lock"):
        try:
            candidate = stage_candidate_runtime(
                base_python=base_python,
                wheel_path=wheel_path,
                setup_dir=context.setup_dir,
                transaction_id=tx,
                features=selected,
                expected_version=expected_version,
            )
            steps.append(SetupStep("candidate_staging", "PASS", data={"runtime": candidate.runtime_path}))
            validation = validate_candidate(candidate, platform_name=platform_name)
            steps.append(SetupStep("candidate_validation", validation.status, data=validation.to_dict()))
            if not validation.activatable:
                cleanup_candidate(candidate)
                return LifecycleResult(mode, "FAIL", tuple(steps), tx)

            activation = activate_candidate(
                candidate,
                validation,
                setup_dir=context.setup_dir,
                active_runtime_path=context.active_runtime_path,
                codex_config_path=context.codex_config_path,
                codex_backup_root=context.codex_backup_root,
                state_path=context.state_path,
                state_backup_root=context.state_backup_root,
            )
            steps.append(SetupStep("activation", "PASS", data={"runtime": str(context.active_runtime_path)}))
            steps.append(SetupStep("mcp_registration", "PASS", data={"config": str(context.codex_config_path)}))

            if mode == "UPDATE":
                active_validation = validate_runtime(
                    context.active_runtime_path,
                    features=selected,
                    expected_version=expected_version,
                    platform_name=platform_name,
                )
                steps.append(
                    SetupStep("active_runtime_validation", active_validation.status, data=active_validation.to_dict())
                )
                if not active_validation.activatable:
                    rollback_activation(Path(activation.activation_record_path), state_path=context.state_path)
                    return LifecycleResult(mode, "FAIL", tuple(steps), tx, activation.activation_record_path)

            agents_result = update_managed_agents(
                path=context.agents_path,
                backup_root=context.agents_backup_root,
                transaction_id=tx,
            )
            steps.append(
                SetupStep(
                    "managed_agents",
                    "PASS",
                    "Managed policy updated" if agents_result.changed else "Managed policy already current",
                )
            )

            if mode == "INSTALL":
                trusted_result = add_trusted_root(
                    trusted_root,
                    config_file=context.local_config_path,
                    backup_root=context.trusted_roots_backup_root,
                    transaction_id=tx,
                )
                trusted_backup = trusted_result.backup
                steps.append(
                    SetupStep(
                        "trusted_root_setup",
                        "PASS",
                        "Trusted root added" if trusted_result.changed else "Trusted root already configured",
                        {"root": trusted_result.root},
                    )
                )

            state = load_setup_state(context.state_path)
            if agents_result.backup is not None:
                _track_backup(state, agents_result.backup)
                state.rollback.agents_backup_id = agents_result.backup.backup_id
            state.agents = ManagedAgentsState(
                target_path=agents_result.path,
                block_version=agents_result.block_version,
                installed=True,
                backup_id=agents_result.backup.backup_id if agents_result.backup is not None else None,
            )
            if trusted_backup is not None:
                _track_backup(state, trusted_backup)
                state.rollback.setup_config_backup_id = trusted_backup.backup_id
            state.last_successful_operation = mode
            state.transaction.operation = mode
            state.transaction.phase = "LIFECYCLE_COMMITTED"
            save_setup_state(state, context.state_path)
            steps.append(SetupStep("setup_state_persistence", "PASS", data={"state": str(context.state_path)}))
            return LifecycleResult(mode, "PASS", tuple(steps), tx, activation.activation_record_path)
        except (RuntimeOperationError, TrustedRootError, CodexConfigError, BackupError, OSError, RuntimeError) as exc:
            if activation is not None:
                recovery_errors = _restore_post_activation(
                    activation,
                    context,
                    agents_result=agents_result,
                    trusted_backup=trusted_backup,
                )
                detail = str(exc)
                if recovery_errors:
                    detail = f"{detail}; recovery: {'; '.join(recovery_errors)}"
                steps.append(SetupStep("rollback_after_failure", "FAIL" if recovery_errors else "PASS", detail))
            elif candidate is not None:
                try:
                    cleanup_candidate(candidate)
                except RuntimeOperationError:
                    pass
            steps.append(SetupStep("lifecycle_failure", "FAIL", str(exc)))
            return LifecycleResult(mode, "FAIL", tuple(steps), tx, activation.activation_record_path if activation else None)


def execute_install(
    *,
    context: SetupContext,
    base_python: Path,
    wheel_path: Path,
    trusted_root: Path,
    features: list[str] | tuple[str, ...] | set[str] | None = None,
    expected_version: str | None = None,
    platform_name: str | None = None,
) -> LifecycleResult:
    return _execute_candidate_lifecycle(
        "INSTALL",
        context=context,
        base_python=base_python,
        wheel_path=wheel_path,
        trusted_root=trusted_root,
        features=features,
        expected_version=expected_version,
        platform_name=platform_name,
    )


def execute_update(
    *,
    context: SetupContext,
    base_python: Path,
    wheel_path: Path,
    features: list[str] | tuple[str, ...] | set[str] | None = None,
    expected_version: str | None = None,
    platform_name: str | None = None,
) -> LifecycleResult:
    return _execute_candidate_lifecycle(
        "UPDATE",
        context=context,
        base_python=base_python,
        wheel_path=wheel_path,
        features=features,
        expected_version=expected_version,
        platform_name=platform_name,
    )


def execute_rollback(*, context: SetupContext) -> LifecycleResult:
    plan = plan_rollback(context=context)
    if plan.status != "PASS":
        return LifecycleResult("ROLLBACK", plan.status, plan.steps)
    state = load_setup_state(context.state_path)
    agents_backup = _find_backup(state, state.rollback.agents_backup_id)
    config_backup = _find_backup(state, state.rollback.setup_config_backup_id)
    activation_path = Path(state.rollback.activation_record_path or "")
    steps: list[SetupStep] = []
    tx = new_transaction_id("ROLLBACK")
    current_backups: list[BackupRecord] = []
    try:
        with SetupLock(context.setup_dir / "setup.lock"):
            rollback_backup_root = context.setup_dir / "backups" / "rollback-current"
            current_backups.extend(
                (
                    create_backup(
                        context.agents_path,
                        rollback_backup_root,
                        transaction_id=tx,
                        kind="rollback-current-agents",
                    ),
                    create_backup(
                        context.local_config_path,
                        rollback_backup_root,
                        transaction_id=tx,
                        kind="rollback-current-local-config",
                    ),
                )
            )
            try:
                if config_backup is not None:
                    restore_trusted_roots(config_backup)
                steps.append(SetupStep("restore_trusted_roots", "PASS"))
                if agents_backup is not None:
                    restore_managed_agents(agents_backup)
                steps.append(SetupStep("restore_managed_agents", "PASS"))
                restored = rollback_activation(activation_path, state_path=context.state_path)
                steps.append(
                    SetupStep(
                        "restore_previous_runtime",
                        "PASS",
                        data={"runtime": restored.runtime_path if restored is not None else None},
                    )
                )
                return LifecycleResult(
                    "ROLLBACK",
                    "PASS",
                    tuple(steps),
                    tx,
                    str(activation_path),
                )
            except (RuntimeOperationError, TrustedRootError, AgentsConfigError, BackupError, OSError) as exc:
                recovery_errors: list[str] = []
                for backup in reversed(current_backups):
                    try:
                        restore_backup(backup)
                    except (BackupError, OSError) as restore_exc:
                        recovery_errors.append(str(sanitize(str(restore_exc))))
                detail = str(sanitize(str(exc)))
                if recovery_errors:
                    detail = f"{detail}; recovery: {'; '.join(recovery_errors)}"
                steps.append(SetupStep("rollback_failure", "FAIL", detail))
                return LifecycleResult(
                    "ROLLBACK",
                    "FAIL",
                    tuple(steps),
                    tx,
                    str(activation_path),
                )
    except (SetupLockError, BackupError, OSError) as exc:
        steps.append(SetupStep("rollback_failure", "FAIL", str(sanitize(str(exc)))))
        return LifecycleResult("ROLLBACK", "FAIL", tuple(steps), tx, str(activation_path))


def apply_repair(
    *,
    context: SetupContext,
    approved_codes: set[str],
    selected_features: list[str] | tuple[str, ...] | set[str] | None = None,
    trusted_root: Path | None = None,
    platform_name: str | None = None,
) -> LifecycleResult:
    assessment = detect_repairs(
        context=context,
        selected_features=selected_features,
        platform_name=platform_name,
    )
    if assessment.status == "FAIL":
        return LifecycleResult(
            "REPAIR",
            "FAIL",
            tuple(SetupStep(item.code.lower(), item.status, item.detail) for item in assessment.findings),
        )
    actionable = [item for item in assessment.findings if item.code != "HEALTHY"]
    if not actionable:
        return LifecycleResult("REPAIR", "PASS", ())
    unapproved = [item for item in actionable if item.code not in approved_codes]
    if unapproved:
        return LifecycleResult(
            "REPAIR",
            "WAITING_FOR_USER",
            tuple(SetupStep(item.code.lower(), "WAITING_FOR_USER", item.detail) for item in unapproved),
        )

    direct_repairs = {"WRONG_MCP_RUNTIME_PATH", "MANAGED_AGENTS_MISSING", "MISSING_TRUSTED_ROOT"}
    deferred = [item for item in actionable if item.code not in direct_repairs]
    if deferred:
        return LifecycleResult(
            "REPAIR",
            "WAITING_FOR_USER",
            tuple(
                SetupStep(
                    item.code.lower(),
                    "WAITING_FOR_USER",
                    "Repair requires staging a fresh validated runtime; use INSTALL/UPDATE with an explicit wheel",
                )
                for item in deferred
            ),
        )
    if any(item.code == "MISSING_TRUSTED_ROOT" for item in actionable):
        if trusted_root is None:
            return LifecycleResult(
                "REPAIR",
                "WAITING_FOR_USER",
                (SetupStep("repair_trusted_root", "WAITING_FOR_USER", "Explicit project root is required"),),
            )
        try:
            normalize_trusted_root(trusted_root)
        except (TrustedRootError, FileNotFoundError, NotADirectoryError) as exc:
            return LifecycleResult(
                "REPAIR",
                "FAIL",
                (SetupStep("repair_trusted_root", "FAIL", str(sanitize(str(exc)))),),
            )

    tx = new_transaction_id("REPAIR")
    steps: list[SetupStep] = []
    applied_backups: list[BackupRecord] = []
    try:
        with SetupLock(context.setup_dir / "setup.lock"):
            state = load_setup_state(context.state_path)
            try:
                for item in actionable:
                    if item.code == "WRONG_MCP_RUNTIME_PATH":
                        result = update_codex_mcp(
                            path=context.codex_config_path,
                            runtime_python=runtime_python(context.active_runtime_path, platform_name=platform_name),
                            backup_root=context.codex_backup_root,
                            transaction_id=tx,
                        )
                        if result.backup is not None:
                            applied_backups.append(result.backup)
                            _track_backup(state, result.backup)
                        state.mcp_registration = {
                            "status": "PASS",
                            "runtime_python": result.runtime_python,
                            "config_path": result.path,
                        }
                        steps.append(SetupStep("repair_mcp_registration", "PASS"))
                    elif item.code == "MANAGED_AGENTS_MISSING":
                        result = update_managed_agents(
                            path=context.agents_path,
                            backup_root=context.agents_backup_root,
                            transaction_id=tx,
                        )
                        if result.backup is not None:
                            applied_backups.append(result.backup)
                            _track_backup(state, result.backup)
                        state.agents = ManagedAgentsState(
                            target_path=result.path,
                            block_version=result.block_version,
                            installed=True,
                            backup_id=result.backup.backup_id if result.backup is not None else None,
                        )
                        steps.append(SetupStep("repair_managed_agents", "PASS"))
                    elif item.code == "MISSING_TRUSTED_ROOT":
                        result = add_trusted_root(
                            trusted_root,
                            config_file=context.local_config_path,
                            backup_root=context.trusted_roots_backup_root,
                            transaction_id=tx,
                        )
                        if result.backup is not None:
                            applied_backups.append(result.backup)
                            _track_backup(state, result.backup)
                        steps.append(SetupStep("repair_trusted_root", "PASS", data={"root": result.root}))

                state.last_successful_operation = "REPAIR"
                state.transaction.transaction_id = tx
                state.transaction.operation = "REPAIR"
                state.transaction.phase = "LIFECYCLE_COMMITTED"
                save_setup_state(state, context.state_path)
                steps.append(SetupStep("setup_state_persistence", "PASS", data={"state": str(context.state_path)}))
                return LifecycleResult("REPAIR", "PASS", tuple(steps), tx)
            except (
                AgentsConfigError,
                BackupError,
                CodexConfigError,
                OSError,
                SetupStateError,
                TransactionWriteError,
                TrustedRootError,
            ) as exc:
                recovery_errors: list[str] = []
                for backup in reversed(applied_backups):
                    try:
                        restore_backup(backup)
                    except (BackupError, OSError) as restore_exc:
                        recovery_errors.append(str(sanitize(str(restore_exc))))
                detail = str(sanitize(str(exc)))
                if recovery_errors:
                    detail = f"{detail}; recovery: {'; '.join(recovery_errors)}"
                steps.append(SetupStep("repair_failure", "FAIL", detail))
                return LifecycleResult("REPAIR", "FAIL", tuple(steps), tx)
    except SetupLockError as exc:
        steps.append(SetupStep("repair_failure", "FAIL", str(sanitize(str(exc)))))
        return LifecycleResult("REPAIR", "FAIL", tuple(steps), tx)
