# Troubleshooting

## Start with diagnostics

Run:

```text
clops diagnostics
codex mcp list
```

Then use the read-only `platform_info` tool through `codexLocalOps` if the MCP
registration is visible.

## MCP server is not listed

Rerun the platform setup script from the repository root. Both installers back
up the existing Codex configuration before replacing the Local Ops MCP section.
Do not delete the whole Codex config to repair one MCP entry.

Windows:

```powershell
.\setup.ps1
```

Linux/macOS:

```sh
./setup.sh
```

## Optional capability is unavailable

`CAPABILITY_UNAVAILABLE` is expected when an optional dependency, system binary,
desktop session, permission, or external service is missing. Follow the
`suggested_setup` field when present instead of reinstalling the entire project.

Typical examples:

- browser package present but no Playwright browser binary: run
  `python -m playwright install chromium` using the Local Ops venv Python;
- desktop Python extras absent: install the platform's documented extras into
  the Local Ops venv;
- FFmpeg tools unavailable: install FFmpeg and expose `ffmpeg`/`ffprobe` on
  `PATH`;
- OBS unavailable: start OBS, enable WebSocket, and configure authentication if
  you intend to use the integration.

## SSH command is denied

SSH `PERMISSION_DENIED` results include structured fields such as `host_profile`,
`policy_mode`, `command`, a safely derived `path`, `blocking_rule`, and the
runtime `config` path. Use `blocking_rule` to identify the policy that rejected
the request. SSH permissions inherit as `READ_ONLY ⊂ OPERATIONS ⊂ FULL`. A
`FULL` host can use bounded `OPERATIONS` commands without expert mode; expert
mode is needed only for commands outside that bounded policy.

## Long operation times out through a bridge

Use the async/session API instead of one long synchronous MCP call:

```text
job_start / run_script_async
job_status
job_output
job_cancel
job_list
```

Start should return a `session_id` quickly. Poll status/output with short calls.

`local_shell_run` has a 180-second synchronous safety budget. Known long-running
test/build/media commands are routed to persistent async jobs automatically. If
the Web/bridge response disappears, resume by querying the same `session_id`;
do not launch the test/build again. A short command that exceeds its effective
timeout returns `TIMED_OUT` after Local Ops terminates its process tree and
collects bounded stdout/stderr.

## Windows desktop works manually but fails in Web/Native harness

Restricted harnesses may be unable to access the user's interactive desktop,
launch local GUI processes, or use a capture backend. Treat errors seen only in
that environment as harness limitations until they reproduce in a normal
interactive Windows session.

Run `scripts/validate_interactive_windows.ps1` from normal PowerShell before
repairing Python, the venv, or desktop dependencies.

## Linux desktop

Check `XDG_SESSION_TYPE`, `DISPLAY`, and `WAYLAND_DISPLAY`. X11 desktop actions
can require `wmctrl`/`xdotool`. Wayland may intentionally return capability
limits for global window/input operations.

## macOS desktop

Check Accessibility, Automation, and Screen Recording permissions in System
Settings > Privacy & Security. CI success does not imply that an interactive Mac
has granted those permissions.

## Reporting an issue

Use the GitHub bug template and include:

- OS and version;
- Python version;
- Codex Local Ops version;
- the exact command/tool that failed;
- sanitized diagnostics and error text;
- whether the issue reproduces outside a sandbox/headless runner.

Do not attach credentials, SSH private keys, cookies, browser profiles, private
configuration, or unreviewed full logs.
