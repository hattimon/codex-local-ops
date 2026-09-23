param(
    [string]$PythonPath = (Join-Path $HOME ".codex-local-ops.venv\Scripts\python.exe")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$validator = Join-Path $PSScriptRoot "validate_interactive_windows.py"

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Local Ops Python was not found: $PythonPath"
}

if (-not (Test-Path -LiteralPath $validator -PathType Leaf)) {
    throw "Interactive validator was not found: $validator"
}

Write-Host "Codex Local Ops interactive Windows validation"
Write-Host "Python:  $PythonPath"
Write-Host "Project: $projectRoot"
Write-Host ""

& $PythonPath $validator --project-root $projectRoot
exit $LASTEXITCODE
