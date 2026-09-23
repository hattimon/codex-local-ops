# Windows

Windows is the primary supported platform for the first public beta.

## Validated state

The project has been validated on a real interactive Windows session for:

- core STDIO MCP registration and read-only platform discovery;
- async/session jobs, including start, running state, completion, output,
  cancellation, and cleanup;
- Windows OpenSSH agent integration;
- WSL discovery and read-only execution;
- Docker daemon/version/container inspection;
- Playwright browser lifecycle/navigation/snapshot/screenshot flows;
- FFmpeg/ffprobe media inspection and async frame extraction;
- native desktop backend and window enumeration;
- `pywinauto`/pywin32 desktop integration;
- Codex Local Ops Manager and completed First Run;
- a real nonblank PNG capture of the Manager window.

OBS was intentionally skipped because OBS/WebSocket was not running. Interactive
screen-recording capture remains a separate pending smoke test.

## Install

Use the repository's installer:

```powershell
.\setup.ps1
```

For the validated desktop/browser extras:

```powershell
$LocalOpsPython = Join-Path $env:USERPROFILE '.codex-local-ops.venv\Scripts\python.exe'
& $LocalOpsPython -m pip install --upgrade ".[desktop,browser,windows]"
& $LocalOpsPython -m playwright install chromium
```

Install FFmpeg separately and keep `ffmpeg`/`ffprobe` on `PATH` for media tools.

## Interactive validation

Desktop tests must run from a normal interactive Windows session, not from a
headless or restricted Web/Native harness.

The project includes:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\validate_interactive_windows.ps1
```

The validator checks the actual interpreter/venv, optional desktop imports,
desktop backend, window enumeration, Manager GUI/First Run state, and a safe
window screenshot. Generated validation files are written under `artifacts/`
and are ignored by Git.

If you use a custom Local Ops venv, pass its interpreter with `-PythonPath`.

## Web/Native sandbox limitations

Process-launch or capture failures such as `Access denied` observed only inside
a restricted bridge do not demonstrate that the installed Python environment or
desktop backend is broken. Reproduce desktop/capture problems in a normal user
session before repairing or reinstalling Local Ops.

## OBS

OBS is optional. To test it deliberately:

1. Start OBS.
2. Enable OBS WebSocket.
3. Configure authentication in the Local Ops secret/config path.
4. Call `obs_status` before any recording or streaming action.

Do not treat an absent OBS WebSocket server as a project failure.
