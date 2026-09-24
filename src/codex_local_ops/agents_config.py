from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from .backup_ops import BackupRecord, restore_backup, transactional_write_bytes
from .config import install_root

MANAGED_BLOCK_VERSION = 1
BEGIN_MARKER = "<!-- CODEX_LOCAL_OPS_SETUP_ASSISTANT_START -->"
END_MARKER = "<!-- CODEX_LOCAL_OPS_SETUP_ASSISTANT_END -->"

MANAGED_POLICY_LINES = (
    "# Codex Local Ops managed policy",
    f"Managed policy version: {MANAGED_BLOCK_VERSION}",
    "",
    "- Prefer the configured `codexLocalOps` execution layer for operations on the real Windows host.",
    "- When a project is already open, use the current opened workspace as the primary project context.",
    "- Treat `MyProject`, `ProjectName`, `ExampleProject`, and `YourProject` as placeholders; never create them as literal project directories unless the user explicitly asks for those names.",
    "- If an example or referenced project path is missing, search for the intended existing project or report ambiguity instead of creating the example path automatically.",
    "- A Native2 or restricted sandbox failure to launch a Windows executable does not prove that the Windows host component is broken.",
    "- Do not bootstrap or reinstall Local Ops merely because a Native2 sandbox cannot launch host executables.",
    "- Local project editing, testing, and building are allowed inside trusted workspaces.",
    "- Do not use destructive Git operations such as hard reset, destructive clean, force push, or published-history rewrite.",
    "- Paid AI APIs require explicit user approval.",
    "- Public or remote mutations require approval unless the user already explicitly authorized that action.",
)


class AgentsConfigError(RuntimeError):
    """Raised when a managed AGENTS.md block is ambiguous or cannot be changed safely."""


@dataclass(frozen=True, slots=True)
class AgentsConfigResult:
    path: str
    changed: bool
    block_version: int
    backup: BackupRecord | None = None


def agents_path(user_home: Path | None = None) -> Path:
    home = Path(user_home) if user_home is not None else Path.home()
    return home / ".codex" / "AGENTS.md"


def agents_backup_root(root: Path | None = None) -> Path:
    return Path(root) if root is not None else install_root() / "setup" / "backups" / "agents"


def render_managed_block(*, newline: str = "\n") -> str:
    return newline.join((BEGIN_MARKER, *MANAGED_POLICY_LINES, END_MARKER))


def _read_text_preserve_newlines(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return handle.read()


def _detect_newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _managed_span(text: str) -> tuple[int, int] | None:
    begin_count = text.count(BEGIN_MARKER)
    end_count = text.count(END_MARKER)
    if begin_count == 0 and end_count == 0:
        return None
    if begin_count != 1 or end_count != 1:
        raise AgentsConfigError("AGENTS.md has duplicate or unbalanced Codex Local Ops managed markers")
    start = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER)
    if end < start:
        raise AgentsConfigError("AGENTS.md managed markers are in the wrong order")
    return start, end + len(END_MARKER)


def _append_block(text: str, block: str, newline: str) -> str:
    if not text:
        return block + newline
    if text.endswith(newline * 2):
        separator = ""
    elif text.endswith(newline):
        separator = newline
    else:
        separator = newline * 2
    return text + separator + block + newline


def _validate_managed_file(path: Path) -> None:
    try:
        text = _read_text_preserve_newlines(path)
    except (OSError, UnicodeError) as exc:
        raise AgentsConfigError(f"Cannot read AGENTS.md {path}: {exc}") from exc
    span = _managed_span(text)
    if span is None:
        raise AgentsConfigError("Managed AGENTS.md block is missing after write")
    newline = _detect_newline(text)
    if text[span[0] : span[1]] != render_managed_block(newline=newline):
        raise AgentsConfigError("Managed AGENTS.md block validation failed")


def update_managed_agents(
    *,
    path: Path | None = None,
    user_home: Path | None = None,
    backup_root: Path | None = None,
    transaction_id: str | None = None,
) -> AgentsConfigResult:
    target = Path(path) if path is not None else agents_path(user_home)
    try:
        text = _read_text_preserve_newlines(target) if target.exists() else ""
    except (OSError, UnicodeError) as exc:
        raise AgentsConfigError(f"Cannot read AGENTS.md {target}: {exc}") from exc

    newline = _detect_newline(text)
    block = render_managed_block(newline=newline)
    span = _managed_span(text)
    if span is None:
        rendered = _append_block(text, block, newline)
    else:
        if text[span[0] : span[1]] == block:
            return AgentsConfigResult(path=str(target), changed=False, block_version=MANAGED_BLOCK_VERSION)
        rendered = text[: span[0]] + block + text[span[1] :]

    transaction = transaction_id or f"agents-config-{uuid.uuid4().hex}"
    backups = Path(backup_root) if backup_root is not None else agents_backup_root()
    backup = transactional_write_bytes(
        target,
        rendered.encode("utf-8"),
        backup_root=backups,
        transaction_id=transaction,
        kind="agents-config",
        validator=_validate_managed_file,
    )
    return AgentsConfigResult(
        path=str(target),
        changed=True,
        block_version=MANAGED_BLOCK_VERSION,
        backup=backup,
    )


def restore_managed_agents(backup: BackupRecord) -> Path:
    if backup.kind != "agents-config":
        raise AgentsConfigError(f"Backup kind is not agents-config: {backup.kind}")
    return restore_backup(backup)
