from __future__ import annotations

import os
import re
import shlex
import shutil
import socket
from pathlib import Path
from typing import Any

from .config import load_config, save_config
from .processes import run
from .safety import assert_trusted_path, expert_mode


READ_ONLY_COMMANDS = {"hostname", "whoami", "uname", "systeminfo", "uptime", "id", "pwd", "df", "free", "ls", "cat", "docker ps", "docker logs", "systemctl status"}
OPERATIONS_COMMANDS = READ_ONLY_COMMANDS | {
    "docker start", "docker stop", "docker restart", "docker compose ps", "docker compose logs",
    "docker compose start", "docker compose stop", "docker compose restart",
    "systemctl start", "systemctl stop", "systemctl restart", "service",
}
SHELL_META = re.compile(r"[;&|><`\r\n]|\$\(")


def parse_ssh_config(path: Path | None = None) -> list[dict[str, Any]]:
    path = path or Path.home() / ".ssh" / "config"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, value = parts[0].lower(), parts[1].strip()
        if key == "host":
            if current and "*" not in current.get("aliases", []):
                rows.append(current)
            aliases = value.split()
            current = {"id": aliases[0], "name": aliases[0], "alias": aliases[0], "aliases": aliases, "port": 22, "permission_profile": "READ_ONLY", "trusted": False}
        elif current is not None:
            mapping = {"hostname": "hostname", "user": "user", "port": "port", "identityfile": "identity_file", "proxyjump": "proxy_jump", "proxycommand": "proxy_command", "forwardagent": "forward_agent"}
            if key in mapping:
                current[mapping[key]] = int(value) if key == "port" and value.isdigit() else value
    if current and "*" not in current.get("aliases", []):
        rows.append(current)
    return rows


def hosts() -> list[dict[str, Any]]:
    cfg = load_config()
    configured = cfg.get("ssh", {}).get("hosts", []) or []
    imported = parse_ssh_config() if cfg.get("ssh", {}).get("import_ssh_config", True) else []
    by_id: dict[str, dict[str, Any]] = {str(x.get("id") or x.get("alias")): dict(x) for x in imported}
    for item in configured:
        key = str(item.get("id") or item.get("alias") or item.get("hostname"))
        by_id[key] = {**by_id.get(key, {}), **item}
    return list(by_id.values())


def host_info(host: str) -> dict[str, Any]:
    for item in hosts():
        if host in {str(item.get("id")), str(item.get("alias")), str(item.get("name"))}:
            return item
    raise KeyError(f"Unknown SSH host: {host}")


def add_host(item: dict[str, Any]) -> dict:
    required = {"id", "hostname", "user"}
    missing = sorted(required - item.keys())
    if missing:
        raise ValueError(f"Missing host fields: {', '.join(missing)}")
    cfg = load_config()
    host = {
        "id": str(item["id"]), "name": str(item.get("name") or item["id"]), "hostname": str(item["hostname"]),
        "port": int(item.get("port", 22)), "user": str(item["user"]), "alias": str(item.get("alias") or item["id"]),
        "tags": list(item.get("tags", [])), "os": item.get("os"), "permission_profile": str(item.get("permission_profile", "READ_ONLY")).upper(),
        "trusted": bool(item.get("trusted", False)),
    }
    for key in ("identity_file", "proxy_jump", "proxy_command", "forward_agent"):
        if item.get(key) is not None:
            host[key] = item[key]
    if host["permission_profile"] not in {"READ_ONLY", "OPERATIONS", "FULL"}:
        raise ValueError("permission_profile must be READ_ONLY, OPERATIONS, or FULL")
    entries = [x for x in cfg["ssh"].get("hosts", []) if x.get("id") != host["id"]]
    entries.append(host)
    cfg["ssh"]["hosts"] = entries
    save_config(cfg)
    return {"status": "OK", "host": host}


def remove_host(host_id: str) -> dict[str, Any]:
    cfg = load_config()
    entries = cfg.get("ssh", {}).get("hosts", []) or []
    kept = [x for x in entries if str(x.get("id")) != str(host_id)]
    removed = len(entries) - len(kept)
    cfg.setdefault("ssh", {})["hosts"] = kept
    save_config(cfg)
    return {"status": "OK", "removed": removed, "id": str(host_id)}


def import_hosts(path: str | None = None, *, persist: bool = False) -> dict[str, Any]:
    source = Path(path).expanduser() if path else Path.home() / ".ssh" / "config"
    imported = parse_ssh_config(source)
    if persist:
        for item in imported:
            if item.get("hostname") and item.get("user"):
                add_host(item)
    return {"status": "OK", "source": str(source), "hosts": imported, "persisted": bool(persist)}


def _target_args(item: dict[str, Any]) -> list[str]:
    target = f"{item.get('user')}@{item.get('hostname')}"
    args = ["-p", str(item.get("port", 22)), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=ask"]
    if item.get("identity_file"):
        args.extend(["-i", os.path.expanduser(str(item["identity_file"]))])
    if item.get("proxy_jump"):
        args.extend(["-J", str(item["proxy_jump"])])
    if item.get("forward_agent") and str(item["forward_agent"]).lower() in {"yes", "true"}:
        args.append("-A")
    return args + [target]


def _command_allowed(command: str, profile: str) -> bool:
    profile = profile.upper()
    if profile == "FULL":
        return expert_mode()
    if SHELL_META.search(command):
        return False
    normalized = " ".join(shlex.split(command, posix=True)).strip()
    if profile == "READ_ONLY":
        return any(normalized == prefix or normalized.startswith(prefix + " ") for prefix in READ_ONLY_COMMANDS)
    if profile == "OPERATIONS":
        if normalized.startswith("service "):
            parts = normalized.split()
            return len(parts) == 3 and parts[2] in {"status", "start", "stop", "restart"}
        return any(normalized == prefix or normalized.startswith(prefix + " ") for prefix in OPERATIONS_COMMANDS - {"service"})
    return False


def test(host: str, timeout: int = 15) -> dict:
    item = host_info(host)
    exe = shutil.which("ssh")
    if not exe:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "ssh executable not found"}
    args = [exe, *_target_args(item), "hostname"]
    result = run(args, timeout=timeout)
    result["status"] = "OK" if result["exit_code"] == 0 else "FAILED"
    return result


def exec_remote(host: str, command: str, timeout: int = 120) -> dict:
    item = host_info(host)
    profile = str(item.get("permission_profile", "READ_ONLY"))
    if not item.get("trusted", False):
        return {"status": "PERMISSION_DENIED", "reason": "Host is not trusted", "host": host}
    if not _command_allowed(command, profile):
        return {"status": "PERMISSION_DENIED", "reason": f"Command is not allowed by SSH profile {profile}", "host": host}
    exe = shutil.which("ssh")
    if not exe:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "ssh executable not found"}
    result = run([exe, *_target_args(item), command], timeout=timeout)
    result["status"] = "OK" if result["exit_code"] == 0 else "FAILED"
    return result


def transfer(host: str, local_path: str, remote_path: str, *, upload: bool) -> dict:
    item = host_info(host)
    profile = str(item.get("permission_profile", "READ_ONLY")).upper()
    if not item.get("trusted", False):
        return {"status": "PERMISSION_DENIED", "reason": "Host is not trusted"}
    if upload and profile == "READ_ONLY":
        return {"status": "PERMISSION_DENIED", "reason": "READ_ONLY host does not allow uploads"}
    local = assert_trusted_path(local_path, must_exist=upload)
    exe = shutil.which("scp")
    if not exe:
        return {"status": "CAPABILITY_UNAVAILABLE", "reason": "scp executable not found"}
    target = f"{item.get('user')}@{item.get('hostname')}:{remote_path}"
    args = [exe, "-P", str(item.get("port", 22)), "-o", "StrictHostKeyChecking=ask"]
    if upload:
        args += [str(local), target]
    else:
        local.parent.mkdir(parents=True, exist_ok=True)
        args += [target, str(local)]
    result = run(args, timeout=300)
    result["status"] = "OK" if result["exit_code"] == 0 else "FAILED"
    return result


def socket_probe(hostname: str, port: int, timeout: float = 3.0) -> dict:
    try:
        with socket.create_connection((hostname, port), timeout=timeout):
            return {"status": "OK", "reachable": True}
    except OSError as exc:
        return {"status": "FAILED", "reachable": False, "reason": str(exc)}
