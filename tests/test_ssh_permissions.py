from codex_local_ops import ssh_ops


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
    monkeypatch.setattr(ssh_ops, "expert_mode", lambda: True)
    assert ssh_ops._command_allowed("echo ok", "FULL")
