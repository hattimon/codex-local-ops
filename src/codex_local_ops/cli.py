from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import git_ops
from .agent_runtime import AgentRuntime
from .audit import emit
from .config import ensure_config
from .diagnostics import run_diagnostics
from .manager import run_manager
from .safety import require_raw_execution, sanitize
from .setup_assistant import (
    default_context,
    plan_web_repair,
    plan_web_setup,
    record_web_verification,
    run_web_setup_status,
)
from .wizard import configure_first_run, first_run_status


def _wizard_cli() -> dict:
    current = first_run_status()
    default_root = str(Path.cwd().resolve())
    root = input(f"Trusted project root [{default_root}]: ").strip() or default_root
    profile = input(f"Local profile SAFE/DEVELOPER/FULL [{current['local_profile']}]: ").strip() or current["local_profile"]
    mode = input(f"Computer control OFF/SAFE/INTERACTIVE/FULL [{current['computer_mode']}]: ").strip() or current["computer_mode"]
    return configure_first_run([root], local_profile=profile, computer_mode=mode)


def _local_action(
    name: str,
    audit_args: dict[str, Any],
    callback: Callable[[], dict[str, Any]],
    *,
    write: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        if write:
            require_raw_execution()
        result = callback()
        if not isinstance(result, dict):
            result = {"status": "OK", "data": result}
    except PermissionError as exc:
        result = {"status": "PERMISSION_DENIED", "reason": str(exc)}
    except FileNotFoundError as exc:
        result = {"status": "NOT_FOUND", "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - CLI boundary returns a structured failure.
        result = {"status": "FAILED", "reason": str(exc)}
    result = sanitize(result)
    try:
        emit(
            name,
            audit_args,
            result,
            started,
            approval_class="local-write" if write else "local-read",
        )
    except Exception:  # noqa: BLE001, S110 - audit failure must not replace the command result.
        pass
    return result


def _git_commit(path: str, message: str, add: list[str]) -> dict[str, Any]:
    if add:
        staged = git_ops.stage(path, add)
        if staged.get("status") != "OK":
            return staged
    return git_ops.commit(path, message)


def _print_result(result: dict[str, Any]) -> None:
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result.get("status") not in {"OK", "STARTED", "COMPLETED"}:
        raise SystemExit(1)


def _agent_result(result: dict[str, Any]) -> dict[str, Any]:
    """Keep the public CLI result stable and independent of adapter internals."""
    return sanitize(
        {
            "job_id": result.get("job_id"),
            "status": result.get("status"),
            "provider": result.get("selected_provider"),
            "model": result.get("selected_model"),
            "attempts": result.get("attempts", []),
            "fallback_history": result.get("fallback_history", []),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "duration": result.get("duration_ms", 0),
            "usage": result.get("usage", {}),
            "error": result.get("error"),
            "base_head": result.get("base_head"),
            "branch": result.get("branch"),
            "changed_files": result.get("changed_files", []),
            "diff_summary": result.get("diff_summary", {}),
            "repo_lock_status": result.get("repo_lock_status"),
        }
    )


def _print_agent_result(result: dict[str, Any], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(f"Job: {result.get('job_id')}")
        print(f"Status: {result.get('status')}")
        print(f"Provider: {result.get('provider') or '-'}")
        print(f"Model: {result.get('model') or '-'}")
        print(f"Fallback used: {'yes' if result.get('fallback_history') else 'no'}")
        print(f"Duration: {result.get('duration', 0)} ms")
        changed_files = result.get("changed_files", [])
        print(f"Changed files: {len(changed_files)}")
        if changed_files:
            print("Paths: " + ", ".join(changed_files[:20]))
        if result.get("stdout"):
            print("Output:")
            print(result["stdout"])
        if result.get("stderr"):
            print("Stderr:")
            print(result["stderr"])
        if result.get("error"):
            print(f"Error: {result['error']}")
    if result.get("status") != "COMPLETED":
        raise SystemExit(1)


def _run_agent(args: argparse.Namespace) -> dict[str, Any]:
    if args.timeout is not None and args.timeout < 1:
        raise ValueError("timeout must be at least one second")
    # ``_repo_root`` performs the existing trusted-root, symlink and Git-worktree checks.
    repo_root, _ = git_ops._repo_root(args.repo)
    runtime = AgentRuntime()
    return runtime.run(
        args.task,
        repo_root,
        sensitivity=args.sensitivity,
        preferred_provider=args.provider,
        preferred_model=args.model,
        timeout=args.timeout,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clops")
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("diagnostics")
    commands.add_parser("init")
    commands.add_parser("manager")
    commands.add_parser("wizard")
    commands.add_parser("first-run-status")

    setup_parser = commands.add_parser("setup-assistant", help="Setup Assistant lifecycle and Web integration")
    setup_commands = setup_parser.add_subparsers(dest="setup_command", required=True)
    setup_commands.add_parser("web-status", help="Inspect ChatGPT Web / Native2 setup without changing it")
    setup_commands.add_parser("web-plan", help="Plan install/update and guided ChatGPT Web setup")
    setup_commands.add_parser("web-repair", help="Plan Web integration repairs without repairing Local Ops")
    web_verify = setup_commands.add_parser("web-verify", help="Inspect or record read-only end-to-end verification")
    web_verify.add_argument(
        "--confirm",
        action="append",
        choices=(
            "FULL_HARNESS_TO_CODEX",
            "CODEX_TO_CODEXLOCALOPS",
            "WINDOWS_HOST_VISIBLE_THROUGH_LOCALOPS",
        ),
        default=[],
    )

    agent_parser = commands.add_parser("agent", help="Run a local coding agent without publishing changes")
    agent_commands = agent_parser.add_subparsers(dest="agent_command", required=True)
    agent_run = agent_commands.add_parser("run", help="Run an approved local agent task in a trusted Git repository")
    agent_run.add_argument("--repo", required=True)
    agent_run.add_argument("--task", required=True)
    agent_run.add_argument("--sensitivity", choices=("public", "private", "sensitive"), default="private")
    agent_run.add_argument("--provider")
    agent_run.add_argument("--model")
    agent_run.add_argument("--timeout", type=int)
    agent_run.add_argument("--json", dest="json_output", action="store_true")

    git_parser = commands.add_parser("git", help="Local Git operations")
    git_commands = git_parser.add_subparsers(dest="git_command", required=True)

    git_status = git_commands.add_parser("status")
    git_status.add_argument("--path", default=".")

    git_commit = git_commands.add_parser("commit")
    git_commit.add_argument("--path", default=".")
    git_commit.add_argument("-m", "--message", required=True)
    git_commit.add_argument("--add", action="append", default=[], metavar="PATHSPEC")

    git_push = git_commands.add_parser("push")
    git_push.add_argument("--path", default=".")
    git_push.add_argument("--remote", default="origin")
    git_push.add_argument("--branch")
    git_push.add_argument("--async", dest="async_job", action="store_true")

    git_publish = git_commands.add_parser("publish")
    git_publish.add_argument("--path", default=".")
    git_publish.add_argument("--remote", default="origin")
    git_publish.add_argument("--branch")

    github_parser = commands.add_parser("github", help="Local GitHub CLI operations")
    github_commands = github_parser.add_subparsers(dest="github_command", required=True)

    github_commands.add_parser("auth-status")

    workflow_status = github_commands.add_parser("workflow-status")
    workflow_status.add_argument("--path", default=".")
    workflow_status.add_argument("--limit", type=int, default=20)

    release_create = github_commands.add_parser("release-create")
    release_create.add_argument("--path", default=".")
    release_create.add_argument("--tag", required=True)
    release_create.add_argument("--title")
    release_create.add_argument("--notes")
    release_create.add_argument("--draft", action="store_true")
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "setup-assistant":
        context = default_context()
        if args.setup_command == "web-status":
            result = run_web_setup_status(context=context)
        elif args.setup_command == "web-plan":
            result = plan_web_setup(context=context)
        elif args.setup_command == "web-repair":
            result = plan_web_repair(context=context)
        else:
            if args.confirm:
                record_web_verification(context=context, results={key: "PASS" for key in args.confirm})
            result = run_web_setup_status(context=context)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    config_path = ensure_config()

    if args.command in {None, "diagnostics"}:
        print(json.dumps(run_diagnostics(), indent=2, ensure_ascii=False))
        return
    if args.command == "init":
        print(config_path)
        return
    if args.command == "manager":
        print(json.dumps(run_manager(), indent=2, ensure_ascii=False))
        return
    if args.command == "wizard":
        print(json.dumps(_wizard_cli(), indent=2, ensure_ascii=False))
        return
    if args.command == "first-run-status":
        print(json.dumps(first_run_status(), indent=2, ensure_ascii=False))
        return

    if args.command == "agent":
        audit_args = {
            "path": args.repo,
            "sensitivity": args.sensitivity,
            "requested_provider": args.provider,
            "requested_model": args.model,
            "task_sha256": hashlib.sha256(args.task.encode("utf-8")).hexdigest(),
        }

        def action() -> dict[str, Any]:
            try:
                raw = _run_agent(args)
            except PermissionError as exc:
                raw = {"status": "FAILED", "error": str(exc)}
            except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
                raw = {"status": "FAILED", "error": str(exc)}
            result = _agent_result(raw)
            audit_args.update(
                {
                    "job_id": result.get("job_id"),
                    "actual_provider": result.get("provider"),
                    "actual_model": result.get("model"),
                    "fallback_reason": (result.get("fallback_history") or [{}])[-1].get("reason"),
                }
            )
            return result

        result = _local_action("cli_agent_run", audit_args, action, write=True)
        _print_agent_result(result, json_output=args.json_output)
        return

    if args.command == "git":
        if args.git_command == "status":
            result = _local_action(
                "cli_git_status",
                {"path": args.path},
                lambda: git_ops.git(args.path, ["status", "--short", "--branch"]),
            )
        elif args.git_command == "commit":
            result = _local_action(
                "cli_git_commit",
                {"path": args.path, "message_supplied": True, "add": args.add},
                lambda: _git_commit(args.path, args.message, args.add),
                write=True,
            )
        elif args.git_command == "push":
            callback = (
                (lambda: git_ops.push_async(args.path, args.remote, args.branch))
                if args.async_job
                else (lambda: git_ops.push(args.path, args.remote, args.branch))
            )
            result = _local_action(
                "cli_git_push_async" if args.async_job else "cli_git_push",
                {"path": args.path, "remote": args.remote, "branch": args.branch},
                callback,
                write=True,
            )
        else:
            result = _local_action(
                "cli_git_publish",
                {"path": args.path, "remote": args.remote, "branch": args.branch},
                lambda: git_ops.publish(args.path, args.remote, args.branch),
                write=True,
            )
        _print_result(result)
        return

    if args.github_command == "auth-status":
        result = _local_action("cli_github_auth_status", {}, git_ops.github_auth_status)
    elif args.github_command == "workflow-status":
        result = _local_action(
            "cli_github_workflow_status",
            {"path": args.path, "limit": args.limit},
            lambda: git_ops.github_run_list(args.path, args.limit),
        )
    else:
        result = _local_action(
            "cli_github_release_create",
            {
                "path": args.path,
                "tag": args.tag,
                "title_supplied": bool(args.title),
                "notes_supplied": bool(args.notes),
                "draft": args.draft,
            },
            lambda: git_ops.github_release_create(
                args.path,
                args.tag,
                title=args.title,
                notes=args.notes,
                draft=args.draft,
            ),
            write=True,
        )
    _print_result(result)
