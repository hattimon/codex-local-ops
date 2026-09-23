#!/usr/bin/env sh
# User-level installer for Linux and macOS. No root privileges are required.
set -eu

INSTALL_ROOT="${CODEX_LOCAL_OPS_HOME:-$HOME/.codex-local-ops}"
VENV_ROOT="${CODEX_LOCAL_OPS_VENV:-$INSTALL_ROOT/.venv}"
SOURCE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON="${PYTHON:-}"

is_ai_python() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | grep -Eq 'hermes|ollama|local-ai|llama|inference'; }
valid_python() {
  candidate=$1
  [ -x "$candidate" ] || return 1
  is_ai_python "$candidate" && return 1
  "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

if [ -z "$PYTHON" ]; then
  for candidate in "$(command -v python3 2>/dev/null || true)" "$(command -v python 2>/dev/null || true)"; do
    if [ -n "$candidate" ] && valid_python "$candidate"; then PYTHON=$candidate; break; fi
  done
elif ! valid_python "$PYTHON"; then
  echo "PYTHON must be an independent CPython 3.11+ (not an AI application environment)." >&2; exit 1
fi
[ -n "$PYTHON" ] || { echo "Independent Python 3.11+ not found. Install Python with your OS package manager and retry." >&2; exit 1; }

"$PYTHON" -m venv "$VENV_ROOT"
"$VENV_ROOT/bin/python" -m pip install --upgrade pip
"$VENV_ROOT/bin/python" -m pip install --upgrade "$SOURCE_ROOT"
"$VENV_ROOT/bin/python" -c 'from codex_local_ops.config import ensure_config; print(ensure_config())'

CODEX_CONFIG="$HOME/.codex/config.toml"
mkdir -p "$(dirname "$CODEX_CONFIG")"
[ -f "$CODEX_CONFIG" ] || : > "$CODEX_CONFIG"
backup="$CODEX_CONFIG.backup-$(date +%Y%m%d-%H%M%S)"
cp "$CODEX_CONFIG" "$backup"
tmp="$CODEX_CONFIG.tmp.$$"
awk 'BEGIN{skip=0} /^\[mcp_servers\.codexLocalOps\]$/{skip=1; next} /^\[/{skip=0} !skip{print}' "$CODEX_CONFIG" > "$tmp"
printf '\n[mcp_servers.codexLocalOps]\ncommand = "%s"\nargs = ["-m", "codex_local_ops.server"]\n' "$VENV_ROOT/bin/python" >> "$tmp"
mv "$tmp" "$CODEX_CONFIG"
"$VENV_ROOT/bin/python" -m codex_local_ops.cli diagnostics
codex mcp list
