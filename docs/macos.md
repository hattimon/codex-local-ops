# macOS

The macOS backend is implemented, but the project does not yet claim full native
interactive validation on a physical Mac.

## Install

```sh
chmod +x setup.sh
./setup.sh
```

Optional Python features can be installed into the Local Ops venv:

```sh
~/.codex-local-ops/.venv/bin/python -m pip install --upgrade ".[desktop,browser,obs]"
~/.codex-local-ops/.venv/bin/python -m playwright install chromium
```

## Implemented platform paths

- system information and shell detection;
- launchd-oriented service inspection/actions where a valid target is supplied;
- Finder/open integration;
- SSH-agent status;
- window inventory through `osascript`/System Events;
- focus and supported window actions using the `pid:index` handles returned by
  the backend;
- screenshots through `screencapture`.

## Permissions

Desktop behavior can require permissions in **System Settings > Privacy &
Security**:

- Accessibility for UI/window actions;
- Automation for System Events control;
- Screen Recording for screenshots/capture.

CI cannot grant or fully emulate these interactive user permissions.

## Validation status

GitHub-hosted `macos-latest` runners validate installation, compilation, unit
tests, STDIO MCP startup/smoke, platform detection, and package builds. This is
useful release coverage, but it does not replace final native interactive testing
on a real user Mac session.
