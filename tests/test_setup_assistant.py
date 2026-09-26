from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

import pytest

from codex_local_ops import setup_assistant
from codex_local_ops.agents_config import AgentsConfigError, AgentsConfigInspection, update_managed_agents
from codex_local_ops.codex_config import update_codex_mcp
from codex_local_ops.runtime_ops import (
    CandidateRuntime,
    RepairIssue,
    RepairReport,
    ValidationCheck,
    ValidationReport,
    activate_candidate,
)
from codex_local_ops.setup_assistant import (
    LifecycleResult,
    RepairAssessment,
    RepairFinding,
    SetupStep,
    apply_repair,
    default_context,
    detect_repairs,
    execute_install,
    execute_rollback,
    execute_update,
    plan_install,
    plan_rollback,
    plan_update,
    plan_web_repair,
    run_setup_diagnostics,
    run_web_setup_status,
)
from codex_local_ops.setup_state import (
    RuntimeRecord,
    SetupLockError,
    SetupState,
    load_setup_state,
    save_setup_state,
)
from codex_local_ops.trusted_roots import TrustedRootsInspection, add_trusted_root


def _context(tmp_path: Path):
    return default_context(tmp_path / "home")


def _windows_python(runtime: Path) -> Path:
    python = runtime / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"python")
    return python


def _candidate(context, tmp_path: Path, *, transaction_id: str = "candidate-1") -> CandidateRuntime:
    root = context.setup_dir / "staging" / transaction_id
    runtime = root / "runtime.venv"
    _windows_python(runtime)
    root.mkdir(parents=True, exist_ok=True)
    (root / ".codex-local-ops-candidate.json").write_text(
        json.dumps({"owned_by": "codex-local-ops-setup-assistant", "transaction_id": transaction_id}),
        encoding="utf-8",
    )
    wheel = tmp_path / "codex_local_ops-0.1.0b1-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    return CandidateRuntime(
        transaction_id=transaction_id,
        candidate_root=str(root),
        runtime_path=str(runtime),
        wheel_path=str(wheel),
        wheel_name=wheel.name,
        wheel_sha256="abc123",
        requested_features=(),
        expected_version="0.1.0b1",
        staged_at="2026-09-24T10:00:00Z",
    )


def _pass_report(candidate: CandidateRuntime) -> ValidationReport:
    return ValidationReport(
        "PASS",
        candidate.runtime_path,
        (ValidationCheck("package_import", "PASS"),),
        package_version="0.1.0b1",
        python_version="3.12.10",
    )


def _repair_assessment(*findings: RepairFinding) -> RepairAssessment:
    statuses = [item.status for item in findings]
    if "FAIL" in statuses:
        status = "FAIL"
    elif "WAITING_FOR_USER" in statuses:
        status = "WAITING_FOR_USER"
    elif "WARNING" in statuses:
        status = "WARNING"
    else:
        status = "PASS"
    return RepairAssessment(status, tuple(findings))


def test_default_context_custom_home_is_fully_isolated(tmp_path: Path):
    home = tmp_path / "isolated-home"
    context = default_context(home)

    assert context.active_runtime_path == home / ".codex-local-ops-runtime.venv"
    assert context.codex_config_path == home / ".codex" / "config.toml"
    assert context.agents_path == home / ".codex" / "AGENTS.md"
    assert context.setup_dir == home / ".codex-local-ops" / "setup"
    assert context.local_config_path == home / ".codex-local-ops" / "config" / "config.yaml"
    for context_field in fields(context):
        value = getattr(context, context_field.name)
        assert Path(value).is_relative_to(home)


def test_install_and_update_planning_require_explicit_inputs(tmp_path: Path):
    install = plan_install(wheel_path=None, trusted_root=None)
    assert install.status == "WAITING_FOR_USER"
    assert install.required_inputs == ("wheel_path", "trusted_root")

    context = _context(tmp_path)
    wheel = tmp_path / "package.whl"
    wheel.write_bytes(b"wheel")
    update = plan_update(context=context, wheel_path=wheel)
    assert update.status == "NOT_CONFIGURED"
    assert update.steps[0].name == "current_runtime"
    assert update.steps[0].status == "NOT_CONFIGURED"


def test_rollback_plan_requires_a_real_activation_record(tmp_path: Path):
    context = _context(tmp_path)
    state = SetupState(status="ROLLBACK_AVAILABLE")
    state.rollback.eligible = True
    state.rollback.activation_record_path = str(context.setup_dir / "transactions" / "missing" / "activation.json")
    save_setup_state(state, context.state_path)

    missing = plan_rollback(context=context)
    assert missing.status == "FAIL"

    record = Path(state.rollback.activation_record_path)
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text("{}", encoding="utf-8")
    ready = plan_rollback(context=context)
    assert ready.status == "PASS"


def test_diagnostics_uses_stored_runtime_feature_validation(tmp_path: Path):
    context = _context(tmp_path)
    active_python = _windows_python(context.active_runtime_path)
    trusted = tmp_path / "project"
    trusted.mkdir()
    state = SetupState(
        status="ACTIVE",
        active_runtime=RuntimeRecord(runtime_path=str(context.active_runtime_path)),
        validation_summary={
            "status": "PASS",
            "runtime_path": str(context.active_runtime_path),
            "checks": [
                {"name": "feature_browser", "status": "PASS"},
                {"name": "playwright_chromium", "status": "PASS"},
                {"name": "feature_desktop", "status": "FAIL", "detail": "not selected"},
                {"name": "feature_windows", "status": "PASS"},
            ],
        },
    )
    save_setup_state(state, context.state_path)
    update_codex_mcp(
        path=context.codex_config_path,
        runtime_python=active_python,
        backup_root=context.codex_backup_root,
        transaction_id="diagnostics-mcp",
    )
    update_managed_agents(
        path=context.agents_path,
        backup_root=context.agents_backup_root,
        transaction_id="diagnostics-agents",
    )
    add_trusted_root(
        trusted,
        config_file=context.local_config_path,
        backup_root=context.trusted_roots_backup_root,
        transaction_id="diagnostics-root",
    )

    report = run_setup_diagnostics(
        context=context,
        selected_root=trusted,
        selected_features=["browser", "windows"],
    )
    checks = report["sections"]["CAPABILITIES"]["checks"]

    assert checks["browser_package"]["status"] == "PASS"
    assert checks["playwright_chromium"]["status"] == "PASS"
    assert checks["desktop_capture"]["status"] == "WARNING"
    assert checks["desktop_ui_automation"]["status"] == "PASS"
    assert checks["browser_package"]["source"] == "stored runtime validation"


def test_detect_repairs_classifies_unsafe_and_automatic_findings(tmp_path: Path, monkeypatch):
    context = _context(tmp_path)
    save_setup_state(SetupState(), context.state_path)
    monkeypatch.setattr(
        setup_assistant,
        "detect_repair_needs",
        lambda **kwargs: RepairReport(
            "FAIL",
            (
                RepairIssue("INTERRUPTED_UPDATE", "FAIL", "transaction-x"),
                RepairIssue("ACTIVE_RUNTIME_MISSING", "FAIL", "runtime"),
            ),
        ),
    )
    monkeypatch.setattr(
        setup_assistant,
        "inspect_managed_agents",
        lambda **kwargs: AgentsConfigInspection("agents", True, True, True, True, 1),
    )
    monkeypatch.setattr(
        setup_assistant,
        "inspect_trusted_roots",
        lambda **kwargs: TrustedRootsInspection("config", True, True, ("D:/CODEX",)),
    )

    assessment = detect_repairs(context=context)
    by_code = {item.code: item for item in assessment.findings}

    assert by_code["INTERRUPTED_UPDATE"].classification == "unsafe/ambiguous"
    assert by_code["INTERRUPTED_UPDATE"].status == "FAIL"
    assert by_code["ACTIVE_RUNTIME_MISSING"].classification == "automatic repair possible"
    assert by_code["ACTIVE_RUNTIME_MISSING"].status == "WARNING"


def test_repair_preflight_defers_runtime_work_before_any_mutation(tmp_path: Path, monkeypatch):
    context = _context(tmp_path)
    findings = (
        RepairFinding("WRONG_MCP_RUNTIME_PATH", "WARNING", "automatic repair possible", "wrong path"),
        RepairFinding("ACTIVE_RUNTIME_MISSING", "WARNING", "automatic repair possible", "missing runtime"),
    )
    monkeypatch.setattr(setup_assistant, "detect_repairs", lambda **kwargs: _repair_assessment(*findings))
    monkeypatch.setattr(
        setup_assistant,
        "update_codex_mcp",
        lambda **kwargs: pytest.fail("repair mutated config before completing preflight"),
    )

    result = apply_repair(
        context=context,
        approved_codes={item.code for item in findings},
    )

    assert result.status == "WAITING_FOR_USER"
    assert result.steps[0].name == "active_runtime_missing"


def test_repair_rolls_back_prior_mutation_when_later_step_fails(tmp_path: Path, monkeypatch):
    context = _context(tmp_path)
    _windows_python(context.active_runtime_path)
    save_setup_state(
        SetupState(status="ACTIVE", active_runtime=RuntimeRecord(runtime_path=str(context.active_runtime_path))),
        context.state_path,
    )
    context.codex_config_path.parent.mkdir(parents=True, exist_ok=True)
    original = b'model = "keep"\n'
    context.codex_config_path.write_bytes(original)
    findings = (
        RepairFinding("WRONG_MCP_RUNTIME_PATH", "WARNING", "automatic repair possible", "wrong path"),
        RepairFinding("MANAGED_AGENTS_MISSING", "WARNING", "automatic repair possible", "missing agents"),
    )
    monkeypatch.setattr(setup_assistant, "detect_repairs", lambda **kwargs: _repair_assessment(*findings))
    monkeypatch.setattr(
        setup_assistant,
        "update_managed_agents",
        lambda **kwargs: (_ for _ in ()).throw(AgentsConfigError("forced agents failure")),
    )

    result = apply_repair(
        context=context,
        approved_codes={item.code for item in findings},
        platform_name="windows",
    )

    assert result.status == "FAIL"
    assert context.codex_config_path.read_bytes() == original
    assert load_setup_state(context.state_path).config_backups == []


def test_successful_repair_tracks_backups_and_state(tmp_path: Path, monkeypatch):
    context = _context(tmp_path)
    _windows_python(context.active_runtime_path)
    save_setup_state(
        SetupState(status="ACTIVE", active_runtime=RuntimeRecord(runtime_path=str(context.active_runtime_path))),
        context.state_path,
    )
    context.codex_config_path.parent.mkdir(parents=True, exist_ok=True)
    context.codex_config_path.write_text('model = "keep"\n', encoding="utf-8")
    context.agents_path.parent.mkdir(parents=True, exist_ok=True)
    context.agents_path.write_text("# User instructions\n", encoding="utf-8")
    context.local_config_path.parent.mkdir(parents=True, exist_ok=True)
    context.local_config_path.write_text("version: 1\nprojects:\n  trusted_roots: []\n", encoding="utf-8")
    trusted = tmp_path / "project"
    trusted.mkdir()
    findings = (
        RepairFinding("WRONG_MCP_RUNTIME_PATH", "WARNING", "automatic repair possible", "wrong path"),
        RepairFinding("MANAGED_AGENTS_MISSING", "WARNING", "automatic repair possible", "missing agents"),
        RepairFinding("MISSING_TRUSTED_ROOT", "WAITING_FOR_USER", "user action required", "missing root"),
    )
    monkeypatch.setattr(setup_assistant, "detect_repairs", lambda **kwargs: _repair_assessment(*findings))

    result = apply_repair(
        context=context,
        approved_codes={item.code for item in findings},
        trusted_root=trusted,
        platform_name="windows",
    )
    state = load_setup_state(context.state_path)

    assert result.status == "PASS"
    assert len(state.config_backups) == 3
    assert state.last_successful_operation == "REPAIR"
    assert state.transaction.operation == "REPAIR"
    assert state.transaction.phase == "LIFECYCLE_COMMITTED"
    assert state.agents.installed is True
    assert state.mcp_registration["status"] == "PASS"


def test_candidate_validation_failure_preserves_active_runtime_and_state(tmp_path: Path, monkeypatch):
    context = _context(tmp_path)
    active = context.active_runtime_path
    active.mkdir(parents=True)
    sentinel = active / "known-good.txt"
    sentinel.write_text("safe", encoding="utf-8")
    save_setup_state(
        SetupState(status="ACTIVE", active_runtime=RuntimeRecord(runtime_path=str(active))),
        context.state_path,
    )
    state_before = context.state_path.read_bytes()
    candidate = _candidate(context, tmp_path, transaction_id="validation-fail")
    cleaned: list[str] = []
    monkeypatch.setattr(setup_assistant, "stage_candidate_runtime", lambda **kwargs: candidate)
    monkeypatch.setattr(
        setup_assistant,
        "validate_candidate",
        lambda *args, **kwargs: ValidationReport(
            "FAIL",
            candidate.runtime_path,
            (ValidationCheck("package_import", "FAIL", "broken"),),
        ),
    )
    monkeypatch.setattr(setup_assistant, "cleanup_candidate", lambda item: cleaned.append(item.transaction_id))

    result = execute_update(
        context=context,
        base_python=tmp_path / "base-python.exe",
        wheel_path=Path(candidate.wheel_path),
        platform_name="windows",
    )

    assert result.status == "FAIL"
    assert sentinel.read_text(encoding="utf-8") == "safe"
    assert context.state_path.read_bytes() == state_before
    assert cleaned == ["validation-fail"]


def test_install_failure_after_activation_restores_runtime_and_configuration(tmp_path: Path, monkeypatch):
    context = _context(tmp_path)
    context.codex_config_path.parent.mkdir(parents=True, exist_ok=True)
    context.agents_path.parent.mkdir(parents=True, exist_ok=True)
    context.local_config_path.parent.mkdir(parents=True, exist_ok=True)
    codex_original = b'model = "keep"\n'
    agents_original = b"# User instructions\nKeep this.\n"
    local_original = b"version: 1\ncustom: keep\nprojects:\n  trusted_roots: []\n"
    context.codex_config_path.write_bytes(codex_original)
    context.agents_path.write_bytes(agents_original)
    context.local_config_path.write_bytes(local_original)
    save_setup_state(SetupState(), context.state_path)
    state_original = context.state_path.read_bytes()
    trusted = tmp_path / "project"
    trusted.mkdir()
    candidate = _candidate(context, tmp_path, transaction_id="install-rollback")
    report = _pass_report(candidate)
    monkeypatch.setattr(setup_assistant, "stage_candidate_runtime", lambda **kwargs: candidate)
    monkeypatch.setattr(setup_assistant, "validate_candidate", lambda *args, **kwargs: report)

    def fail_final_state(state, path):
        raise OSError("forced final state failure")

    monkeypatch.setattr(setup_assistant, "save_setup_state", fail_final_state)

    result = execute_install(
        context=context,
        base_python=tmp_path / "base-python.exe",
        wheel_path=Path(candidate.wheel_path),
        trusted_root=trusted,
        platform_name="windows",
    )

    assert result.status == "FAIL"
    assert not context.active_runtime_path.exists()
    assert context.codex_config_path.read_bytes() == codex_original
    assert context.agents_path.read_bytes() == agents_original
    assert context.local_config_path.read_bytes() == local_original
    assert context.state_path.read_bytes() == state_original


def test_explicit_rollback_restores_runtime_agents_and_trusted_roots(tmp_path: Path):
    context = _context(tmp_path)
    active = context.active_runtime_path
    _windows_python(active)
    (active / "old.txt").write_text("old", encoding="utf-8")
    save_setup_state(
        SetupState(
            status="ACTIVE",
            active_runtime=RuntimeRecord(package_version="0.0.9", runtime_path=str(active)),
        ),
        context.state_path,
    )
    context.codex_config_path.parent.mkdir(parents=True, exist_ok=True)
    codex_original = b'model = "legacy"\n'
    context.codex_config_path.write_bytes(codex_original)
    context.agents_path.parent.mkdir(parents=True, exist_ok=True)
    agents_original = b"# User instructions\n"
    context.agents_path.write_bytes(agents_original)
    context.local_config_path.parent.mkdir(parents=True, exist_ok=True)
    local_original = b"version: 1\nprojects:\n  trusted_roots: []\n"
    context.local_config_path.write_bytes(local_original)

    candidate = _candidate(context, tmp_path, transaction_id="update-rollback")
    activation = activate_candidate(
        candidate,
        _pass_report(candidate),
        setup_dir=context.setup_dir,
        active_runtime_path=context.active_runtime_path,
        codex_config_path=context.codex_config_path,
        codex_backup_root=context.codex_backup_root,
        state_path=context.state_path,
        state_backup_root=context.state_backup_root,
    )
    agents_result = update_managed_agents(
        path=context.agents_path,
        backup_root=context.agents_backup_root,
        transaction_id="update-rollback",
    )
    trusted = tmp_path / "project"
    trusted.mkdir()
    trusted_result = add_trusted_root(
        trusted,
        config_file=context.local_config_path,
        backup_root=context.trusted_roots_backup_root,
        transaction_id="update-rollback",
    )
    state = load_setup_state(context.state_path)
    assert agents_result.backup is not None
    assert trusted_result.backup is not None
    state.config_backups.extend(
        [
            setup_assistant._backup_reference(agents_result.backup),
            setup_assistant._backup_reference(trusted_result.backup),
        ]
    )
    state.rollback.agents_backup_id = agents_result.backup.backup_id
    state.rollback.setup_config_backup_id = trusted_result.backup.backup_id
    save_setup_state(state, context.state_path)

    result = execute_rollback(context=context)

    assert result.status == "PASS"
    assert result.activation_record_path == activation.activation_record_path
    assert (context.active_runtime_path / "old.txt").read_text(encoding="utf-8") == "old"
    assert context.codex_config_path.read_bytes() == codex_original
    assert context.agents_path.read_bytes() == agents_original
    assert context.local_config_path.read_bytes() == local_original
    assert load_setup_state(context.state_path).active_runtime.package_version == "0.0.9"


def test_repair_lock_contention_returns_failure_without_mutation(tmp_path: Path, monkeypatch):
    context = _context(tmp_path)
    finding = RepairFinding("WRONG_MCP_RUNTIME_PATH", "WARNING", "automatic repair possible", "wrong path")
    monkeypatch.setattr(setup_assistant, "detect_repairs", lambda **kwargs: _repair_assessment(finding))
    monkeypatch.setattr(
        setup_assistant,
        "update_codex_mcp",
        lambda **kwargs: pytest.fail("repair mutated configuration while setup lock was busy"),
    )

    class BusyLock:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            raise SetupLockError("SETUP_BUSY")

        def __exit__(self, *args):
            return None

    monkeypatch.setattr(setup_assistant, "SetupLock", BusyLock)

    result = apply_repair(context=context, approved_codes={finding.code})

    assert result.status == "FAIL"
    assert "SETUP_BUSY" in (result.steps[-1].detail or "")


def test_lifecycle_result_redacts_secrets():
    result = LifecycleResult(
        "REPAIR",
        "FAIL",
        (SetupStep("failure", "FAIL", "Authorization: Bearer super-secret-token"),),
    )

    rendered = json.dumps(result.to_dict())
    assert "super-secret-token" not in rendered
    assert "REDACTED" in rendered


def test_web_setup_status_keeps_healthy_local_ops_separate_from_incomplete_web(tmp_path: Path, monkeypatch):
    context = _context(tmp_path)
    save_setup_state(SetupState(), context.state_path)
    monkeypatch.setattr(
        setup_assistant,
        "run_setup_diagnostics",
        lambda **_kwargs: {
            "sections": {
                "LOCAL_OPS": {"status": "PASS"},
                "CODEX": {"status": "PASS"},
            }
        },
    )
    installation = setup_assistant.WebInstallation(
        status="PASS",
        installed=True,
        install_path=str(tmp_path / "launcher"),
        executable=str(tmp_path / "launcher" / "Codex Web GPT.exe"),
        version="6.1.1",
        version_source="test",
        launcher_running=True,
        profile_present=True,
        launcher_state_path=str(tmp_path / "launcher-state.json"),
    )

    result = run_web_setup_status(
        context=context,
        user_home=tmp_path / "home",
        installation=installation,
        launcher_state={"browserSmokePassed": False},
    )

    steps = {item["name"]: item for item in result["steps"]}
    assert steps["CODEX_TO_CODEXLOCALOPS"]["status"] == "PASS"
    assert steps["CHATGPT_LOGIN"]["status"] == "WAITING_FOR_USER"
    assert result["ready"] is False


def test_web_repair_does_not_plan_local_runtime_reinstall(tmp_path: Path, monkeypatch):
    context = _context(tmp_path)
    save_setup_state(SetupState(), context.state_path)
    monkeypatch.setattr(
        setup_assistant,
        "run_setup_diagnostics",
        lambda **_kwargs: {
            "sections": {
                "LOCAL_OPS": {"status": "PASS", "active_runtime_exists": True},
                "CODEX": {"status": "PASS"},
            }
        },
    )
    installation = setup_assistant.WebInstallation(
        status="PASS",
        installed=True,
        install_path=str(tmp_path / "launcher"),
        executable=str(tmp_path / "launcher" / "Codex Web GPT.exe"),
        version="6.1.1",
        version_source="test",
        launcher_running=True,
        profile_present=True,
        launcher_state_path=str(tmp_path / "launcher-state.json"),
    )

    result = plan_web_repair(
        context=context,
        user_home=tmp_path / "home",
        installation=installation,
        launcher_state={"browserSmokePassed": False},
    )

    assert result["status"] == "WAITING_FOR_USER"
    assert result["local_ops_health"]["status"] == "PASS"
    assert result["findings"]
    assert all(item["repair_scope"] == "chatgpt-web" for item in result["findings"])
    assert "do not trigger Local Ops runtime reinstall" in result["rule"]
