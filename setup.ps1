[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $env:USERPROFILE '.codex-local-ops'),
    [string]$VenvRoot = (Join-Path $env:USERPROFILE '.codex-local-ops.venv'),
    [string]$PythonExe = '',
    [string]$TrustedRoot = '',
    [switch]$SkipDiagnostics
)

$ErrorActionPreference = 'Stop'
$SourceRoot = $PSScriptRoot

function Find-Python311OrNewer {
    $candidates = @()
    try {
        $candidates += (& py -0p 2>$null | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '\.exe$' })
    } catch {}
    foreach ($name in @('python.exe', 'python3.exe')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $candidates += $command.Source }
    }
    $registryRoots = @(
        'HKCU:\Software\Python\PythonCore',
        'HKLM:\Software\Python\PythonCore',
        'HKLM:\Software\WOW6432Node\Python\PythonCore'
    )
    foreach ($registryRoot in $registryRoots) {
        Get-ChildItem -Path $registryRoot -ErrorAction SilentlyContinue | ForEach-Object {
            $install = Get-ItemProperty -Path "$($_.PSPath)\InstallPath" -ErrorAction SilentlyContinue
            if ($install.'(default)') { $candidates += (Join-Path $install.'(default)' 'python.exe') }
        }
    }
    $candidates += @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
        'C:\Program Files\Python313\python.exe',
        'C:\Program Files\Python312\python.exe',
        'C:\Program Files\Python311\python.exe'
    )
    foreach ($candidate in ($candidates | Select-Object -Unique)) {
        if (-not (Test-Path -LiteralPath $candidate)) { continue }
        # Local Ops must never inherit an interpreter supplied by an AI/model application.
        if ($candidate.ToLowerInvariant() -match '(hermes|ollama|local-ai|llama|inference)') { continue }
        try {
            $version = & $candidate -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ([version]$version -ge [version]'3.11') { return (Resolve-Path -LiteralPath $candidate).Path }
        } catch {}
    }
    throw 'No independent CPython 3.11+ was found. Install official CPython (for example: winget install Python.Python.3.12) and rerun setup.ps1.'
}

function Assert-IndependentPython([string]$Candidate) {
    $resolved = (Resolve-Path -LiteralPath $Candidate -ErrorAction Stop).Path
    if ($resolved.ToLowerInvariant() -match '(hermes|ollama|local-ai|llama|inference)') {
        throw "Refusing AI-application Python interpreter: $resolved"
    }
    $version = & $resolved -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    if ([version]$version -lt [version]'3.11') { throw "Python 3.11+ is required: $resolved" }
    return $resolved
}

function Register-CodexMcp([string]$PythonExe) {
    $codexDir = Join-Path $env:USERPROFILE '.codex'
    New-Item -ItemType Directory -Force -Path $codexDir | Out-Null
    $configPath = Join-Path $codexDir 'config.toml'
    if (-not (Test-Path -LiteralPath $configPath)) { New-Item -ItemType File -Path $configPath | Out-Null }
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backup = "$configPath.backup-$stamp"
    Copy-Item -LiteralPath $configPath -Destination $backup -Force
    $text = Get-Content -LiteralPath $configPath -Raw
    $pattern = '(?ms)^\[mcp_servers\.codexLocalOps\]\s*\r?\n.*?(?=^\[|\z)'
    $text = [regex]::Replace($text, $pattern, '').TrimEnd()
    $tomlPath = $PythonExe.Replace('\', '\\')
    $entry = @"

[mcp_servers.codexLocalOps]
command = "$tomlPath"
args = ["-m", "codex_local_ops.server"]
"@
    Set-Content -LiteralPath $configPath -Value ($text + $entry + [Environment]::NewLine) -Encoding utf8
    return @{ ConfigPath = $configPath; BackupPath = $backup }
}

$Python = if ($PythonExe) { Assert-IndependentPython $PythonExe } else { Find-Python311OrNewer }
New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
$VenvPython = Join-Path $VenvRoot 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $Python -m venv $VenvRoot
}
& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install --upgrade $SourceRoot
& $VenvPython -c "from codex_local_ops.config import ensure_config; print(ensure_config())"

# The generated config is deliberately explicit and scoped to the user's project root.
$ConfigFile = Join-Path $InstallRoot 'config\config.yaml'
if ($TrustedRoot -and (Test-Path -LiteralPath $TrustedRoot)) {
    $cfg = Get-Content -LiteralPath $ConfigFile -Raw
    if ($cfg -notmatch [regex]::Escape($TrustedRoot)) {
        $cfg = $cfg -replace 'trusted_roots: \[\]', "trusted_roots:`r`n  - '$TrustedRoot'"
        Set-Content -LiteralPath $ConfigFile -Value $cfg -Encoding utf8
    }
}

$registration = Register-CodexMcp $VenvPython
Write-Host "Codex Local Ops installed at $InstallRoot"
Write-Host "Python: $VenvPython"
Write-Host "Codex config backup: $($registration.BackupPath)"
if (-not $SkipDiagnostics) {
    & $VenvPython -m codex_local_ops.cli diagnostics
    & codex mcp list
}
