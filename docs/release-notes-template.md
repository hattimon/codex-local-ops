# Release notes template

Use this template when preparing a GitHub prerelease/release. Do not include
machine-specific paths, credentials, raw validation artifacts, or private host
information.

## Codex Local Ops vX.Y.Z

### Status

- Windows: supported / validated status
- Linux: implementation / native-validation status
- macOS: implementation / CI/native-validation status

### Highlights

- User-visible change
- User-visible change

### Installation and update

Link to `docs/installation.md` and call out any release-specific migration step.

### Validation

- CI matrix result
- unit-test result
- package build/check result
- relevant native validation result

### Known limitations

- Limitation with clear platform/scope

### Artifacts

- Source archive (GitHub-generated)
- Wheel/sdist built by the release validation workflow

### Security

State whether the release changes trusted-root, permission, credential, or
remote-execution behavior. Link to `SECURITY.md` for vulnerability reporting.
