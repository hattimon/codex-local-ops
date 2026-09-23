## What changed

Describe the concrete behavior or documentation change.

## Validation

- [ ] `python -m compileall -q src tests scripts`
- [ ] `python -m pytest -m "not interactive"`
- [ ] `python scripts/mcp_smoke.py` when MCP behavior changed
- [ ] Package/docs checks when packaging or public documentation changed

## Security and privacy

- [ ] No user-specific paths, credentials, private hosts, cookies, browser
      profiles, raw logs, local config, or generated validation artifacts were
      added.
- [ ] Permission/trusted-root/security behavior is documented when relevant.

## Platform status

State which of Windows, Linux, and macOS were actually exercised. Do not infer
native desktop validation from headless CI alone.
