# Security Policy

Codex Local Ops exposes local developer operations through MCP, so changes to
execution, trusted roots, desktop control, SSH, Docker, browser automation, and
credential handling should be reviewed as security-sensitive.

## Reporting a vulnerability

Use GitHub private vulnerability reporting from the repository's **Security**
tab when it is available. Do not open a public issue containing credentials,
tokens, private host details, cookies, SSH private material, or unredacted
configuration/log output.

If private vulnerability reporting is not available, contact the repository
maintainer through GitHub first and arrange a private channel before sending
sensitive details.

## Security model

- Project file operations are constrained by configured trusted roots.
- Path checks resolve real paths and reject traversal/symlink escapes.
- Local execution is governed by explicit permission profiles.
- Browser/desktop control has a separate computer-control mode.
- SSH hosts have per-host permission levels.
- Process output is bounded and common secret patterns are redacted before
  normal tool exposure.
- OBS credentials are stored through the configured secret store rather than
  returned by status calls.

Secret redaction reduces accidental exposure; it is not a substitute for
reviewing command scope, trusted roots, and permissions before enabling broad
local or remote execution.
