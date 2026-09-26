# ChatGPT Web setup on Windows

This guide connects a healthy Codex Local Ops installation to the current
`miuuyy/codex-chatgpt-web` launcher and the ChatGPT Full Harness.

## Audited upstream baseline

The integration was audited against `codex-chatgpt-web` **v6.1.1**, published
2026-09-26. The current Windows launcher flow uses the official release asset
and the official `install-launcher.ps1` script. That installer downloads the
Windows EXE plus `checksums.txt`, verifies SHA-256, installs per user, and keeps
the existing launcher settings and ChatGPT profile during update.

Official command documented upstream:

```powershell
irm https://github.com/miuuyy/codex-chatgpt-web/releases/latest/download/install-launcher.ps1 | iex
```

Quit **Codex Web GPT** before an update. A healthy launcher is updated in place;
do not uninstall it first and do not delete its ChatGPT profile.

## Guided setup

1. Launch **Codex Web GPT**.
2. Sign in to ChatGPT inside the launcher's embedded browser. Codex Local Ops
   never asks for, reads, or stores ChatGPT credentials, cookies, browser
   storage, authentication headers, or profile files.
3. Run the launcher's browser smoke test.
4. Choose **Install models** / **Install into Codex**.
5. Fully quit Codex, including background processes, then reopen it while the
   launcher is running. Wait until the launcher reports the Codex catalog as
   verified.
6. Open **MCP** in the launcher and create the required OpenAI tunnel and the
   regular API key used by the tunnel.
7. Choose **Connect harness**.
8. In ChatGPT, enable **Developer Mode**.
9. Create a **new** connector named exactly **Codex Native2**.
10. Select the same Tunnel, set **Authentication: None**, open Permissions, and
    choose **Allow all actions**.
11. Return to the launcher and run **Verify runtime**.
12. Perform one read-only end-to-end proof through Native2 that returns Windows
    host evidence through `codexLocalOps`.

Do not rename or reuse an old `Codex Native` connector. The current upstream
troubleshooting guide requires a new connector identity so ChatGPT loads the
current MCP contract.

## Setup Assistant status

```powershell
clops setup-assistant web-status
```

The status is split into independent checks:

- `CODEX_WEB_GPT`
- `CHATGPT_LOGIN`
- `BROWSER_SMOKE_TEST`
- `WEB_MODELS`
- `FULL_HARNESS`
- `CODEX_NATIVE2`
- `VERIFY_RUNTIME`
- `FULL_HARNESS_TO_CODEX`
- `CODEX_TO_CODEXLOCALOPS`
- `WINDOWS_HOST_VISIBLE_THROUGH_LOCALOPS`

The final state is `READY` only after every required hop is actually verified.
Manual/account steps stay `WAITING_FOR_USER`; they are never guessed as `PASS`.

The launcher state reader uses only non-secret setup fields from
`launcher-state.json`, including browser smoke, model/catalog, restart, and MCP
verification flags. Unknown keys are ignored, so cookies or tokens are not
copied into Local Ops state or output.

## Install or update plan

```powershell
clops setup-assistant web-plan
```

The plan reports `INSTALL`, `UPDATE`, or `NONE` and points to the official
installer. It does not silently run a downloaded binary. Update planning keeps
the user's launcher configuration and ChatGPT profile and does not prescribe a
healthy-launcher uninstall/reinstall cycle.

## Repair mode

```powershell
clops setup-assistant web-repair
```

Web repair findings are kept separate from Local Ops runtime health. Examples:

- launcher missing or version not proven current;
- browser smoke not verified or stale after an update;
- models installed but Codex restart/catalog verification incomplete;
- Full Harness incomplete;
- `Codex Native2` / Verify Runtime incomplete;
- end-to-end Web → Windows proof still missing.

A broken Web route does not trigger Local Ops reinstall. If `codexLocalOps` is
healthy, it stays healthy in the report even when Web integration is incomplete.

## Web failures and authorization

`stream disconnected`, transient transport failure, and a failed Web turn do
not prove that Windows, Python, Docker, SSH, or Local Ops is broken.

If Web/Native2 cannot execute the known development interpreter, use the exact
host path:

```text
%USERPROFILE%\.codex-local-ops.venv\Scripts\python.exe
```

Do not put a nested `.venv` under `%USERPROFILE%\.codex-local-ops`; the dev venv
is the sibling directory `.codex-local-ops.venv`. Do not search for another
Python to work around a harness limitation.

If a Local Ops operation returns `AUTHORIZATION_REQUIRED`,
`PERMISSION_REQUIRED`, `APPROVAL_REQUIRED`, `SECURITY_DENIED`, or
`POLICY_DENIED`, that side effect stops as `WAITING_FOR_USER`. Do not retry the
same effect through Codex exec, PowerShell, cmd, Python, WSL, Docker, SSH,
browser automation, or another MCP.

## Diagnostics upstream

For launcher-specific failures use **Settings → Run doctor**, then reproduce the
problem once and use **Activity → Export safe log**. Do not export cookies,
browser storage, authentication headers, or the raw launcher profile.

For `Reconnecting`, `stream disconnected`, or `ChatGPT failed`, use the final
detailed error. A generic reconnect or 502 alone does not identify the failing
hop.
