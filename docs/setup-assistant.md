# Setup Assistant

Setup Assistant is the lifecycle and connection layer above the existing Codex
Local Ops runtime. It keeps stable runtime health, Codex registration, trusted
roots, managed instructions, and ChatGPT Web integration as separate checks.

## Status model

Setup steps use:

- `PASS` — directly verified;
- `WARNING` — usable evidence exists but attention is recommended;
- `FAIL` — a verified failure blocks the step;
- `NOT_CONFIGURED` — the component is absent;
- `WAITING_FOR_USER` — an account/UI choice, authorization, or manual proof is
  still required.

`READY` is used only for the complete Web-to-Windows chain after every required
hop is `PASS`.

## Local and Web health are independent

The local path is:

```text
Codex → codexLocalOps → Windows host
```

The Web path adds:

```text
ChatGPT Web → Codex Native2 → Full Harness → Codex
```

Web breakage never means Local Ops must be reinstalled. Setup Assistant reports
the failing layer and keeps a healthy stable runtime untouched.

## Commands

```powershell
clops setup-assistant web-status
clops setup-assistant web-plan
clops setup-assistant web-repair
clops setup-assistant web-verify
```

`web-status`, `web-plan`, and `web-repair` are non-destructive. They inspect
only safe setup evidence. `web-verify --confirm <HOP>` records a completed
verification in Setup Assistant state; it does not modify the launcher profile
or ChatGPT account.

Allowed recorded hop names are:

```text
FULL_HARNESS_TO_CODEX
CODEX_TO_CODEXLOCALOPS
WINDOWS_HOST_VISIBLE_THROUGH_LOCALOPS
```

## Stable runtime activation

Setup Assistant validates a candidate before activation and keeps the previous
runtime for rollback. After moving the candidate to its final path, activation
verifies the staged wheel hash and reinstalls that exact wheel with the active
Python interpreter and the no-deps option. This regenerates Windows console
launchers with the final interpreter path without changing the candidate's
selected optional features. Before changing Codex MCP registration or committing
setup state, activation checks clops --help and confirms the package imports
from the active runtime. A failed post-move check restores the previous runtime
and setup state.

## Managed AGENTS.md policy

The managed block preserves all user text outside its markers. Version 2 adds
the Web/Native2 rules required by this stage:

- prefer `codexLocalOps` for real Windows work;
- current opened workspace has priority;
- placeholder project names must not create literal directories;
- Web sandbox failure is not host failure;
- do not repair a healthy Local Ops runtime because Web cannot execute host
  Python;
- use `%USERPROFILE%\.codex-local-ops.venv\Scripts\python.exe` for dev host
  validation;
- explicit authorization denial cannot be bypassed through another executor;
- preserve persistent async jobs and inspect the same job after a disconnect;
- public/remote mutations still require their normal authorization boundary.

## Repair decisions

Local runtime repair remains component-specific. ChatGPT Web repair findings use
`repair_scope: chatgpt-web` and point to the exact incomplete setup step. The Web
repair planner does not stage a Local Ops runtime and does not reinstall it.

## Security boundary

Setup Assistant never persists ChatGPT credentials. The launcher state parser
whitelists only setup booleans/version markers and drops unknown fields. Manual
connection verification stores only a whitelisted hop name, status, and
timestamp.

Explicit denial statuses — `AUTHORIZATION_REQUIRED`, `PERMISSION_REQUIRED`,
`APPROVAL_REQUIRED`, `SECURITY_DENIED`, and `POLICY_DENIED` — become
`WAITING_FOR_USER`; the same side effect must not be retried with another
executor.
