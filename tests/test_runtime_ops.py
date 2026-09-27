from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import replace
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
        wheel_sha256=hashlib.sha256(wheel.read_bytes()).hexdigest(),
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


def test_wheel_requirement_without_features_is_local_file_uri(tmp_path):
    wheel = tmp_path / "pkg.whl"
    wheel.write_bytes(b"artifact")

    assert runtime_ops._wheel_requirement(wheel) == wheel.resolve().as_uri()


def test_wheel_requirement_with_features_is_pep508_direct_reference(tmp_path):
    wheel = tmp_path / "pkg.whl"
    wheel.write_bytes(b"artifact")

    requirement = runtime_ops._wheel_requirement(
        wheel,
        ["windows", "browser", "desktop", "browser", "WINDOWS"],
    )

    assert requirement == f"codex-local-ops[desktop,browser,windows] @ {wheel.resolve().as_uri()}"


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
        features=["windows", "browser", "desktop", "browser"],
        install_browser_assets=False,
    )

    install = next(call for call in calls if "pip" in call)
    assert "-e" not in install
    assert "--editable" not in install
    assert install == [
        str(Path(candidate.runtime_path) / "Scripts" / "python.exe"),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        f"codex-local-ops[desktop,browser,windows] @ {wheel.resolve().as_uri()}",
    ]
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



def _patch_activation_paths(monkeypatch):
    def fake_python(runtime, *, platform_name=None):
        return _windows_python(Path(runtime))

    def fake_clops(runtime, *, platform_name=None):
        path = Path(runtime) / "Scripts" / "clops.exe"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"launcher")
        return path

    monkeypatch.setattr(runtime_ops, "runtime_python", fake_python)
    monkeypatch.setattr(runtime_ops, "runtime_clops", fake_clops)


def _activation_sandbox(tmp_path: Path, *, version="0.1.0b1", features=()):
    candidate = _candidate(tmp_path, features=features, version=version)
    _windows_python(Path(candidate.runtime_path))
    active = tmp_path / "home" / ".codex-local-ops-runtime.venv"
    active_python = _windows_python(active)
    sentinel = active / "known-good.txt"
    sentinel.write_text("previous-runtime", encoding="utf-8")
    state_path = tmp_path / "setup" / "state.json"
    previous = RuntimeRecord(
        package_version="0.1.0b1",
        runtime_path=str(active),
        features=list(features),
    )
    save_setup_state(SetupState(status="ACTIVE", active_runtime=previous), state_path)
    config = tmp_path / "home" / ".codex" / "config.toml"
    _config(config, active_python)
    return candidate, active, state_path, config, sentinel


def _activate_for_test(candidate, active, state_path, config, tmp_path):
    return activate_candidate(
        candidate,
        _pass_report(candidate),
        setup_dir=tmp_path / "setup",
        active_runtime_path=active,
        codex_config_path=config,
        codex_backup_root=tmp_path / "backups" / "config",
        state_path=state_path,
        state_backup_root=tmp_path / "backups" / "state",
    )


def _activation_runner(
    calls,
    *,
    package_version="0.1.0b1",
    refresh_returncode=0,
    clops_returncode=0,
    import_returncode=0,
    import_file=None,
):
    def fake_run(args, **kwargs):
        argv = list(args)
        calls.append({"args": argv, "kwargs": kwargs})
        if argv[1:4] == ["-m", "pip", "install"]:
            if refresh_returncode == 0:
                active = Path(argv[0]).parent.parent
                package_file = active / "Lib" / "site-packages" / "codex_local_ops" / "__init__.py"
                package_file.parent.mkdir(parents=True, exist_ok=True)
                package_file.write_text("", encoding="utf-8")
            return subprocess.CompletedProcess(
                args,
                refresh_returncode,
                "",
                "refresh failed" if refresh_returncode else "",
            )
        if Path(argv[0]).name.casefold() == "clops.exe":
            return subprocess.CompletedProcess(
                args,
                clops_returncode,
                "",
                "launcher failed" if clops_returncode else "",
            )
        if len(argv) > 2 and argv[1] == "-c":
            active = Path(argv[0]).parent.parent
            package_file = import_file or (
                active / "Lib" / "site-packages" / "codex_local_ops" / "__init__.py"
            )
            output = json.dumps({"version": package_version, "file": str(Path(package_file).resolve())}) + "\n"
            return subprocess.CompletedProcess(
                args,
                import_returncode,
                output if import_returncode == 0 else "",
                "import failed" if import_returncode else "",
            )
        raise AssertionError(f"Unexpected activation command: {argv}")

    return fake_run


def _assert_activation_rolled_back(candidate, active, state_path, config, sentinel, original_state, original_config):
    assert sentinel.read_text(encoding="utf-8") == "previous-runtime"
    assert Path(candidate.runtime_path).is_dir()
    assert state_path.read_bytes() == original_state
    assert config.read_bytes() == original_config


def test_activation_requires_pass_and_preserves_previous_runtime(tmp_path, monkeypatch):
    _patch_activation_paths(monkeypatch)
    candidate, active, state_path, config, sentinel = _activation_sandbox(tmp_path)
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
    assert sentinel.is_file()

    calls = []
    monkeypatch.setattr(runtime_ops, "_run", _activation_runner(calls))
    result = _activate_for_test(candidate, active, state_path, config, tmp_path)
    state = load_setup_state(state_path)
    assert result.previous_runtime is not None
    assert state.active_runtime is not None and state.active_runtime.package_version == "0.1.0b1"
    assert state.previous_runtime is not None
    assert (Path(state.previous_runtime.runtime_path or "") / "known-good.txt").read_text(encoding="utf-8") == "previous-runtime"
    assert state.rollback.eligible is True
    assert Path(result.activation_record_path).is_file()


def test_activation_failure_recovers_previous_runtime(tmp_path, monkeypatch):
    _patch_activation_paths(monkeypatch)
    candidate, active, state_path, config, sentinel = _activation_sandbox(tmp_path)
    original_state = state_path.read_bytes()
    original_config = config.read_bytes()

    def fail_config(**kwargs):
        raise RuntimeError("config registration failed")

    calls = []
    monkeypatch.setattr(runtime_ops, "_run", _activation_runner(calls))
    monkeypatch.setattr(runtime_ops, "update_codex_mcp", fail_config)
    with pytest.raises(RuntimeOperationError, match="ACTIVATION_FAILED"):
        _activate_for_test(candidate, active, state_path, config, tmp_path)
    _assert_activation_rolled_back(candidate, active, state_path, config, sentinel, original_state, original_config)
    assert len(calls) == 3


def test_activation_artifact_hash_mismatch_rolls_back_runtime_and_state(tmp_path, monkeypatch):
    _patch_activation_paths(monkeypatch)
    candidate, active, state_path, config, sentinel = _activation_sandbox(tmp_path)
    original_state = state_path.read_bytes()
    original_config = config.read_bytes()
    candidate = replace(candidate, wheel_sha256="0" * 64)
    calls = []
    monkeypatch.setattr(runtime_ops, "_run", _activation_runner(calls))

    with pytest.raises(RuntimeOperationError, match="ARTIFACT_HASH_MISMATCH") as error:
        _activate_for_test(candidate, active, state_path, config, tmp_path)

    assert error.value.code == "ARTIFACT_HASH_MISMATCH"
    _assert_activation_rolled_back(candidate, active, state_path, config, sentinel, original_state, original_config)
    assert calls == []
    record = json.loads(Path(runtime_ops.activation_record_path(tmp_path / "setup", candidate.transaction_id)).read_text())
    assert record["phase"] == "ROLLED_BACK"


def test_activation_refresh_failure_rolls_back_runtime_and_state(tmp_path, monkeypatch):
    _patch_activation_paths(monkeypatch)
    candidate, active, state_path, config, sentinel = _activation_sandbox(tmp_path, version="0.1.0b2")
    original_state = state_path.read_bytes()
    original_config = config.read_bytes()
    calls = []
    monkeypatch.setattr(
        runtime_ops,
        "_run",
        _activation_runner(calls, package_version="0.1.0b2", refresh_returncode=1),
    )

    with pytest.raises(RuntimeOperationError, match="PACKAGE_REFRESH_FAILED") as error:
        _activate_for_test(candidate, active, state_path, config, tmp_path)

    assert error.value.code == "PACKAGE_REFRESH_FAILED"
    _assert_activation_rolled_back(candidate, active, state_path, config, sentinel, original_state, original_config)
    assert len(calls) == 1
    assert calls[0]["args"][1:4] == ["-m", "pip", "install"]


def test_activation_post_move_clops_failure_rolls_back_runtime_and_state(tmp_path, monkeypatch):
    _patch_activation_paths(monkeypatch)
    candidate, active, state_path, config, sentinel = _activation_sandbox(tmp_path, version="0.1.0b2")
    original_state = state_path.read_bytes()
    original_config = config.read_bytes()
    calls = []
    monkeypatch.setattr(
        runtime_ops,
        "_run",
        _activation_runner(calls, package_version="0.1.0b2", clops_returncode=1),
    )

    with pytest.raises(RuntimeOperationError, match="ACTIVE_CLOPS_HELP_FAILED") as error:
        _activate_for_test(candidate, active, state_path, config, tmp_path)

    assert error.value.code == "ACTIVE_CLOPS_HELP_FAILED"
    _assert_activation_rolled_back(candidate, active, state_path, config, sentinel, original_state, original_config)
    assert len(calls) == 2
    assert Path(calls[1]["args"][0]).name == "clops.exe"


def test_activation_post_move_import_failure_rolls_back_runtime_and_state(tmp_path, monkeypatch):
    _patch_activation_paths(monkeypatch)
    candidate, active, state_path, config, sentinel = _activation_sandbox(tmp_path, version="0.1.0b2")
    original_state = state_path.read_bytes()
    original_config = config.read_bytes()
    calls = []
    monkeypatch.setattr(
        runtime_ops,
        "_run",
        _activation_runner(calls, package_version="0.1.0b2", import_returncode=1),
    )

    with pytest.raises(RuntimeOperationError, match="ACTIVE_PACKAGE_IMPORT_FAILED"):
        _activate_for_test(candidate, active, state_path, config, tmp_path)

    _assert_activation_rolled_back(candidate, active, state_path, config, sentinel, original_state, original_config)
    assert len(calls) == 3
    assert calls[2]["args"][1] == "-c"


def test_activation_rejects_import_outside_active_runtime(tmp_path, monkeypatch):
    _patch_activation_paths(monkeypatch)
    candidate, active, state_path, config, sentinel = _activation_sandbox(tmp_path, version="0.1.0b2")
    original_state = state_path.read_bytes()
    original_config = config.read_bytes()
    outside_file = tmp_path / "source" / "codex_local_ops" / "__init__.py"
    calls = []
    monkeypatch.setattr(
        runtime_ops,
        "_run",
        _activation_runner(calls, package_version="0.1.0b2", import_file=outside_file),
    )

    with pytest.raises(RuntimeOperationError, match="ACTIVE_PACKAGE_IMPORT_OUTSIDE_RUNTIME"):
        _activate_for_test(candidate, active, state_path, config, tmp_path)

    _assert_activation_rolled_back(candidate, active, state_path, config, sentinel, original_state, original_config)
    assert len(calls) == 3


def test_activation_refreshes_entrypoints_before_config_and_preserves_previous_runtime(tmp_path, monkeypatch):
    _patch_activation_paths(monkeypatch)
    features = ("desktop", "browser", "windows")
    candidate, active, state_path, config, _sentinel = _activation_sandbox(
        tmp_path,
        version="0.1.0b2",
        features=features,
    )
    calls = []
    monkeypatch.setattr(runtime_ops, "_run", _activation_runner(calls, package_version="0.1.0b2"))

    result = _activate_for_test(candidate, active, state_path, config, tmp_path)
    state = load_setup_state(state_path)
    refresh_args = calls[0]["args"]
    assert refresh_args == [
        str(active / "Scripts" / "python.exe"),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--force-reinstall",
        "--no-deps",
        str(Path(candidate.wheel_path).resolve()),
    ]
    assert Path(calls[1]["args"][0]) == active / "Scripts" / "clops.exe"
    assert calls[1]["args"][1:] == ["--help"]
    assert Path(calls[2]["args"][0]) == active / "Scripts" / "python.exe"
    assert calls[2]["args"][1] == "-c"
    assert calls[0]["kwargs"]["env"]["CODEX_LOCAL_OPS_HOME"] == str(
        Path(candidate.candidate_root) / "validation-home"
    )
    assert result.active_runtime.package_version == "0.1.0b2"
    assert result.active_runtime.features == list(features)
    assert result.previous_runtime is not None
    assert (Path(result.previous_runtime.runtime_path) / "known-good.txt").read_text(encoding="utf-8") == "previous-runtime"
    assert state.active_runtime is not None and state.active_runtime.package_version == "0.1.0b2"
    assert state.previous_runtime is not None and state.rollback.eligible is True
    assert json.loads(Path(result.activation_record_path).read_text())["phase"] == "COMMITTED"


def test_rollback_restores_previous_runtime_config_and_state(tmp_path, monkeypatch):
    _patch_activation_paths(monkeypatch)
    candidate, active, state_path, config, sentinel = _activation_sandbox(tmp_path)
    calls = []
    monkeypatch.setattr(runtime_ops, "_run", _activation_runner(calls))
    result = _activate_for_test(candidate, active, state_path, config, tmp_path)
    restored = rollback_activation(Path(result.activation_record_path), state_path=state_path)
    document = tomlkit.parse(config.read_text(encoding="utf-8"))
    assert sentinel.read_text(encoding="utf-8") == "previous-runtime"
    assert document["mcp_servers"]["codexLocalOps"]["command"] == str(active / "Scripts" / "python.exe")
    assert restored.package_version == "0.1.0b1"
    assert load_setup_state(state_path).active_runtime.package_version == "0.1.0b1"
    assert len(calls) == 3

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
