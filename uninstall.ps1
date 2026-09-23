[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:USERPROFILE '.codex-local-ops'),
    [string]$VenvRoot = (Join-Path $env:USERPROFILE '.codex-local-ops.venv'),
    [switch]$RemoveConfig
)

$ErrorActionPreference = 'Stop'
$configPath = Join-Path $env:USERPROFILE '.codex\config.toml'
if (Test-Path -LiteralPath $configPath) {
    Copy-Item -LiteralPath $configPath -Destination "$configPath.backup-$(Get-Date -Format 'yyyyMMdd-HHmmss')" -Force
    $text = Get-Content -LiteralPath $configPath -Raw
    $text = [regex]::Replace($text, '(?ms)^\[mcp_servers\.codexLocalOps\]\s*\r?\n.*?(?=^\[|\z)', '').TrimEnd()
    Set-Content -LiteralPath $configPath -Value ($text + [Environment]::NewLine) -Encoding utf8
}
if (Test-Path -LiteralPath $VenvRoot) { Remove-Item -LiteralPath $VenvRoot -Recurse -Force }
if ($RemoveConfig -and (Test-Path -LiteralPath $InstallRoot)) { Remove-Item -LiteralPath $InstallRoot -Recurse -Force }
Write-Host 'Codex Local Ops MCP registration and runtime removed. Configuration was preserved unless -RemoveConfig was supplied.'
