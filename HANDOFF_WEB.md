# Project handoff

Codex Local Ops is in public prerelease hardening. Continue from the existing
repository state; do not bootstrap or reinstall the working MCP as part of
normal development.

## Validated state

- Windows is the primary supported and interactively validated platform.
- The STDIO MCP server, trusted-root checks, async/session jobs, browser
  automation, FFmpeg/media, SSH agent integration, WSL, Docker, the Manager GUI,
  Windows desktop automation, and a real Manager-window PNG screenshot have
  passed validation.
- OBS support is implemented but its live WebSocket connection is currently
  recorded as skipped because OBS was not intentionally running.
- Interactive Windows screen-recording capture still needs a successful native
  smoke test.
- Linux is implemented as a secondary target with broader native validation in
  progress.
- macOS backend paths are implemented and suitable for headless CI validation;
  final interactive validation on a physical Mac is still pending.

Earlier process-launch, Access denied, and desktop-capture failures observed
only through a restricted Web/Native harness are treated as sandbox-boundary
limitations. They are not evidence of a broken Python installation, virtual
environment, or Windows desktop backend.

## Runtime conventions

The normal Windows installation uses a dedicated user-level Local Ops
environment and configuration rather than a machine-specific hardcoded path.
The setup scripts discover or create these locations and register
`codexLocalOps` in the Codex MCP configuration.

Long-running work should use `job_start` / `run_script_async` and then poll
with `job_status` / `job_output`. Cancellation and cleanup have been
validated.

## Publication status

The repository is being prepared for the first public prerelease:
`v0.1.0-beta.1` (Python package version `0.1.0b1`).

Before publication, require:

- Windows/Linux/macOS headless CI;
- compile and non-interactive unit tests;
- STDIO MCP smoke and platform detection;
- wheel/sdist build and metadata checks;
- README/document link validation;
- repository privacy/security scan;
- no generated validation artifacts, credentials, browser profiles, local
  configuration, logs, or machine-specific paths in the public tree.

See `README.md`, `README_PL.md`, `IMPLEMENTATION_STATE.json`, and
`docs/` for public project status.
