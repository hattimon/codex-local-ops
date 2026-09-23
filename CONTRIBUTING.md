# Contributing to Codex Local Ops

Contributions are welcome. Windows is the primary validated platform; Linux and
macOS support should keep explicit capability reporting where the host desktop
or operating system cannot provide a feature.

## Development setup

Use CPython 3.11 or newer in a development environment:

```sh
python -m pip install -e ".[dev,desktop,browser,obs,windows]"
```

The `windows` extra is guarded by a platform marker and is ignored on other
operating systems.

## Validation

Before opening a pull request, run:

```sh
python -m compileall -q src tests scripts
python -m pytest -m "not interactive"
python scripts/mcp_smoke.py
python -m build
```

Interactive desktop validation is intentionally separate from headless CI.
Changes to native Windows desktop behavior should also be exercised from a
normal interactive Windows session using
`scripts/validate_interactive_windows.ps1`.

## Project rules

- Keep trusted-root and permission checks intact.
- Preserve structured `CAPABILITY_UNAVAILABLE` results for optional features.
- Use async/session jobs for operations that can exceed a normal MCP request.
- Do not commit user-specific absolute paths, credentials, SSH private data,
  browser profiles, cookies, local configuration, raw logs, or generated media.
- Do not add machine-specific validation artifacts to the repository.
- Keep platform support claims consistent with the validation actually
  performed.

See [SECURITY.md](SECURITY.md) before changing execution, desktop-control,
credential, SSH, or remote-operation behavior.
