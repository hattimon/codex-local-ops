from codex_local_ops import ssh_ops

HOST = {"id": "test-host", "hostname": "example.invalid", "user": "tester", "trusted": True, "permission_profile": "FULL", "identity_file": "private-key-path"}


def test_read_only_rejects_service_mutation_and_shell_chaining():
    assert ssh_ops._command_allowed("systemctl status nginx", "READ_ONLY")
    assert not ssh_ops._command_allowed("service nginx restart", "READ_ONLY")
    assert not ssh_ops._command_allowed("hostname; reboot", "READ_ONLY")


def test_operations_allows_bounded_operational_commands():
    assert ssh_ops._command_allowed("service nginx restart", "OPERATIONS")
    assert ssh_ops._command_allowed("docker restart web", "OPERATIONS")
    assert not ssh_ops._command_allowed("bash -c 'rm -rf /tmp/demo'", "OPERATIONS")
    assert not ssh_ops._command_allowed("docker restart web && reboot", "OPERATIONS")


def test_full_requires_expert_mode(monkeypatch):
    monkeypatch.setattr(ssh_ops, "expert_mode", lambda: False)
    assert not ssh_ops._command_allowed("echo ok", "FULL")
    assert ssh_ops._command_policy("echo ok", "FULL")["blocking_rule"] == "permissions.expert_mode"
    monkeypatch.setattr(ssh_ops, "expert_mode", lambda: True)
    assert ssh_ops._command_allowed("echo ok", "FULL")


def test_full_allowed_when_expert_mode_enabled(monkeypatch):
    monkeypatch.setattr(ssh_ops, "expert_mode", lambda: True)
    assert ssh_ops._command_policy("echo ok", "FULL")["allowed"]


def test_policy_denial_has_structured_fields_and_safe_path(monkeypatch, tmp_path):
    monkeypatch.setattr(ssh_ops, "host_info", lambda _: HOST)
    monkeypatch.setattr(ssh_ops, "expert_mode", lambda: False)
    monkeypatch.setattr(ssh_ops, "config_path", lambda: tmp_path / "resolved" / "config.yaml")
    monkeypatch.setattr(ssh_ops.shutil, "which", lambda _: (_ for _ in ()).throw(AssertionError("SSH must not run")))
    result = ssh_ops.exec_remote("test-host", "cat /tmp/build.exit")
    assert result == {
        "status": "PERMISSION_DENIED", "reason": "Command is not allowed by SSH profile FULL",
        "host": "test-host", "host_profile": "test-host", "target": "tester@example.invalid",
        "policy_mode": "FULL", "command": "cat /tmp/build.exit", "path": "/tmp/build.exit",
        "blocking_rule": "permissions.expert_mode", "config": str(tmp_path / "resolved" / "config.yaml"),
        "blocking_validator": "expert_mode",
    }
    assert "private-key-path" not in repr(result)


def test_untrusted_exec_denial_is_structured_without_ssh(monkeypatch, tmp_path):
    item = {**HOST, "trusted": False}
    monkeypatch.setattr(ssh_ops, "host_info", lambda _: item)
    monkeypatch.setattr(ssh_ops, "config_path", lambda: tmp_path / "config.yaml")
    monkeypatch.setattr(ssh_ops.shutil, "which", lambda _: (_ for _ in ()).throw(AssertionError("SSH must not run")))
    result = ssh_ops.exec_remote("test-host", "cat /tmp/build.exit")
    assert result["status"] == "PERMISSION_DENIED"
    assert result["blocking_rule"] == "ssh.host.trusted"
    assert result["host_profile"] == "test-host" and result["policy_mode"] == "FULL"
    assert result["command"] == "cat /tmp/build.exit" and result["config"] == str(tmp_path / "config.yaml")


def test_policy_denial_rules_and_ambiguous_path(monkeypatch):
    monkeypatch.setattr(ssh_ops, "expert_mode", lambda: False)
    assert ssh_ops._command_policy("reboot", "READ_ONLY")["blocking_rule"] == "ssh.read_only_commands"
    assert ssh_ops._command_policy("reboot", "OPERATIONS")["blocking_rule"] == "ssh.operations_commands"
    policy = ssh_ops._command_policy("cat /tmp/x; reboot", "READ_ONLY")
    assert policy["blocking_rule"] == "ssh.shell_meta"
    assert policy["rejected_token"] == ";"
    assert ssh_ops._safe_command_path("cat /tmp/x; reboot") is None
    assert ssh_ops._safe_command_path("cat /tmp/x extra") is None


def test_transfer_denials_are_structured(monkeypatch, tmp_path):
    monkeypatch.setattr(ssh_ops, "host_info", lambda _: HOST)
    monkeypatch.setattr(ssh_ops, "config_path", lambda: tmp_path / "config.yaml")
    untrusted = {**HOST, "trusted": False}
    monkeypatch.setattr(ssh_ops, "host_info", lambda _: untrusted)
    result = ssh_ops.transfer("test-host", "local-file", "/tmp/remote", upload=True)
    assert result["status"] == "PERMISSION_DENIED"
    assert result["blocking_rule"] == "ssh.host.trusted"
    assert result["host_profile"] == "test-host" and result["policy_mode"] == "FULL"

    monkeypatch.setattr(ssh_ops, "host_info", lambda _: {**HOST, "permission_profile": "READ_ONLY"})
    result = ssh_ops.transfer("test-host", "local-file", "/tmp/remote", upload=True)
    assert result["blocking_rule"] == "ssh.transfer.read_only_upload"
    assert result["path"] == "/tmp/remote"


def test_safe_path_forms():
    assert ssh_ops._safe_command_path("cat /tmp/build.exit") == "/tmp/build.exit"
    assert ssh_ops._safe_command_path("stat /tmp/build.exit") == "/tmp/build.exit"
    assert ssh_ops._safe_command_path("test -e /tmp/build.exit") == "/tmp/build.exit"
    assert ssh_ops._safe_command_path("test -f /tmp/build.exit") == "/tmp/build.exit"
