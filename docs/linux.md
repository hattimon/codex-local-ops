# Linux

Linux is a secondary target for the first public beta. The backend is
implemented and covered by unit/headless CI, while broader native-machine
validation is still in progress.

## Install

```sh
chmod +x setup.sh
./setup.sh
```

Optional features can be installed into the Local Ops venv:

```sh
~/.codex-local-ops/.venv/bin/python -m pip install --upgrade ".[desktop,browser,obs]"
~/.codex-local-ops/.venv/bin/python -m playwright install chromium
```

## Headless/core use

The Linux backend supports normal headless workflows such as system/file
operations, shell, Git, SSH, Docker, services, deployments, health checks, and
async jobs without requiring a desktop session.

## X11 and Wayland

For X11 desktop window operations, install the system tools used by the backend
when required, including `wmctrl` and `xdotool`.

Wayland deliberately does not claim unrestricted global window/input control.
When the compositor or portal does not expose the required capability, Local Ops
returns `CAPABILITY_UNAVAILABLE` with guidance. Screenshot behavior depends on
the desktop environment and available portal/screenshot implementation.

## Validation status

GitHub Actions validates Linux compile, unit tests, STDIO MCP startup/smoke,
platform detection, and wheel/sdist build paths. Interactive X11/Wayland testing
on representative native machines remains part of prerelease hardening.
