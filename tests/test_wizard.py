from codex_local_ops.config import load_config
from codex_local_ops.wizard import configure_first_run, first_run_status


def test_first_run_writes_portable_config(monkeypatch, tmp_path):
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("CODEX_LOCAL_OPS_HOME", str(home / ".codex-local-ops"))

    result = configure_first_run(
        [str(project)],
        local_profile="DEVELOPER",
        computer_mode="SAFE",
        import_ssh_config=True,
    )

    assert result["status"] == "OK"
    assert first_run_status()["completed"] is True
    cfg = load_config()
    assert cfg["projects"]["trusted_roots"] == [str(project.resolve())]
    assert cfg["permissions"]["local_profile"] == "DEVELOPER"
    assert cfg["computer_control"]["mode"] == "SAFE"
