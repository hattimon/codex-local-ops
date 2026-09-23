[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:USERPROFILE '.codex-local-ops'),
    [string]$VenvRoot = (Join-Path $env:USERPROFILE '.codex-local-ops.venv'),
    [string]$PythonExe = '',
    [string]$TrustedRoot = ''
)

$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'setup.ps1') -InstallRoot $InstallRoot -VenvRoot $VenvRoot -PythonExe $PythonExe -TrustedRoot $TrustedRoot
