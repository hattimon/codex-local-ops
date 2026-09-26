from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .backup_ops import BackupError, BackupRecord, create_backup, restore_backup
from .codex_config import (
    CodexConfigError,
    CodexConfigInspection,
    inspect_codex_mcp,
    restore_codex_config,
    update_codex_mcp,
)
from .safety import sanitize
from .setup_state import (
    RollbackMetadata,
    RuntimeRecord,
    load_setup_state,
    save_setup_state,
    utc_timestamp,
)

FEATURES = ("desktop", "browser", "windows", "obs")
FEATURE_IMPORTS: dict[str, tuple[str, ...]] = {
    "desktop": ("mss", "PIL"),
    "browser": ("playwright",),
    "windows": ("pywinauto",),
    "obs": ("obsws_python",),
}
MIN_PYTHON = (3, 11)
CANDIDATE_MARKER = ".codex-local-ops-candidate.json"
ACTIVATION_RECORD = "activation.json"


class RuntimeOperationError(RuntimeError):
    def __init__(self, code: str, phase: str, detail: str) -> None:
        self.code = code
        self.phase = phase
        self.detail = str(sanitize(detail))
        super().__init__(f"{code} [{phase}]: {self.detail}")


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    name: str
    status: str
    detail: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return sanitize(asdict(self))


@dataclass(frozen=True, slots=True)
class ValidationReport:
    status: str
    runtime_path: str
    checks: tuple[ValidationCheck, ...]
    package_version: str | None = None
    python_version: str | None = None
    validated_at: str = field(default_factory=utc_timestamp)

    @property
    def activatable(self) -> bool:
        return self.status == "PASS" and not any(check.status == "FAIL" for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return sanitize(
            {
                "status": self.status,
                "runtime_path": self.runtime_path,
                "checks": [check.to_dict() for check in self.checks],
                "package_version": self.package_version,
                "python_version": self.python_version,
                "validated_at": self.validated_at,
            }
        )


@dataclass(frozen=True, slots=True)
class CandidateRuntime:
    transaction_id: str
    candidate_root: str
    runtime_path: str
    wheel_path: str
    wheel_name: str
    wheel_sha256: str
    requested_features: tuple[str, ...]
    expected_version: str | None
    staged_at: str

    def to_dict(self) -> dict[str, Any]:
        return sanitize(asdict(self))


@dataclass(frozen=True, slots=True)
class ActivationResult:
    transaction_id: str
    active_runtime: RuntimeRecord
    previous_runtime: RuntimeRecord | None
    activation_record_path: str
    config_backup: BackupRecord | None
    state_backup: BackupRecord | None


@dataclass(frozen=True, slots=True)
class RepairIssue:
    code: str
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class RepairReport:
    status: str
    issues: tuple[RepairIssue, ...]


def normalize_features(features: list[str] | tuple[str, ...] | set[str] | None) -> tuple[str, ...]:
    requested = {str(item).strip().lower() for item in (features or ()) if str(item).strip()}
    unknown = sorted(requested.difference(FEATURES))
    if unknown:
        raise RuntimeOperationError("UNKNOWN_FEATURE", "PREPARE", ", ".join(unknown))
    return tuple(name for name in FEATURES if name in requested)


def runtime_python(runtime_path: Path, *, platform_name: str | None = None) -> Path:
    name = (platform_name or os.name).lower()
    if name in {"nt", "windows", "win32"}:
        return Path(runtime_path) / "Scripts" / "python.exe"
    return Path(runtime_path) / "bin" / "python"


def runtime_clops(runtime_path: Path, *, platform_name: str | None = None) -> Path:
    name = (platform_name or os.name).lower()
    if name in {"nt", "windows", "win32"}:
        return Path(runtime_path) / "Scripts" / "clops.exe"
    return Path(runtime_path) / "bin" / "clops"


def candidate_root(setup_dir: Path, transaction_id: str) -> Path:
    return Path(setup_dir) / "staging" / transaction_id


def previous_runtime_slot(setup_dir: Path, transaction_id: str) -> Path:
    return Path(setup_dir) / "runtime-history" / transaction_id / "runtime.venv"


def activation_record_path(setup_dir: Path, transaction_id: str) -> Path:
    return Path(setup_dir) / "transactions" / transaction_id / ACTIVATION_RECORD


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(sanitize(data), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _command_failure(code: str, phase: str, result: subprocess.CompletedProcess[str]) -> RuntimeOperationError:
    output = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
    return RuntimeOperationError(code, phase, output[-2000:])


def _validation_env(runtime_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    isolated_home = Path(runtime_path).parent / "validation-home"
    env["CODEX_LOCAL_OPS_HOME"] = str(isolated_home)
    return env


def stage_candidate_runtime(
    *,
    base_python: Path,
    wheel_path: Path,
    setup_dir: Path,
    transaction_id: str,
    features: list[str] | tuple[str, ...] | set[str] | None = None,
    expected_version: str | None = None,
    install_browser_assets: bool = True,
) -> CandidateRuntime:
    base_python = Path(base_python)
    wheel_path = Path(wheel_path)
    selected = normalize_features(features)
    if not base_python.is_file():
        raise RuntimeOperationError("BASE_PYTHON_MISSING", "STAGE", str(base_python))
    if not wheel_path.is_file() or wheel_path.suffix.lower() != ".whl":
        raise RuntimeOperationError("WHEEL_INVALID", "STAGE", str(wheel_path))

    root = candidate_root(setup_dir, transaction_id)
    runtime = root / "runtime.venv"
    marker = root / CANDIDATE_MARKER
    if root.exists():
        raise RuntimeOperationError("CANDIDATE_EXISTS", "STAGE", str(root))
    root.mkdir(parents=True)
    marker_data = {
        "owned_by": "codex-local-ops-setup-assistant",
        "transaction_id": transaction_id,
        "created_at": utc_timestamp(),
    }
    _atomic_json(marker, marker_data)

    created = _run([str(base_python), "-m", "venv", str(runtime)], cwd=root, timeout=180)
    if created.returncode != 0:
        raise _command_failure("VENV_CREATE_FAILED", "STAGE", created)
    candidate_python = runtime_python(runtime)
    if not candidate_python.is_file():
        raise RuntimeOperationError("CANDIDATE_PYTHON_MISSING", "STAGE", str(candidate_python))

    requirement = wheel_path.resolve().as_uri()
    if selected:
        requirement = f"{requirement}[{','.join(selected)}]"
    installed = _run(
        [
            str(candidate_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            requirement,
        ],
        cwd=root,
        env=_validation_env(runtime),
        timeout=600,
    )
    if installed.returncode != 0:
        raise _command_failure("PACKAGE_INSTALL_FAILED", "STAGE", installed)

    if "browser" in selected and install_browser_assets:
        browser_install = _run(
            [str(candidate_python), "-m", "playwright", "install", "chromium"],
            cwd=root,
            env=_validation_env(runtime),
            timeout=900,
        )
        if browser_install.returncode != 0:
            raise _command_failure("PLAYWRIGHT_CHROMIUM_INSTALL_FAILED", "STAGE", browser_install)

    candidate = CandidateRuntime(
        transaction_id=transaction_id,
        candidate_root=str(root),
        runtime_path=str(runtime),
        wheel_path=str(wheel_path.resolve()),
        wheel_name=wheel_path.name,
        wheel_sha256=_sha256(wheel_path),
        requested_features=selected,
        expected_version=expected_version,
        staged_at=utc_timestamp(),
    )
    _atomic_json(root / "candidate.json", candidate.to_dict())
    return candidate


def cleanup_candidate(candidate: CandidateRuntime) -> None:
    root = Path(candidate.candidate_root)
    marker = root / CANDIDATE_MARKER
    if not root.exists():
        return
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeOperationError("CANDIDATE_NOT_OWNED", "CLEANUP", str(root)) from exc
    if data.get("owned_by") != "codex-local-ops-setup-assistant" or data.get("transaction_id") != candidate.transaction_id:
        raise RuntimeOperationError("CANDIDATE_NOT_OWNED", "CLEANUP", str(root))
    if Path(candidate.runtime_path).parent.resolve() != root.resolve():
        raise RuntimeOperationError("CANDIDATE_PATH_MISMATCH", "CLEANUP", str(candidate.runtime_path))
    shutil.rmtree(root)


def _check_command(
    name: str,
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 120,
) -> tuple[ValidationCheck, subprocess.CompletedProcess[str]]:
    try:
        result = _run(args, cwd=cwd, env=env, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return ValidationCheck(name, "FAIL", str(sanitize(str(exc)))), subprocess.CompletedProcess(args, 1, "", str(exc))
    if result.returncode == 0:
        return ValidationCheck(name, "PASS"), result
    detail = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()[-1000:]
    return ValidationCheck(name, "FAIL", str(sanitize(detail))), result


def validate_runtime(
    runtime_path: Path,
    *,
    features: list[str] | tuple[str, ...] | set[str] | None = None,
    expected_version: str | None = None,
    require_clops: bool = True,
    require_diagnostics: bool = True,
    platform_name: str | None = None,
) -> ValidationReport:
    runtime_path = Path(runtime_path)
    selected = normalize_features(features)
    checks: list[ValidationCheck] = []
    python = runtime_python(runtime_path, platform_name=platform_name)
    if not python.is_file():
        checks.append(ValidationCheck("candidate_python", "FAIL", f"Missing: {python}"))
        return ValidationReport("FAIL", str(runtime_path), tuple(checks))
    checks.append(ValidationCheck("candidate_python", "PASS", data={"path": str(python)}))

    env = _validation_env(runtime_path)
    cwd = runtime_path.parent
    version_check, result = _check_command(
        "python_version",
        [str(python), "-c", "import json,sys; print(json.dumps(list(sys.version_info[:3])))"],
        cwd=cwd,
        env=env,
    )
    python_version: str | None = None
    if version_check.status == "PASS":
        try:
            major, minor, patch = json.loads(result.stdout.strip().splitlines()[-1])
            python_version = f"{major}.{minor}.{patch}"
            if major != 3 or (major, minor) < MIN_PYTHON:
                version_check = ValidationCheck(
                    "python_version",
                    "FAIL",
                    f"Unsupported Python {python_version}; requires CPython 3.11+",
                )
        except (ValueError, TypeError, json.JSONDecodeError, IndexError) as exc:
            version_check = ValidationCheck("python_version", "FAIL", f"Cannot parse Python version: {exc}")
    checks.append(version_check)

    import_script = (
        "import json,pathlib; import codex_local_ops; "
        "from importlib.metadata import version; "
        "print(json.dumps({'version':version('codex-local-ops'),'file':str(pathlib.Path(codex_local_ops.__file__).resolve())}))"
    )
    package_check, result = _check_command(
        "package_import",
        [str(python), "-c", import_script],
        cwd=cwd,
        env=env,
    )
    package_version: str | None = None
    if package_check.status == "PASS":
        try:
            package_data = json.loads(result.stdout.strip().splitlines()[-1])
            package_version = str(package_data["version"])
            package_file = Path(str(package_data["file"]))
            try:
                package_file.resolve().relative_to(runtime_path.resolve())
            except ValueError:
                package_check = ValidationCheck(
                    "package_import",
                    "FAIL",
                    "codex_local_ops imported from outside the candidate runtime",
                    {"file": str(package_file)},
                )
            if expected_version is not None and package_version != expected_version:
                package_check = ValidationCheck(
                    "package_import",
                    "FAIL",
                    f"Expected package {expected_version}, got {package_version}",
                    {"file": str(package_file)},
                )
        except (ValueError, KeyError, TypeError, json.JSONDecodeError, IndexError) as exc:
            package_check = ValidationCheck("package_import", "FAIL", f"Cannot parse package identity: {exc}")
    checks.append(package_check)

    if require_clops:
        clops = runtime_clops(runtime_path, platform_name=platform_name)
        if not clops.is_file():
            checks.append(ValidationCheck("clops_help", "FAIL", f"Missing: {clops}"))
        else:
            check, _ = _check_command("clops_help", [str(clops), "--help"], cwd=cwd, env=env)
            checks.append(check)

    if require_diagnostics:
        check, _ = _check_command(
            "clops_diagnostics",
            [str(python), "-m", "codex_local_ops.cli", "diagnostics"],
            cwd=cwd,
            env=env,
            timeout=180,
        )
        checks.append(check)

    check, _ = _check_command(
        "server_import",
        [str(python), "-c", "import codex_local_ops.server"],
        cwd=cwd,
        env=env,
    )
    checks.append(check)

    for feature in FEATURES:
        modules = FEATURE_IMPORTS[feature]
        selected_feature = feature in selected
        script = "import " + ", ".join(modules)
        check, _ = _check_command(f"feature_{feature}", [str(python), "-c", script], cwd=cwd, env=env)
        if check.status == "FAIL" and not selected_feature:
            check = replace(check, status="WARNING", detail=f"Unselected optional feature unavailable: {feature}")
        checks.append(check)

    browser_selected = "browser" in selected
    chromium_script = (
        "import json,pathlib; from playwright.sync_api import sync_playwright; "
        "p=sync_playwright().start(); x=pathlib.Path(p.chromium.executable_path); "
        "print(json.dumps({'path':str(x),'exists':x.is_file()})); p.stop(); "
        "raise SystemExit(0 if x.is_file() else 3)"
    )
    chromium_check, _ = _check_command(
        "playwright_chromium",
        [str(python), "-c", chromium_script],
        cwd=cwd,
        env=env,
    )
    if chromium_check.status == "FAIL" and not browser_selected:
        chromium_check = replace(chromium_check, status="WARNING", detail="Unselected browser asset unavailable")
    checks.append(chromium_check)

    overall = "FAIL" if any(check.status == "FAIL" for check in checks) else "PASS"
    return ValidationReport(overall, str(runtime_path), tuple(checks), package_version, python_version)


def validate_candidate(candidate: CandidateRuntime, *, platform_name: str | None = None) -> ValidationReport:
    report = validate_runtime(
        Path(candidate.runtime_path),
        features=candidate.requested_features,
        expected_version=candidate.expected_version,
        platform_name=platform_name,
    )
    _atomic_json(Path(candidate.candidate_root) / "validation.json", report.to_dict())
    return report


def _record_from_candidate(candidate: CandidateRuntime, report: ValidationReport, runtime_path_value: Path) -> RuntimeRecord:
    return RuntimeRecord(
        package_version=report.package_version or candidate.expected_version,
        runtime_path=str(runtime_path_value),
        python_version=report.python_version,
        features=list(candidate.requested_features),
        artifact_name=candidate.wheel_name,
        artifact_sha256=candidate.wheel_sha256,
        validation_status=report.status,
        known_good_at=utc_timestamp(),
    )


def _backup_to_dict(record: BackupRecord | None) -> dict[str, Any] | None:
    return record.to_dict() if record is not None else None


def _backup_from_dict(data: dict[str, Any] | None) -> BackupRecord | None:
    return BackupRecord(**data) if data is not None else None


def _journal(path: Path, data: dict[str, Any], phase: str) -> None:
    data = dict(data)
    data["phase"] = phase
    data["updated_at"] = utc_timestamp()
    _atomic_json(path, data)


def activate_candidate(
    candidate: CandidateRuntime,
    validation: ValidationReport,
    *,
    setup_dir: Path,
    active_runtime_path: Path,
    codex_config_path: Path,
    codex_backup_root: Path,
    state_path: Path,
    state_backup_root: Path,
) -> ActivationResult:
    if not validation.activatable:
        raise RuntimeOperationError("CANDIDATE_NOT_VALIDATED", "ACTIVATE", validation.status)
    candidate_runtime = Path(candidate.runtime_path)
    if not candidate_runtime.is_dir():
        raise RuntimeOperationError("CANDIDATE_MISSING", "ACTIVATE", str(candidate_runtime))
    if candidate_runtime.parent.resolve() != Path(candidate.candidate_root).resolve():
        raise RuntimeOperationError("CANDIDATE_PATH_MISMATCH", "ACTIVATE", str(candidate_runtime))

    active = Path(active_runtime_path)
    previous_slot = previous_runtime_slot(setup_dir, candidate.transaction_id)
    record_path = activation_record_path(setup_dir, candidate.transaction_id)
    if active.resolve().drive.lower() != candidate_runtime.resolve().drive.lower():
        raise RuntimeOperationError("CROSS_VOLUME_ACTIVATION", "ACTIVATE", f"{candidate_runtime} -> {active}")
    if previous_slot.exists():
        raise RuntimeOperationError("PREVIOUS_SLOT_EXISTS", "ACTIVATE", str(previous_slot))

    state = load_setup_state(state_path)
    state_backup = create_backup(
        state_path,
        state_backup_root,
        transaction_id=candidate.transaction_id,
        kind="setup-state",
    )
    old_active_record = state.active_runtime
    journal: dict[str, Any] = {
        "transaction_id": candidate.transaction_id,
        "candidate_runtime": str(candidate_runtime),
        "active_runtime": str(active),
        "previous_runtime": str(previous_slot),
        "config_path": str(codex_config_path),
        "state_path": str(state_path),
        "state_backup": _backup_to_dict(state_backup),
        "validation": validation.to_dict(),
        "created_at": utc_timestamp(),
        "config_backup": None,
    }
    _journal(record_path, journal, "PREPARED")

    config_backup: BackupRecord | None = None
    moved_previous = False
    moved_candidate = False
    try:
        if active.exists():
            previous_slot.parent.mkdir(parents=True, exist_ok=True)
            os.replace(active, previous_slot)
            moved_previous = True
        _journal(record_path, journal, "PREVIOUS_PRESERVED")

        active.parent.mkdir(parents=True, exist_ok=True)
        os.replace(candidate_runtime, active)
        moved_candidate = True
        _journal(record_path, journal, "RUNTIME_ACTIVATED")

        config_result = update_codex_mcp(
            path=codex_config_path,
            runtime_python=runtime_python(active),
            backup_root=codex_backup_root,
            transaction_id=candidate.transaction_id,
        )
        config_backup = config_result.backup
        journal["config_backup"] = _backup_to_dict(config_backup)
        _journal(record_path, journal, "CONFIG_REGISTERED")

        previous_record = None
        if moved_previous:
            if old_active_record is not None:
                previous_record = replace(old_active_record, runtime_path=str(previous_slot))
            else:
                previous_record = RuntimeRecord(runtime_path=str(previous_slot), validation_status="UNKNOWN")
        active_record = _record_from_candidate(candidate, validation, active)
        state.status = "ROLLBACK_AVAILABLE" if previous_record is not None else "ACTIVE"
        state.previous_runtime = previous_record
        state.active_runtime = active_record
        state.requested_features = list(candidate.requested_features)
        state.validation_summary = validation.to_dict()
        state.mcp_registration = {
            "status": "PASS",
            "runtime_python": str(runtime_python(active)),
            "config_path": str(codex_config_path),
        }
        state.rollback = RollbackMetadata(
            eligible=previous_record is not None,
            reason="previous known-good runtime retained" if previous_record is not None else None,
            config_backup_id=config_backup.backup_id if config_backup is not None else None,
            previous_runtime_path=str(previous_slot) if previous_record is not None else None,
            activation_transaction_id=candidate.transaction_id,
            activation_record_path=str(record_path),
        )
        state.last_successful_operation = "UPDATE" if previous_record is not None else "INSTALL"
        state.transaction.transaction_id = candidate.transaction_id
        state.transaction.operation = state.last_successful_operation
        state.transaction.phase = "COMMIT_KNOWN_GOOD"
        save_setup_state(state, state_path)
        _journal(record_path, journal, "COMMITTED")
        return ActivationResult(
            candidate.transaction_id,
            active_record,
            previous_record,
            str(record_path),
            config_backup,
            state_backup,
        )
    except Exception as exc:
        rollback_errors: list[str] = []
        try:
            if config_backup is not None:
                restore_codex_config(config_backup)
        except (CodexConfigError, BackupError, OSError) as restore_exc:
            rollback_errors.append(f"config restore: {restore_exc}")
        try:
            if moved_candidate and active.exists():
                candidate_runtime.parent.mkdir(parents=True, exist_ok=True)
                os.replace(active, candidate_runtime)
            if moved_previous and previous_slot.exists():
                os.replace(previous_slot, active)
        except OSError as restore_exc:
            rollback_errors.append(f"runtime restore: {restore_exc}")
        try:
            restore_backup(state_backup)
        except (BackupError, OSError) as restore_exc:
            rollback_errors.append(f"state restore: {restore_exc}")
        journal["failure"] = str(sanitize(str(exc)))
        journal["rollback_errors"] = [str(sanitize(item)) for item in rollback_errors]
        _journal(record_path, journal, "RECOVERY_REQUIRED" if rollback_errors else "ROLLED_BACK")
        code = "ACTIVATION_RECOVERY_REQUIRED" if rollback_errors else "ACTIVATION_FAILED"
        raise RuntimeOperationError(code, "ACTIVATE", str(exc)) from exc


def rollback_activation(
    activation_path: Path,
    *,
    state_path: Path,
) -> RuntimeRecord:
    path = Path(activation_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeOperationError("ROLLBACK_METADATA_INVALID", "ROLLBACK", str(path)) from exc
    if data.get("phase") != "COMMITTED":
        raise RuntimeOperationError("ROLLBACK_NOT_COMMITTED", "ROLLBACK", str(data.get("phase")))
    active = Path(data["active_runtime"])
    previous = Path(data["previous_runtime"])
    if not previous.is_dir():
        raise RuntimeOperationError("PREVIOUS_RUNTIME_MISSING", "ROLLBACK", str(previous))
    failed_slot = previous.parent / f"replaced-{uuid.uuid4().hex}.venv"

    config_backup = _backup_from_dict(data.get("config_backup"))
    state_backup = _backup_from_dict(data.get("state_backup"))
    moved_current = False
    moved_previous = False
    try:
        if active.exists():
            os.replace(active, failed_slot)
            moved_current = True
        os.replace(previous, active)
        moved_previous = True
        if config_backup is not None:
            restore_codex_config(config_backup)
        if state_backup is not None:
            restore_backup(state_backup)
        restored_state = load_setup_state(state_path)
        restored_record = restored_state.active_runtime or RuntimeRecord(runtime_path=str(active))
        if restored_record.runtime_path != str(active):
            restored_record = replace(restored_record, runtime_path=str(active))
            restored_state.active_runtime = restored_record
            save_setup_state(restored_state, state_path)
        data["rollback_failed_runtime"] = str(failed_slot) if moved_current else None
        _journal(path, data, "ROLLBACK_COMMITTED")
        return restored_record
    except Exception as exc:
        recovery_errors: list[str] = []
        try:
            if moved_previous and active.exists():
                os.replace(active, previous)
            if moved_current and failed_slot.exists():
                os.replace(failed_slot, active)
        except OSError as recovery_exc:
            recovery_errors.append(str(recovery_exc))
        data["rollback_failure"] = str(sanitize(str(exc)))
        data["rollback_recovery_errors"] = [str(sanitize(item)) for item in recovery_errors]
        _journal(path, data, "RECOVERY_REQUIRED")
        raise RuntimeOperationError("ROLLBACK_FAILED", "ROLLBACK", str(exc)) from exc


def detect_interrupted_updates(setup_dir: Path) -> tuple[Path, ...]:
    root = Path(setup_dir) / "transactions"
    if not root.exists():
        return ()
    interrupted: list[Path] = []
    terminal = {"COMMITTED", "ROLLED_BACK", "ROLLBACK_COMMITTED"}
    for path in sorted(root.glob(f"*/{ACTIVATION_RECORD}")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            interrupted.append(path)
            continue
        if data.get("phase") not in terminal:
            interrupted.append(path)
    return tuple(interrupted)


def detect_repair_needs(
    *,
    active_runtime_path: Path,
    codex_config_path: Path,
    setup_dir: Path,
    selected_features: list[str] | tuple[str, ...] | set[str] | None = None,
    validation: ValidationReport | None = None,
    platform_name: str | None = None,
) -> RepairReport:
    active = Path(active_runtime_path)
    selected = normalize_features(selected_features)
    issues: list[RepairIssue] = []
    expected_python = runtime_python(active, platform_name=platform_name)
    if not active.is_dir():
        issues.append(RepairIssue("ACTIVE_RUNTIME_MISSING", "FAIL", str(active)))
    elif not expected_python.is_file():
        issues.append(RepairIssue("ACTIVE_PYTHON_MISSING", "FAIL", str(expected_python)))

    inspection: CodexConfigInspection = inspect_codex_mcp(
        path=codex_config_path,
        expected_runtime_python=expected_python,
    )
    if not inspection.valid:
        issues.append(RepairIssue("CODEX_CONFIG_INVALID", "FAIL", inspection.error or inspection.path))
    elif not inspection.matches_expected:
        issues.append(
            RepairIssue(
                "WRONG_MCP_RUNTIME_PATH",
                "FAIL",
                inspection.runtime_python or "codexLocalOps MCP registration missing",
            )
        )

    for path in detect_interrupted_updates(setup_dir):
        issues.append(RepairIssue("INTERRUPTED_UPDATE", "FAIL", str(path)))

    if validation is not None:
        failed_names = {check.name for check in validation.checks if check.status == "FAIL"}
        if "package_import" in failed_names:
            issues.append(RepairIssue("PACKAGE_MISSING_OR_BROKEN", "FAIL", "codex_local_ops import failed"))
        if validation.status == "FAIL":
            issues.append(RepairIssue("RUNTIME_VALIDATION_FAILED", "FAIL", validation.runtime_path))
        for feature in selected:
            if f"feature_{feature}" in failed_names:
                issues.append(RepairIssue("SELECTED_FEATURE_MISSING", "FAIL", feature))
        if "browser" in selected and "playwright_chromium" in failed_names:
            browser_package_failed = "feature_browser" in failed_names
            if not browser_package_failed:
                issues.append(RepairIssue("PLAYWRIGHT_CHROMIUM_MISSING", "FAIL", "browser"))

    status = "FAIL" if any(issue.status == "FAIL" for issue in issues) else "PASS"
    return RepairReport(status, tuple(issues))
