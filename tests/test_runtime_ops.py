from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import tomlkit

from codex_local_ops import runtime_ops
from codex_local_ops.runtime_ops import (
    CandidateRuntime,
    RuntimeOperationError,
    ValidationCheck,
    ValidationReport,
    activate_candidate,
    cleanup_candidate,
    detect_interrupted_updates,
    detect_repair_needs,
    normalize_features,
    rollback_activation,
    stage_candidate_runtime,
    validate_runtime,
)
from codex_local_ops.setup_state import RuntimeRecord, SetupState, load_setup_state, save_setup_state


def _candidate(tmp_path: Path, *, features=(), version="0.1.0b1") -> CandidateRuntime:
    root = tmp_path / "setup" / "staging" / "update-1"
    runtime = root / "runtime.venv"
    runtime.mkdir(parents=True)
    (root / runtime_ops.CANDIDATE_MARKER).write_text(
        json.dumps({"owned_by": "codex-local-ops-setup-assistant", "transaction_id": "update-1"}),
        encoding="utf-8",
    )
    wheel = tmp_path / "codex_local_ops-0.1.0b1-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    return CandidateRuntime(
        transaction_id="update-1",
        candidate_root=str(root),
        runtime_path=str(runtime),
        wheel_path=str(wheel),
        wheel_name=wheel.name,
        wheel_sha256="abc123",
        requested_features=tuple(features),
        expected_version=version,
        staged_at="2026-09-24T10:00:00Z",
    )


def _pass_report(candidate: CandidateRuntime) -> ValidationReport:
    return ValidationReport(
        "PASS",
        candidate.runtime_path,
        (ValidationCheck("package_import", "PASS"),),
        package_version=candidate.expected_version,
        python_version="3.12.10",
    )


def _windows_python(runtime: Path) -> Path:
    path = runtime / "Scripts" / "python.exe"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"python")
    return path


def _config(path: Path, runtime_python: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'[mcp_servers.codexLocalOps]\ncommand = "{str(runtime_python).replace(chr(92), chr(92) * 2)}"\n'
        'args = ["-m", "codex_local_ops.server"]\n',
        encoding="utf-8",
    )


def test_feature_selection_is_deterministic_and_rejects_unknown():
    assert normalize_features(["OBS", "browser", "browser"]) == ("browser", "obs")
    assert normalize_features([]) == ()
    with pytest.raises(RuntimeOperationError, match="UNKNOWN_FEATURE"):
        normalize_features(["gpu"])


def test_stage_candidate_uses_wheel_and_never_editable_install(tmp_path, monkeypatch):
    base = tmp_path / "base-python.exe"
    base.write_bytes(b"python")
    wheel = tmp_path / "pkg.whl"
    wheel.write_bytes(b"artifact")
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[1:3] == ["-m", "venv"]:
            _windows_python(Path(args[3]))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(runtime_ops, "_run", fake_run)
    monkeypatch.setattr(runtime_ops, "runtime_python", lambda runtime, platform_name=None: _windows_python(Path(runtime)))
    candidate = stage_candidate_runtime(
        base_python=base,
        wheel_path=wheel,
        setup_dir=tmp_path / "setup",
        transaction_id="install-1",
        features=["desktop", "obs"],
    )

    install = next(call for call in calls if "pip" in call)
    assert "-e" not in install
    assert "--editable" not in install
    assert install[-1].startswith(wheel.resolve().as_uri())
    assert install[-1].endswith("[desktop,obs]")
    assert Path(candidate.runtime_path).is_dir()
    assert candidate.wheel_sha256


def test_failed_candidate_install_preserves_active_runtime(tmp_path, monkeypatch):
    active = tmp_path / "active"
    active.mkdir()
    sentinel = active / "keep.txt"
    sentinel.write_text("known-good", encoding="utf-8")
    base = tmp_path / "base-python.exe"
    base.write_bytes(b"python")
    wheel = tmp_path / "pkg.whl"
    wheel.write_bytes(b"artifact")

    def fake_run(args, **kwargs):
        if args[1:3] == ["-m", "venv"]:
            _windows_python(Path(args[3]))
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 1, "", "install failed")

    monkeypatch.setattr(runtime_ops, "_run", fake_run)
    monkeypatch.setattr(runtime_ops, "runtime_python", lambda runtime, platform_name=None: _windows_python(Path(runtime)))
    with pytest.raises(RuntimeOperationError, match="PACKAGE_INSTALL_FAILED"):
        stage_candidate_runtime(
            base_python=base,
            wheel_path=wheel,
            setup_dir=tmp_path / "setup",
            transaction_id="update-fail",
        )
    assert sentinel.read_text(encoding="utf-8") == "known-good"


def test_cleanup_removes_only_owned_candidate(tmp_path):
    candidate = _candidate(tmp_path)
    cleanup_candidate(candidate)
    assert not Path(candidate.candidate_root).exists()

    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    bad = CandidateRuntime("x", str(unrelated), str(unrelated / "runtime.venv"), "x.whl", "x.whl", "x", (), None, "now")
    with pytest.raises(RuntimeOperationError, match="CANDIDATE_NOT_OWNED"):
        cleanup_candidate(bad)
    assert unrelated.exists()


def test_validation_missing_python_is_fail(tmp_path):
    report = validate_runtime(tmp_path / "runtime", platform_name="windows")
    assert report.status == "FAIL"
    assert report.checks[0].name == "candidate_python"


def test_validation_unsupported_python_is_fail(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    _windows_python(runtime)

    def fake_run(args, **kwargs):
        command = " ".join(args)
        if "sys.version_info" in command:
            return subprocess.CompletedProcess(args, 0, "[3, 10, 14]\n", "")
        return subprocess.CompletedProcess(args, 0, "{}\n", "")

    monkeypatch.setattr(runtime_ops, "_run", fake_run)
    report = validate_runtime(runtime, require_clops=False, require_diagnostics=False, platform_name="windows")
    check = next(item for item in report.checks if item.name == "python_version")
    assert check.status == "FAIL"
    assert report.status == "FAIL"


def test_validation_package_import_outside_runtime_fails(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    _windows_python(runtime)

    def fake_run(args, **kwargs):
        command = " ".join(args)
        if "sys.version_info" in command:
            out = "[3, 12, 10]\n"
        elif "importlib.metadata" in command:
            out = json.dumps({"version": "0.1.0b1", "file": str(tmp_path / "source" / "codex_local_ops" / "__init__.py")}) + "\n"
        else:
            out = ""
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(runtime_ops, "_run", fake_run)
    report = validate_runtime(runtime, require_clops=False, require_diagnostics=False, platform_name="windows")
    check = next(item for item in report.checks if item.name == "package_import")
    assert check.status == "FAIL"
    assert "outside" in (check.detail or "")


def test_validation_diagnostics_failure_is_fatal(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    python = _windows_python(runtime)
    clops = runtime / "Scripts" / "clops.exe"
    clops.write_bytes(b"clops")
    package_file = runtime / "Lib" / "site-packages" / "codex_local_ops" / "__init__.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("", encoding="utf-8")

    def fake_run(args, **kwargs):
        command = " ".join(args)
        if "sys.version_info" in command:
            out = "[3, 12, 10]\n"
        elif "importlib.metadata" in command:
            out = json.dumps({"version": "0.1.0b1", "file": str(package_file.resolve())}) + "\n"
        elif "diagnostics" in command:
            return subprocess.CompletedProcess(args, 2, "", "diagnostics failed")
        elif "sync_playwright" in command:
            return subprocess.CompletedProcess(args, 3, "", "missing")
        else:
            out = ""
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(runtime_ops, "_run", fake_run)
    report = validate_runtime(runtime, platform_name="windows")
    assert next(x for x in report.checks if x.name == "clops_diagnostics").status == "FAIL"
    assert report.status == "FAIL"
    assert python.exists()


def test_unselected_optional_features_warn_but_do_not_block(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    _windows_python(runtime)
    package_file = runtime / "Lib" / "site-packages" / "codex_local_ops" / "__init__.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("", encoding="utf-8")

    def fake_run(args, **kwargs):
        command = " ".join(args)
        if "sys.version_info" in command:
            return subprocess.CompletedProcess(args, 0, "[3, 12, 10]\n", "")
        if "importlib.metadata" in command:
            return subprocess.CompletedProcess(
                args, 0, json.dumps({"version": "0.1.0b1", "file": str(package_file.resolve())}) + "\n", ""
            )
        if any(name in command for name in ["mss", "playwright", "pywinauto", "obsws_python"]):
            return subprocess.CompletedProcess(args, 1, "", "missing optional")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(runtime_ops, "_run", fake_run)
    report = validate_runtime(runtime, require_clops=False, require_diagnostics=False, platform_name="windows")
    assert report.status == "PASS"
    assert all(x.status == "WARNING" for x in report.checks if x.name.startswith("feature_"))
    assert next(x for x in report.checks if x.name == "playwright_chromium").status == "WARNING"


def test_selected_browser_distinguishes_package_from_chromium(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    _windows_python(runtime)
    package_file = runtime / "Lib" / "site-packages" / "codex_local_ops" / "__init__.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("", encoding="utf-8")

    def fake_run(args, **kwargs):
        command = " ".join(args)
        if "sys.version_info" in command:
            out = "[3, 12, 10]\n"
            code = 0
        elif "importlib.metadata" in command:
            out = json.dumps({"version": "0.1.0b1", "file": str(package_file.resolve())}) + "\n"
            code = 0
        elif "sync_playwright" in command:
            out, code = "", 3
        elif "import playwright" in command:
            out, code = "", 0
        elif any(name in command for name in ["mss", "pywinauto", "obsws_python"]):
            out, code = "", 1
        else:
            out, code = "", 0
        return subprocess.CompletedProcess(args, code, out, "missing chromium" if code else "")

    monkeypatch.setattr(runtime_ops, "_run", fake_run)
    report = validate_runtime(
        runtime,
        features=["browser"],
        require_clops=False,
        require_diagnostics=False,
        platform_name="windows",
    )
    assert next(x for x in report.checks if x.name == "feature_browser").status == "PASS"
    assert next(x for x in report.checks if x.name == "playwright_chromium").status == "FAIL"
    assert report.status == "FAIL"


def test_activation_requires_pass_and_preserves_previous_runtime(tmp_path):
    candidate = _candidate(tmp_path)
    _windows_python(Path(candidate.runtime_path))
    active = tmp_path / "home" / ".codex-local-ops-runtime.venv"
    _windows_python(active)
    (active / "old.txt").write_text("old", encoding="utf-8")
    state_path = tmp_path / "setup" / "state.json"
    save_setup_state(
        SetupState(status="ACTIVE", active_runtime=RuntimeRecord(package_version="0.0.9", runtime_path=str(active))),
        state_path,
    )
    config = tmp_path / "home" / ".codex" / "config.toml"
    _config(config, _windows_python(active))

    fail = ValidationReport("FAIL", candidate.runtime_path, (ValidationCheck("x", "FAIL"),))
    with pytest.raises(RuntimeOperationError, match="CANDIDATE_NOT_VALIDATED"):
        activate_candidate(
            candidate,
            fail,
            setup_dir=tmp_path / "setup",
            active_runtime_path=active,
            codex_config_path=config,
            codex_backup_root=tmp_path / "backups" / "config",
            state_path=state_path,
            state_backup_root=tmp_path / "backups" / "state",
        )
    assert (active / "old.txt").exists()

    result = activate_candidate(
        candidate,
        _pass_report(candidate),
        setup_dir=tmp_path / "setup",
        active_runtime_path=active,
        codex_config_path=config,
        codex_backup_root=tmp_path / "backups" / "config",
        state_path=state_path,
        state_backup_root=tmp_path / "backups" / "state",
    )
    state = load_setup_state(state_path)
    assert result.previous_runtime is not None
    assert state.active_runtime is not None and state.active_runtime.package_version == "0.1.0b1"
    assert state.previous_runtime is not None
    assert Path(state.previous_runtime.runtime_path or "") / "old.txt" == Path(state.previous_runtime.runtime_path or "") / "old.txt"
    assert (Path(state.previous_runtime.runtime_path or "") / "old.txt").read_text(encoding="utf-8") == "old"
    assert state.rollback.eligible is True
    assert Path(result.activation_record_path).is_file()


def test_activation_failure_recovers_previous_runtime(tmp_path, monkeypatch):
    candidate = _candidate(tmp_path)
    _windows_python(Path(candidate.runtime_path))
    active = tmp_path / "active"
    _windows_python(active)
    sentinel = active / "known-good.txt"
    sentinel.write_text("safe", encoding="utf-8")
    state_path = tmp_path / "setup" / "state.json"
    save_setup_state(SetupState(status="ACTIVE", active_runtime=RuntimeRecord(runtime_path=str(active))), state_path)
    config = tmp_path / "config.toml"
    config.write_text("model = 'keep'\n", encoding="utf-8")

    def fail_config(**kwargs):
        raise RuntimeError("config registration failed")

    monkeypatch.setattr(runtime_ops, "update_codex_mcp", fail_config)
    with pytest.raises(RuntimeOperationError, match="ACTIVATION_FAILED"):
        activate_candidate(
            candidate,
            _pass_report(candidate),
            setup_dir=tmp_path / "setup",
            active_runtime_path=active,
            codex_config_path=config,
            codex_backup_root=tmp_path / "backups" / "config",
            state_path=state_path,
            state_backup_root=tmp_path / "backups" / "state",
        )
    assert sentinel.read_text(encoding="utf-8") == "safe"
    assert Path(candidate.runtime_path).exists()


def test_rollback_restores_previous_runtime_config_and_state(tmp_path):
    candidate = _candidate(tmp_path)
    _windows_python(Path(candidate.runtime_path))
    active = tmp_path / "home" / ".codex-local-ops-runtime.venv"
    _windows_python(active)
    (active / "old.txt").write_text("old", encoding="utf-8")
    state_path = tmp_path / "setup" / "state.json"
    original_state = SetupState(status="ACTIVE", active_runtime=RuntimeRecord(package_version="0.0.9", runtime_path=str(active)))
    save_setup_state(original_state, state_path)
    config = tmp_path / "home" / ".codex" / "config.toml"
    old_python = tmp_path / "legacy" / "python.exe"
    _config(config, old_python)

    result = activate_candidate(
        candidate,
        _pass_report(candidate),
        setup_dir=tmp_path / "setup",
        active_runtime_path=active,
        codex_config_path=config,
        codex_backup_root=tmp_path / "backups" / "config",
        state_path=state_path,
        state_backup_root=tmp_path / "backups" / "state",
    )
    restored = rollback_activation(Path(result.activation_record_path), state_path=state_path)
    document = tomlkit.parse(config.read_text(encoding="utf-8"))
    assert (active / "old.txt").read_text(encoding="utf-8") == "old"
    assert document["mcp_servers"]["codexLocalOps"]["command"] == str(old_python)
    assert restored.package_version == "0.0.9"
    assert load_setup_state(state_path).active_runtime.package_version == "0.0.9"


def test_interrupted_update_detection(tmp_path):
    tx = tmp_path / "setup" / "transactions" / "update-1"
    tx.mkdir(parents=True)
    record = tx / runtime_ops.ACTIVATION_RECORD
    record.write_text(json.dumps({"phase": "RUNTIME_ACTIVATED"}), encoding="utf-8")
    assert detect_interrupted_updates(tmp_path / "setup") == (record,)
    record.write_text(json.dumps({"phase": "COMMITTED"}), encoding="utf-8")
    assert detect_interrupted_updates(tmp_path / "setup") == ()


def test_repair_detection_wrong_config_interrupted_and_browser_asset(tmp_path):
    active = tmp_path / "home" / ".codex-local-ops-runtime.venv"
    _windows_python(active)
    config = tmp_path / "home" / ".codex" / "config.toml"
    _config(config, tmp_path / "wrong" / "python.exe")
    tx = tmp_path / "setup" / "transactions" / "update-x"
    tx.mkdir(parents=True)
    (tx / runtime_ops.ACTIVATION_RECORD).write_text(json.dumps({"phase": "PREPARED"}), encoding="utf-8")
    validation = ValidationReport(
        "FAIL",
        str(active),
        (
            ValidationCheck("package_import", "PASS"),
            ValidationCheck("feature_browser", "PASS"),
            ValidationCheck("playwright_chromium", "FAIL", "missing"),
        ),
    )
    report = detect_repair_needs(
        active_runtime_path=active,
        codex_config_path=config,
        setup_dir=tmp_path / "setup",
        selected_features=["browser"],
        validation=validation,
        platform_name="windows",
    )
    codes = {issue.code for issue in report.issues}
    assert report.status == "FAIL"
    assert {"WRONG_MCP_RUNTIME_PATH", "INTERRUPTED_UPDATE", "PLAYWRIGHT_CHROMIUM_MISSING"} <= codes


def test_repair_detection_missing_runtime_and_package_failure(tmp_path):
    active = tmp_path / "missing-runtime"
    validation = ValidationReport(
        "FAIL",
        str(active),
        (ValidationCheck("package_import", "FAIL", "missing"),),
    )
    report = detect_repair_needs(
        active_runtime_path=active,
        codex_config_path=tmp_path / "config.toml",
        setup_dir=tmp_path / "setup",
        validation=validation,
        platform_name="windows",
    )
    codes = {issue.code for issue in report.issues}
    assert "ACTIVE_RUNTIME_MISSING" in codes
    assert "PACKAGE_MISSING_OR_BROKEN" in codes


def test_runtime_metadata_and_validation_do_not_persist_secrets(tmp_path):
    candidate = _candidate(tmp_path)
    report = ValidationReport(
        "FAIL",
        candidate.runtime_path,
        (ValidationCheck("x", "FAIL", "Authorization: Bearer super-secret"),),
    )
    runtime_ops._atomic_json(Path(candidate.candidate_root) / "validation.json", report.to_dict())
    text = (Path(candidate.candidate_root) / "validation.json").read_text(encoding="utf-8")
    assert "super-secret" not in text
    assert "REDACTED" in text


def test_stage_and_detection_use_only_explicit_tmp_paths(tmp_path, monkeypatch):
    expected_candidate_root = tmp_path / "setup" / "staging" / "safe-home"
    expected_validation_home = expected_candidate_root / "validation-home"
    base = tmp_path / "base-python.exe"
    base.write_bytes(b"python")
    wheel = tmp_path / "pkg.whl"
    wheel.write_bytes(b"wheel")

    def fake_run(args, **kwargs):
        if args[1:3] == ["-m", "venv"]:
            _windows_python(Path(args[3]))
        env = kwargs.get("env") or {}
        if env.get("CODEX_LOCAL_OPS_HOME"):
            assert Path(env["CODEX_LOCAL_OPS_HOME"]) == expected_validation_home
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(runtime_ops, "_run", fake_run)
    monkeypatch.setattr(runtime_ops, "runtime_python", lambda runtime, platform_name=None: _windows_python(Path(runtime)))
    candidate = stage_candidate_runtime(
        base_python=base,
        wheel_path=wheel,
        setup_dir=tmp_path / "setup",
        transaction_id="safe-home",
    )
    assert Path(candidate.candidate_root) == expected_candidate_root
