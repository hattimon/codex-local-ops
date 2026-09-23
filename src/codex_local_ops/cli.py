from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable

from . import git_ops
from .audit import emit
from .config import ensure_config
from .diagnostics import run_diagnostics
from .manager import run_manager
from .safety import require_raw_execution, sanitize
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
    except Exception as exc:
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
    except Exception:
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clops")
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("diagnostics")
    commands.add_parser("init")
    commands.add_parser("manager")
    commands.add_parser("wizard")
    commands.add_parser("first-run-status")

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
