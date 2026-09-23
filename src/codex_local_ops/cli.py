from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import ensure_config
from .diagnostics import run_diagnostics
from .manager import run_manager
from .wizard import configure_first_run, first_run_status


def _wizard_cli() -> dict:
    current = first_run_status()
    default_root = str(Path.cwd().resolve())
    root = input(f"Trusted project root [{default_root}]: ").strip() or default_root
    profile = input(f"Local profile SAFE/DEVELOPER/FULL [{current['local_profile']}]: ").strip() or current["local_profile"]
    mode = input(f"Computer control OFF/SAFE/INTERACTIVE/FULL [{current['computer_mode']}]: ").strip() or current["computer_mode"]
    return configure_first_run([root], local_profile=profile, computer_mode=mode)


def main() -> None:
    parser = argparse.ArgumentParser(prog="clops")
    parser.add_argument("command", choices=["diagnostics", "init", "manager", "wizard", "first-run-status"], nargs="?", default="diagnostics")
    args = parser.parse_args()
    path = ensure_config()
    if args.command == "init":
        print(path)
    elif args.command == "manager":
        print(json.dumps(run_manager(), indent=2, ensure_ascii=False))
    elif args.command == "wizard":
        print(json.dumps(_wizard_cli(), indent=2, ensure_ascii=False))
    elif args.command == "first-run-status":
        print(json.dumps(first_run_status(), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(run_diagnostics(), indent=2, ensure_ascii=False))
