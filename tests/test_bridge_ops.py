from __future__ import annotations

import json
from pathlib import Path

import pytest

from codex_local_ops.bridge_ops import (
    AUDITED_RELEASE,
    CONNECTOR_NAME,
    WebInstallation,
    classify_execution_failure,
    detect_web_repairs,
    detect_windows_installation,
    dev_interpreter_path,
    guided_web_steps,
    inspect_chatgpt_web,
    launcher_state_path,
    manual_validation_commands,
    plan_web_install_update,
    read_launcher_state,
    record_connection_verification,
    version_is_older,
)
from codex_local_ops.cli import _build_parser
from codex_local_ops.setup_state import load_setup_state


def _installed(tmp_path: Path, *, version: str | None = AUDITED_RELEASE) -> WebInstallation:
    state = tmp_path / "AppData" / "Roaming" / "Codex Web GPT" / "launcher-state.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{}\n", encoding="utf-8")
    install = tmp_path / "Codex Web GPT"
    install.mkdir(exist_ok=True)
    executable = install / "Codex Web GPT.exe"
    executable.write_bytes(b"launcher")
    return WebInstallation(
        status="PASS",
        installed=True,
        install_path=str(install),
        executable=str(executable),
        version=version,
        version_source="registry" if version else None,
        launcher_running=True,
        profile_present=True,
        launcher_state_path=str(state),
    )


def test_version_parsing_and_update_detection():
    assert version_is_older("6.1.0", "6.1.1") is True
    assert version_is_older("v6.1.1", "6.1.1") is False
    assert version_is_older("6.2", "6.1.1") is False
    assert version_is_older(None, "6.1.1") is None


def test_detect_windows_installation_installed_and_absent(tmp_path: Path):
    install = tmp_path / "launcher"
    install.mkdir()
    executable = install / "Codex Web GPT.exe"
    executable.write_bytes(b"launcher")
    appdata = tmp_path / "appdata"
    state = appdata / "Codex Web GPT" / "launcher-state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}\n", encoding="utf-8")

    present = detect_windows_installation(
        user_home=tmp_path,
        appdata=appdata,
        registry_reader=lambda: {"InstallLocation": str(install), "DisplayVersion": "6.1.1"},
        process_probe=lambda: (False, None),
    )
    assert present.installed is True
    assert present.version == "6.1.1"
    assert present.profile_present is True

    absent = detect_windows_installation(
        user_home=tmp_path,
        appdata=tmp_path / "missing-appdata",
        registry_reader=dict,
        process_probe=lambda: (False, None),
    )
    assert absent.status == "NOT_CONFIGURED"
    assert absent.installed is False


def test_launcher_state_reads_only_non_secret_setup_fields(tmp_path: Path):
    state = tmp_path / "launcher-state.json"
    state.write_text(
        json.dumps(
            {
                "version": 1,
                "browserSmokePassed": True,
                "mcpSetupComplete": True,
                "cookie": "secret-cookie",
                "token": "secret-token",
                "authorization": "Bearer secret",
            }
        ),
        encoding="utf-8",
    )

    observed = read_launcher_state(state)
    rendered = json.dumps(observed)
    assert observed["browserSmokePassed"] is True
    assert observed["mcpSetupComplete"] is True
    assert "secret" not in rendered
    assert "cookie" not in observed
    assert "token" not in observed


def test_launcher_state_path_honors_isolated_override_without_creating_files(tmp_path: Path):
    override = tmp_path / "isolated-launcher"
    path = launcher_state_path(user_home=tmp_path, env={"CODEX_WEB_GPT_LAUNCHER_DATA_DIR": str(override)})
    assert path == override / "launcher-state.json"
    assert not override.exists()


def test_guided_flow_waiting_and_pass_states_use_exact_native2_name():
    waiting = {step.name: step for step in guided_web_steps({})}
    assert waiting["CHATGPT_LOGIN"].status == "WAITING_FOR_USER"
    assert waiting["BROWSER_SMOKE_TEST"].status == "WAITING_FOR_USER"
    assert waiting["FULL_HARNESS"].status == "WAITING_FOR_USER"
    assert waiting["CODEX_NATIVE2"].status == "WAITING_FOR_USER"
    assert CONNECTOR_NAME == "Codex Native2"
    assert "named exactly Codex Native2" in (waiting["CODEX_NATIVE2"].instruction or "")

    complete_state = {
        "browserSmokePassed": True,
        "browserSmokeVersion": AUDITED_RELEASE,
        "coreSetupComplete": True,
        "codexCatalogVerified": True,
        "codexRestartRequired": False,
        "mcpRuntimeInstalled": True,
        "mcpSetupComplete": True,
    }
    complete = {step.name: step for step in guided_web_steps(complete_state)}
    assert complete["BROWSER_SMOKE_TEST"].status == "PASS"
    assert complete["WEB_MODELS"].status == "PASS"
    assert complete["FULL_HARNESS"].status == "PASS"
    assert complete["CODEX_NATIVE2"].status == "PASS"
    assert complete["VERIFY_RUNTIME"].status == "PASS"


def test_stale_browser_smoke_is_warning_after_newer_release():
    steps = {step.name: step for step in guided_web_steps({"browserSmokePassed": True, "browserSmokeVersion": "6.1.0"})}
    assert steps["BROWSER_SMOKE_TEST"].status == "WARNING"


def test_install_update_plan_uses_official_checksumming_installer_and_preserves_profile(tmp_path: Path):
    status = inspect_chatgpt_web(
        installation=_installed(tmp_path, version="6.1.0"),
        launcher_state={},
        local_ops_status="PASS",
        codex_local_ops_status="PASS",
    )
    plan = plan_web_install_update(status)
    behavior = plan["installer_behavior"]
    assert plan["action"] == "UPDATE"
    assert behavior["official_release_assets"] is True
    assert behavior["sha256_manifest_verified"] is True
    assert behavior["preserve_launcher_settings"] is True
    assert behavior["preserve_chatgpt_profile"] is True
    assert behavior["uninstall_first"] is False


def test_local_ops_health_remains_separate_from_incomplete_web_setup(tmp_path: Path):
    status = inspect_chatgpt_web(
        installation=_installed(tmp_path),
        launcher_state={"browserSmokePassed": False},
        local_ops_status="PASS",
        codex_local_ops_status="PASS",
    )
    steps = {step["name"]: step for step in status["steps"]}
    assert steps["CODEX_TO_CODEXLOCALOPS"]["status"] == "PASS"
    assert status["ready"] is False
    assert status["incomplete_step"] == "CHATGPT_LOGIN"
    repairs = detect_web_repairs(status)
    assert repairs
    assert all(item["repair_scope"] == "chatgpt-web" for item in repairs)
    assert not any("runtime reinstall" in str(item).lower() for item in repairs)


def test_end_to_end_requires_actual_recorded_proof(tmp_path: Path):
    launcher_state = {
        "browserSmokePassed": True,
        "browserSmokeVersion": AUDITED_RELEASE,
        "coreSetupComplete": True,
        "codexCatalogVerified": True,
        "codexRestartRequired": False,
        "mcpRuntimeInstalled": True,
        "mcpSetupComplete": True,
    }
    waiting = inspect_chatgpt_web(
        installation=_installed(tmp_path),
        launcher_state=launcher_state,
        local_ops_status="PASS",
        codex_local_ops_status="PASS",
    )
    assert waiting["incomplete_step"] == "WINDOWS_HOST_VISIBLE_THROUGH_LOCALOPS"

    ready = inspect_chatgpt_web(
        installation=_installed(tmp_path),
        launcher_state=launcher_state,
        local_ops_status="PASS",
        codex_local_ops_status="PASS",
        connection_chain={"WINDOWS_HOST_VISIBLE_THROUGH_LOCALOPS": "PASS"},
    )
    assert ready["status"] == "READY"
    assert ready["ready"] is True


def test_record_verification_whitelists_fields_and_never_persists_credentials(tmp_path: Path):
    state_path = tmp_path / "setup" / "state.json"
    result = record_connection_verification(
        state_path=state_path,
        results={"WINDOWS_HOST_VISIBLE_THROUGH_LOCALOPS": "PASS"},
    )
    assert result["status"] == "PASS"
    state = load_setup_state(state_path)
    assert state.connection_chain["WINDOWS_HOST_VISIBLE_THROUGH_LOCALOPS"] == "PASS"

    with pytest.raises(ValueError):
        record_connection_verification(state_path=state_path, results={"chatgpt_token": "secret"})
    assert "secret" not in state_path.read_text(encoding="utf-8")


def test_correct_dev_venv_path_and_manual_validation_never_generate_old_wrong_path(tmp_path: Path):
    expected = tmp_path / ".codex-local-ops.venv" / "Scripts" / "python.exe"
    assert dev_interpreter_path(tmp_path) == expected
    commands = manual_validation_commands(Path(r"D:\CODEX\CODEX LOCAL OPS"))
    assert r".codex-local-ops.venv\Scripts\python.exe" in commands
    assert r".codex-local-ops\.venv\Scripts\python.exe" not in commands


@pytest.mark.parametrize(
    "code",
    [
        "AUTHORIZATION_REQUIRED",
        "PERMISSION_REQUIRED",
        "APPROVAL_REQUIRED",
        "SECURITY_DENIED",
        "POLICY_DENIED",
    ],
)
def test_authorization_denial_is_waiting_for_user_and_never_bypassed(code: str):
    result = classify_execution_failure(code, "denied")
    assert result["status"] == "WAITING_FOR_USER"
    assert result["classification"] == "authorization_denial"
    assert result["retry_through_other_executor"] is False


def test_transport_failure_is_classified_as_harness_limitation_not_host_failure():
    result = classify_execution_failure("STREAM_DISCONNECTED", "transport failed")
    assert result["status"] == "WARNING"
    assert result["classification"] == "harness_execution_limitation"
    assert result["retry_through_other_executor"] is False


def test_cli_exposes_non_destructive_web_status_plan_and_repair_commands():
    parser = _build_parser()
    for command in ("web-status", "web-plan", "web-repair", "web-verify"):
        args = parser.parse_args(["setup-assistant", command])
        assert args.command == "setup-assistant"
        assert args.setup_command == command
