English | [Polski](README_PL.md)

# Codex Local Ops

Codex Local Ops is a local STDIO MCP server that gives Codex controlled access
to developer operations on the machine where it is installed. It combines
project-scoped file and process operations with platform-aware integrations for
SSH, WSL, Docker, browsers, desktop UI automation, media processing, and
optional OBS control.

The project is preparing its first public prerelease, `v0.1.0-beta.1`. Windows
is the primary platform and has been validated in a real interactive Windows
session. Linux and macOS backends are implemented, but their native desktop
validation is still in progress.

## Platform status

| Platform | Status | Scope |
| --- | --- | --- |
| Windows | **Supported / validated** | Primary target. Core MCP, async jobs, browser, FFmpeg/media, WSL, Docker, SSH, GUI Manager, native window automation, and screenshots have been validated. Interactive screen-recording smoke remains pending. |
| Linux | **Implemented / native validation in progress** | Core operations and Linux platform backend are implemented. X11 desktop support is available when the required system tools exist; Wayland intentionally reports capability limits for unrestricted global control. CI covers headless/unit/package paths. |
| macOS | **Implemented / CI and native validation in progress** | Platform backend, window enumeration/actions, screenshots, and system integration paths are implemented. GitHub-hosted macOS CI covers non-interactive validation. A physical interactive Mac has not yet been used for final native desktop validation. |

## Main capabilities

- Local system information, health checks, files, processes, and controlled shell execution.
- Trusted project roots and explicit execution/control permission profiles.
- Persisted async jobs for commands that should not block one MCP request.
- SSH agent inspection, imported SSH host profiles, and per-host permission levels.
- WSL discovery and execution on Windows.
- Docker and Compose-oriented inspection/operations.
- Git operations used by local development workflows.
- Playwright-based browser automation, screenshots, downloads, console/network diagnostics, and recording support.
- Native desktop window inventory/control and screenshots when the platform backend allows it.
- FFmpeg/ffprobe inspection and media conversion/editing helpers, including async variants for longer work.
- Optional OBS WebSocket scene/source/recording/streaming/status tools.
- Optional animation adapters for installed tools such as Remotion, Manim, or Blender.

Optional capabilities fail explicitly with structured `CAPABILITY_UNAVAILABLE`
results when their dependency, desktop session, permission, or external service
is missing.

## Architecture overview

```text
Codex / MCP client
       |
       | STDIO
       v
codex_local_ops.server
       |
       +-- config + trusted roots + permission checks
       +-- audit/redaction + bounded process execution
       +-- async session-job store
       |
       +-- cross-platform operations
       |     files / shell / Git / SSH / Docker
       |
       +-- platform backend
       |     Windows / Linux / macOS
       |
       +-- optional adapters
             browser / desktop / media / OBS / animation
```

The MCP server is local. Long-running commands can be detached into persisted
session jobs so Web/bridge clients can start work quickly and poll it with short
requests.

## Security model

Codex Local Ops is an operations bridge, not an operating-system sandbox. The
security model narrows what the bridge accepts and makes higher-impact behavior
explicit.

### Trusted roots

Project file operations are scoped to configured trusted roots. Path checks
resolve paths and reject traversal or symlink escapes outside the allowed
project roots. Configure trusted roots during First Run with `clops manager` or
`clops wizard`.

### Permission profiles

Local execution uses `permissions.local_profile`:

- `SAFE` for the most restricted local workflow.
- `DEVELOPER` for normal development operations.
- `FULL` for intentionally broad local execution.

Browser/desktop input has a separate `computer_control.mode`: `OFF`, `SAFE`,
`INTERACTIVE`, or `FULL`.

SSH host profiles use `READ_ONLY`, `OPERATIONS`, or `FULL`. The operations
profile is limited to bounded operational commands; the broadest SSH behavior
also depends on expert-mode policy.

### Git and GitHub boundary

Git/GitHub integration intentionally has two levels. MCP exposes inspection
only: `git_status`, `git_diff`, `git_log`, `git_branch_list`,
`git_remote_list`, `github_auth_status`, `github_repo_view`,
`github_workflow_list`, `github_workflow_runs`, `github_workflow_run_view`,
`github_release_list`, `github_release_view`, `github_pr_list`, and
`github_pr_view`.

Repository and GitHub writes are user-initiated local CLI operations. Examples:

```text
clops git status --path <repo>
clops git commit --path <repo> -m "message" --add <pathspec>
clops git push --path <repo> --remote origin [--branch <branch>] [--async]
clops git publish --path <repo> --remote origin [--branch <branch>]
clops github auth-status
clops github workflow-status --path <repo>
clops github release-create --path <repo> --tag <existing-tag>
```

Local write commands are restricted to trusted repositories, require the local
write permission profile (`DEVELOPER`/`FULL`, or the existing expert-mode
override), and are audited with secret redaction. The narrow push API does not
accept force push/refspecs. GitHub child processes remove `GH_TOKEN` and
`GITHUB_TOKEN` from their child environment so host `gh auth` credentials can
be used without changing the user's global environment or exposing tokens in
normal output. This split is a deliberate security boundary, not a missing
feature.

Command output is bounded and common secret patterns are redacted before normal
tool exposure. OBS credentials are kept in the configured secret store and are
not returned by status calls. Public OBS streaming requires an explicit opt-in
in configuration plus confirmation at the call boundary.

See [SECURITY.md](SECURITY.md) and
[docs/troubleshooting.md](docs/troubleshooting.md).

## Async jobs

Use async/session jobs for commands that could outlive a normal MCP request:

- `job_start`
- `run_script_async`
- `job_status`
- `job_output`
- `job_cancel`
- `job_list`

`job_start` and `run_script_async` return a `session_id` without waiting for
the child process to finish. Job state and redacted output are persisted, and
registered temporary paths are cleaned after completion or cancellation.

Typical lifecycle:

```text
job_start -> STARTED + session_id
job_status -> RUNNING
job_status -> COMPLETED | FAILED | CANCELLED | FINISHED_EXIT_UNKNOWN
job_output -> bounded stdout/stderr
```

## SSH, WSL, and Docker

SSH support can use the operating system's SSH agent and imported host profiles
without reading private key material into normal MCP output. Permission policy
is applied per configured host.

On Windows, WSL tools provide distribution discovery, path translation, and
controlled command execution in installed distributions.

Docker tools expose daemon/version/container information and bounded operational
actions when Docker is available. Docker is optional; unavailable daemons are
reported as capability/runtime state instead of breaking unrelated Local Ops
features.

## Browser automation

Browser automation uses Playwright. Implemented operations include browser
lifecycle, tabs/navigation, snapshots and text lookup, click/fill/type/press,
select/scroll, uploads/downloads, screenshots, console/network diagnostics, and
recording helpers. Browser binaries are an explicit Playwright installation
step; see [Windows setup](docs/windows.md) and
[installation](docs/installation.md).

## Desktop automation

Windows desktop automation uses `pywinauto`; the validated Windows environment
also uses `mss`, Pillow, and pywin32 support installed with the relevant extras.

Linux supports X11 window operations through system tools such as `wmctrl` and
`xdotool` when present. Generic unrestricted global automation is intentionally
reported as unavailable on Wayland where the compositor does not expose it.

macOS uses `osascript`/System Events for implemented window operations and
`screencapture` for screenshots. Accessibility, Automation, and Screen
Recording permissions may be required. Final native interactive validation is
still pending.

## FFmpeg and media

FFmpeg/ffprobe helpers cover media inspection, conversion, trimming, concat,
resize/FPS changes, frame/audio extraction, audio/subtitle composition, and
GIF/video conversion. Longer transforms have async variants backed by the same
session-job system. FFmpeg itself is a system dependency and is not bundled.

Screen recording support is implemented. The Windows interactive recording
smoke is still one of the remaining prerelease validations.

## OBS (optional)

OBS integration uses OBS WebSocket and is optional. It includes short-timeout
status calls plus scene/source/recording/streaming/stat operations. The project
does not require a running OBS instance for its normal test suite. Current
Windows validation records OBS as skipped because OBS/WebSocket was not running;
that is not treated as a Local Ops failure.

## Installation

Python 3.11 or newer is required. The installer deliberately rejects Python
interpreters supplied by local-model runtimes and creates a dedicated user-level
environment for Codex Local Ops.

### Windows — primary path

From the repository root in PowerShell:

```powershell
.\setup.ps1
```

`setup.ps1` creates or reuses the dedicated Local Ops virtual environment,
installs the current source, creates the Local Ops configuration, backs up the
existing Codex `config.toml`, and registers the `codexLocalOps` STDIO server.

For the validated Windows desktop/browser feature set, install the optional
extras into the same Local Ops venv from the repository root:

```powershell
$LocalOpsPython = Join-Path $env:USERPROFILE '.codex-local-ops.venv\Scripts\python.exe'
& $LocalOpsPython -m pip install --upgrade ".[desktop,browser,windows]"
& $LocalOpsPython -m playwright install chromium
```

OBS support is optional:

```powershell
& $LocalOpsPython -m pip install --upgrade ".[obs]"
```

Then run First Run:

```powershell
clops manager
# or
clops wizard
```

See [docs/installation.md](docs/installation.md) and
[docs/windows.md](docs/windows.md) for parameters and validation steps.

### Linux and macOS

From the repository root:

```sh
./setup.sh
```

Linux is secondary and still needs broader native validation. macOS is not yet
fully validated on a physical interactive Mac. See
[docs/linux.md](docs/linux.md) and [docs/macos.md](docs/macos.md) before enabling
desktop-specific capabilities.

## Updating

Update the source checkout, review the changelog, then rerun the platform setup
script. The installer updates the package in the existing dedicated environment
and refreshes the MCP registration while backing up the Codex configuration.

Windows:

```powershell
git pull
.\setup.ps1
```

Linux/macOS:

```sh
git pull
./setup.sh
```

If you use optional extras, update those extras in the same venv after updating
the core package.

## Uninstall

Windows:

```powershell
.\uninstall.ps1
```

The default preserves Local Ops configuration. Use `-RemoveConfig` only when
you intentionally want to remove it as well.

Linux/macOS:

```sh
./uninstall.sh
```

Pass `--remove-config` to remove the Local Ops configuration directory too.

## Troubleshooting

- [General troubleshooting](docs/troubleshooting.md)
- [Windows](docs/windows.md)
- [Linux](docs/linux.md)
- [macOS](docs/macos.md)
- [Security policy](SECURITY.md)

Failures observed only inside a Web/Native sandbox, such as restricted process
launching or desktop capture, do not prove the normal interactive desktop
backend is broken. Reproduce desktop issues from a normal user session before
changing the installed environment.

## Development

Create a Python 3.11+ development environment and install:

```sh
python -m pip install -e ".[dev,desktop,browser,obs,windows]"
```

The `windows` extra is guarded by a platform marker and is harmless on Linux or
macOS. Contributions should preserve structured capability failures for optional
features and must not commit local paths, credentials, browser profiles, logs,
or generated validation artifacts.

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Testing

Core local validation:

```sh
python -m compileall -q src tests scripts
python -m pytest -m "not interactive"
python scripts/mcp_smoke.py
```

Interactive desktop validation is intentionally separate from headless CI. On
Windows, `scripts/validate_interactive_windows.ps1` exercises the real desktop
session and stores ignored local artifacts under `artifacts/`.

GitHub Actions runs compile, unit tests, STDIO MCP smoke, platform detection,
and package build validation on `windows-latest`, `ubuntu-latest`, and
`macos-latest`. This does not replace real interactive desktop validation.

## Versioning and releases

The project uses SemVer for Git tags/releases. The planned first public tag is
`v0.1.0-beta.1`; its Python package version is `0.1.0b1` (PEP 440). Release
workflows build and validate artifacts only for explicit `v*` tag pushes and do
not publish packages or create a GitHub Release automatically.

See [CHANGELOG.md](CHANGELOG.md) and
[docs/release-notes-template.md](docs/release-notes-template.md).

## License

Codex Local Ops is released under the [MIT License](LICENSE).
