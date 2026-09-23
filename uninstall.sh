#!/usr/bin/env sh
set -eu
INSTALL_ROOT="${CODEX_LOCAL_OPS_HOME:-$HOME/.codex-local-ops}"
VENV_ROOT="${CODEX_LOCAL_OPS_VENV:-$INSTALL_ROOT/.venv}"
CONFIG="$HOME/.codex/config.toml"
if [ -f "$CONFIG" ]; then
  cp "$CONFIG" "$CONFIG.backup-$(date +%Y%m%d-%H%M%S)"
  awk 'BEGIN{skip=0} /^\[mcp_servers\.codexLocalOps\]$/{skip=1; next} /^\[/{skip=0} !skip{print}' "$CONFIG" > "$CONFIG.tmp.$$"
  mv "$CONFIG.tmp.$$" "$CONFIG"
fi
rm -rf "$VENV_ROOT"
if [ "${1:-}" = "--remove-config" ]; then rm -rf "$INSTALL_ROOT"; fi
echo 'Codex Local Ops MCP registration and runtime removed.'
