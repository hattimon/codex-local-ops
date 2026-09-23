# Installation

English | [Polski](installation-pl.md)

Windows is the primary and validated installation target. Linux is a secondary
implemented target with native validation in progress. macOS support is
implemented and exercised in non-interactive CI, but final validation on a
physical interactive Mac is still pending.

## Requirements

- CPython 3.11 or newer.
- Codex installed and able to read its user `config.toml`.
- A local checkout of this repository.
- Optional feature dependencies only when you need those features.

The setup scripts reject Python interpreters whose path indicates a local-model
runtime. Codex Local Ops should use its own normal CPython environment.

## Windows — primary path

Open a normal Windows PowerShell in the repository root and run:

```powershell
.\setup.ps1
```

By default the script:

1. Finds an independent CPython 3.11+ interpreter.
2. Creates or reuses `%USERPROFILE%\.codex-local-ops.venv`.
3. Installs the current repository into that venv.
4. Creates the Local Ops configuration under `%USERPROFILE%\.codex-local-ops`.
5. Backs up `%USERPROFILE%\.codex\config.toml`.
6. Replaces only the `mcp_servers.codexLocalOps` registration.
7. Runs diagnostics and `codex mcp list` unless `-SkipDiagnostics` is supplied.

Useful parameters:

```powershell
.\setup.ps1 -PythonExe "C:\Path\To\python.exe"
.\setup.ps1 -TrustedRoot "C:\Projects"
.\setup.ps1 -InstallRoot "C:\LocalOps" -VenvRoot "C:\LocalOpsVenv"
.\setup.ps1 -SkipDiagnostics
```

The default setup installs the core package. Install optional Windows features
into the same Local Ops venv from the repository root:

```powershell
$LocalOpsPython = Join-Path $env:USERPROFILE '.codex-local-ops.venv\Scripts\python.exe'
& $LocalOpsPython -m pip install --upgrade ".[desktop,browser,windows]"
& $LocalOpsPython -m playwright install chromium
```

Optional OBS integration:

```powershell
& $LocalOpsPython -m pip install --upgrade ".[obs]"
```

FFmpeg is a system dependency. Install FFmpeg separately and make sure
`ffmpeg` and `ffprobe` are available on `PATH` before using media tools.

If PowerShell execution policy blocks the script, you can run the setup once
for the current process without changing the machine-wide policy:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1
```

After setup, run First Run:

```powershell
clops manager
```

or:

```powershell
clops wizard
```

Configure at least one trusted root. See [windows.md](windows.md) for the
validated desktop/browser path and interactive validation.

## Linux

From the repository root:

```sh
chmod +x setup.sh
./setup.sh
```

The script creates a user-level venv under the Local Ops home, installs the
current source, creates the configuration, backs up `~/.codex/config.toml`, and
registers the STDIO MCP server.

Optional Python features can be added from the repository root with the Local
Ops venv interpreter, for example:

```sh
~/.codex-local-ops/.venv/bin/python -m pip install --upgrade ".[desktop,browser,obs]"
~/.codex-local-ops/.venv/bin/python -m playwright install chromium
```

Desktop behavior depends on the actual Linux desktop session. X11 automation
may require tools such as `wmctrl` and `xdotool`. Generic global control on
Wayland is intentionally limited. See [linux.md](linux.md).

## macOS

The installer entry point is also:

```sh
chmod +x setup.sh
./setup.sh
```

Optional Python features can be installed into the Local Ops venv in the same
way as on Linux. Desktop features may require Accessibility, Automation, and
Screen Recording permissions in System Settings.

macOS is not yet classified as fully native-validated. GitHub-hosted macOS CI
covers compile, unit, STDIO MCP, platform detection, and package-build paths.
See [macos.md](macos.md).

## First Run and configuration

Use either:

```text
clops manager
clops wizard
```

The Manager/wizard configures trusted roots, local execution profile,
computer-control mode, SSH import and host permissions, and optional feature
state. Headless systems should use the terminal wizard or edit the generated
YAML deliberately.

Browser channels are configured under `browser.channel`. Supported values are
those accepted by the installed Playwright version, for example `chromium`,
`chrome`, or `msedge` when the corresponding browser is available.

## Verify the MCP registration

Run:

```text
codex mcp list
```

Then use the read-only `platform_info` tool through the registered
`codexLocalOps` server. On Windows, the project also ships an interactive
desktop validator described in [windows.md](windows.md).

## Update

Update the source checkout, review [../CHANGELOG.md](../CHANGELOG.md), then rerun
the platform setup script:

```powershell
# Windows
git pull
.\setup.ps1
```

```sh
# Linux/macOS
git pull
./setup.sh
```

If you installed optional extras, update those extras in the same Local Ops venv
after updating the core package.

## Uninstall

Windows:

```powershell
.\uninstall.ps1
```

Use `-RemoveConfig` only when you also want to delete Local Ops configuration.

Linux/macOS:

```sh
./uninstall.sh
```

Pass `--remove-config` to delete the Local Ops configuration directory as well.
Both uninstallers remove the MCP registration while preserving configuration by
default.

## Troubleshooting

See [troubleshooting.md](troubleshooting.md). Platform-specific guidance is in
[windows.md](windows.md), [linux.md](linux.md), and [macos.md](macos.md).
