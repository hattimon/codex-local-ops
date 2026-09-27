# AGENTS.md — Codex Local Ops

## Scope
These instructions apply to work inside the `codex-local-ops` repository.

Keep this file project-specific. Do not put HQTrader, Raspberry Pi addresses,
container names, trading rules, or other consumer-project details here.

Do not modify a user's global `~/.codex/AGENTS.md` or `~/.codex/config.toml`
unless the task explicitly asks for that.

End-user safety and diagnostics must be implemented in source code, tests, and
documentation. Do not rely on this AGENTS.md as a substitute for runtime
behavior that new users need.

## Core security invariant
A policy denial is an authorization boundary.

For results such as:
- `PERMISSION_DENIED`
- `AUTHORIZATION_REQUIRED`
- `PERMISSION_REQUIRED`
- `APPROVAL_REQUIRED`
- `SECURITY_DENIED`
- `POLICY_DENIED`

do not retry the blocked side effect through a different executor, shell,
bridge, MCP server, browser terminal, PowerShell, `cmd.exe`, `ssh.exe`, or
another backend.

Diagnose and fix the relevant policy or return a precise user-actionable
permission report.

Technical capability is not authorization.

## Preserve security controls
Do not weaken or remove trusted-root checks, trusted-host checks, permission
profiles, expert-mode gates, approval boundaries, audit/sanitization behavior,
or safe-mode restrictions just to make a task pass.

Prefer the smallest change that preserves the intended security model.

## SSH model
Treat these as separate concepts:
- requested host/profile identifier,
- target hostname/user/port,
- `permission_profile`,
- global permission/expert-mode state.

Current SSH permission profiles are `READ_ONLY`, `OPERATIONS`, and `FULL`.

Do not assume `FULL` means unrestricted shell. Inspect the actual validator.

In the current implementation, `FULL` is gated by the global expert-mode
policy. A `FULL` denial may therefore be caused by `permissions.expert_mode`,
not by a command allowlist.

Do not invent a rule name such as `ssh.allowed_commands` unless that rule
actually exists and is the validator that rejected the operation.

## Structured SSH permission-denial contract
When changing SSH authorization behavior, prefer machine-readable structured
errors over an opaque reason string.

For a denied remote command, return as many of these fields as are known:

```text
status: PERMISSION_DENIED
reason: <human-readable summary>
host_profile: <requested host/profile id>
target: <safe target identifier>
policy_mode: <READ_ONLY|OPERATIONS|FULL>
command: <requested command>
path: <relevant remote path when safely and reliably identified>
blocking_rule: <actual validator/rule>
config: <runtime-resolved config path>
```

Optional diagnostic fields may include:
```text
blocking_validator: <function/check>
rejected_token: <token/syntax when applicable>
allowed_capability: <safer supported primitive, if one exists>
```

Rules:
1. `host_profile` is the requested/configured host identifier, not the permission mode.
2. `policy_mode` is the host's `permission_profile`.
3. `blocking_rule` must describe the real check that failed.
4. `config` must come from runtime configuration, e.g. `config_path()`; never hardcode a developer-specific path.
5. Do not expose private keys, passwords, tokens, secret values, or unrelated configuration.
6. If `path` cannot be derived safely and unambiguously, omit it instead of guessing.
7. Keep the human-readable `reason` for compatibility.

Examples of legitimate `blocking_rule` values, when they match the real failing check:
- `ssh.host.trusted`
- `permissions.expert_mode`
- `ssh.read_only_commands`
- `ssh.operations_commands`
- `ssh.shell_meta`

## Read-only SSH behavior
A read-only task should not require broader mutation privileges.

Where practical, prefer or add dedicated safe primitives for:
- remote file read,
- remote file stat,
- remote file existence check,
- log read,
- process/status inspection.

Do not broaden to unrestricted shell just to support `cat`, `stat`, or `test`.

## Error ergonomics
Errors returned by MCP tools should be structured JSON, human-readable,
sanitized, and explicit about capability vs permission failures.

Distinguish:
- capability unavailable,
- permission/policy denial,
- not found,
- command/runtime failure,
- timeout.

Do not convert a permission denial into a generic runtime failure.

## Async-job discipline
Long-running operations should use the existing async-job mechanism when appropriate.

Before starting a long operation:
1. check whether an equivalent job/result already exists,
2. use `resume_key`/resume behavior where applicable,
3. avoid duplicate execution,
4. poll existing job status/output instead of restarting work.

A client/Web stream disconnect is not evidence that the underlying job stopped.

## Bounded execution
Synchronous command execution must remain bounded.

Preserve timeout enforcement, Windows process-tree termination, bounded
stdout/stderr handling, and async routing for work that exceeds the sync budget.

## Cross-platform requirement
Codex Local Ops is cross-platform.

Changes must consider Windows, Linux, and macOS unless the code path is
explicitly platform-specific.

Do not hardcode Windows-only paths or binaries in shared logic.

## Configuration changes
Preserve backward compatibility where practical.

When adding config fields:
- define safe defaults,
- merge with existing user config,
- avoid destructive rewrites,
- document behavior,
- add migration/compatibility tests when needed.

Never expose secrets while reporting configuration.

## Runtime / stable activation
Do not bypass the stable-runtime staging/validation/rollback design.

For runtime updates:
1. build the candidate,
2. validate the candidate,
3. preserve a rollback point,
4. activate only after validation succeeds,
5. report the activated version/commit.

## Git discipline
`main` is protected.

For code/documentation changes:
- work on a dedicated branch,
- keep commits focused,
- preserve unrelated existing work,
- do not reset/revert user changes without explicit instruction,
- prefer squash merge through PR after CI passes.

Do not force-push protected history.

## Tests
Any behavior change should have focused regression coverage.

For SSH permission diagnostics, tests should cover at least:
- untrusted host denial,
- `READ_ONLY` accepted read command,
- `READ_ONLY` rejected mutation,
- shell-meta rejection,
- `OPERATIONS` allowed/rejected commands,
- `FULL` with expert mode disabled,
- `FULL` with expert mode enabled,
- structured denial fields,
- runtime-resolved config path,
- no secret leakage.

When practical, run focused tests first, then full pytest, compileall,
changed-file lint/format checks, and `git diff --check`.

Do not mix unrelated repo-wide cleanup into a focused fix.

## Documentation
When runtime behavior or public MCP output changes, update relevant docs in the
same change.

Document:
- what the fields mean,
- the distinction between host profile and permission policy,
- why `FULL` may still be denied,
- how to resolve a denial without bypassing security.

Keep examples generic. Do not publish private infrastructure details.

## Release discipline
Do not tag or publish a release until the change is merged to protected `main`,
required Linux/Windows/macOS CI is green, and release validation passes.

## Before finishing a task
Report:
- files changed,
- behavior changed,
- tests run and results,
- security implications,
- unresolved items,
- exact next step.

For permission fixes, explicitly state whether any authorization boundary was bypassed.
The expected answer is `NO`.
