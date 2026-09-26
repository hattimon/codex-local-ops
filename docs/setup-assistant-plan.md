# Codex Local Ops Setup Assistant — STAGE 1 architecture plan

Status: design only. This stage does not install, repair, update, replace, or remove the working Local Ops runtime.

## 1. Scope and goals

The Setup Assistant is a Windows-first orchestration layer for a normal user who needs to get from a fresh or existing Codex environment to a verified Local Ops connection:

`ChatGPT Web -> Codex Native2 -> Full Harness -> Codex -> codexLocalOps -> Windows host`

The intended operating modes are:

- `INSTALL`
- `REPAIR`
- `UPDATE`
- `ROLLBACK`
- `DIAGNOSTICS`

Development/source state remains separate from the stable runtime. The stable runtime target is:

`%USERPROFILE%\.codex-local-ops-runtime.venv`

The stable runtime must be installed as a normal package build. It must never use `pip install -e`, an editable source link, or the development repository as its import path.

## 2. Current architecture findings

### Installer and runtime

`setup.ps1` already provides a useful Windows bootstrap foundation:

- discovers an independent CPython 3.11+ and rejects interpreters associated with AI/model runtimes;
- creates a dedicated user-level venv;
- installs the package;
- creates the Local Ops configuration;
- can seed one trusted root;
- backs up Codex `config.toml` before MCP registration;
- registers `codexLocalOps` as a STDIO MCP server;
- runs Local Ops diagnostics and `codex mcp list` unless diagnostics are skipped.

The current venv is `%USERPROFILE%\.codex-local-ops.venv`. That remains legacy behavior. Setup Assistant v2 should use `%USERPROFILE%\.codex-local-ops-runtime.venv` for the stable runtime while leaving development environments independent.

`repair.ps1` currently delegates directly to `setup.ps1`. It is therefore a reinstall-style repair entry point, not a diagnostic, component-aware repair engine.

`uninstall.ps1` backs up Codex `config.toml`, removes only the `mcp_servers.codexLocalOps` section, removes the runtime venv, and preserves Local Ops configuration unless `-RemoveConfig` is supplied. Its preservation policy should be reused, but its TOML editing should move to the new structured configuration layer.

### Packaging and optional features

`pyproject.toml` already supports a clean non-editable stable install:

- Python `>=3.11`;
- wheel build through Hatchling;
- console entry points `clops` and `codex-local-ops`;
- optional extras for `desktop`, `browser`, `windows`, and `obs`;
- dev/test dependencies kept separately.

Windows browser/desktop support is already split cleanly into optional dependencies. Playwright Chromium is an additional runtime asset, while FFmpeg/FFprobe remain system capabilities detected by diagnostics.

### Configuration and First Run

`src/codex_local_ops/config.py` is reusable as the Local Ops application configuration layer:

- managed home defaults to `%USERPROFILE%\.codex-local-ops`;
- configuration lives at `config\config.yaml`;
- defaults are merged with user configuration;
- the current schema is version `1`;
- writes use a temporary file followed by replacement;
- legacy config migration already exists.

`src/codex_local_ops/wizard.py` is reusable for First Run policy:

- local execution profiles: `SAFE`, `DEVELOPER`, `FULL`;
- computer-control modes: `OFF`, `SAFE`, `INTERACTIVE`, `FULL`;
- at least one existing trusted project root is required;
- browser/media/OBS choices are stored explicitly;
- completion state is persisted.

The Setup Assistant should call these existing APIs instead of creating a second Local Ops configuration format.

### Manager and CLI

The existing GUI Manager already manages trusted roots, permission profile, computer-control mode, SSH settings, and diagnostics. It is the right UI host for Setup Assistant pages later, but lifecycle orchestration should live in testable Python modules rather than in Tk UI callbacks.

The CLI already exposes `diagnostics`, `init`, `manager`, `wizard`, `first-run-status`, local agent commands, and local Git/GitHub commands. A `clops setup ...` command group can be added without changing existing command behavior.

### Diagnostics

`src/codex_local_ops/diagnostics.py` already detects:

- platform, Python and venv state;
- shell and executable availability;
- Git, GitHub CLI, Docker, SSH/SCP/SFTP, FFmpeg/FFprobe, Node/NPM;
- browser package state;
- desktop availability and Windows UI automation dependency;
- trusted-root presence;
- WSL on Windows;
- Docker health and other feature state.

This should remain the feature-diagnostics engine. Setup Assistant diagnostics should add lifecycle and connection checks around it: runtime identity, package version, state metadata, MCP registration, config integrity, backup integrity, and Native2/Full Harness path status.

Restricted Web/Native harness failures must not be used as proof that interactive Windows desktop/browser automation is broken. Interactive desktop validation remains a separate host-level check.

### Trusted roots and security controls

`src/codex_local_ops/safety.py` already provides the core path boundary:

- all project paths must resolve under an explicitly configured trusted root;
- UNC/network paths require an explicitly trusted UNC root;
- symlink traversal is checked to prevent escape from the trusted root;
- managed Local Ops paths are distinct from trusted project paths;
- raw execution is gated by execution profile/expert mode;
- computer control has a separate mode gate;
- secrets, tokens, cookies, authorization material and private keys are redacted.

These rules are security boundaries and must remain authoritative. Setup Assistant must never silently widen trusted roots.

### Git/GitHub and repository safety

The project already has reusable Git/GitHub support plus repository locking/change capture. Important existing boundaries are:

- Local Ops MCP exposes inspection-oriented Git/GitHub tools;
- mutating Git/GitHub operations are local CLI actions, not general MCP mutation tools;
- repository operations resolve through trusted-root checks;
- agent runs can acquire an exclusive repository mutation lock;
- before/after Git snapshots and sanitized change summaries are available;
- GitHub child process credential exposure is constrained and secret output is redacted.

Setup Assistant must not weaken those boundaries. Setup lifecycle changes should have their own lock because runtime/config mutation is global to the user and is different from repository mutation.

### Backup and rollback

Current setup/uninstall behavior creates timestamped backups of Codex `config.toml`. This is valuable but incomplete for lifecycle management. There is currently no first-class runtime version catalog, known-good runtime marker, interrupted-update recovery, transactional activation, or managed rollback operation.

No existing first-class managed `AGENTS.md` subsystem or explicit ChatGPT Web -> Native2 -> Full Harness configuration subsystem was found. These should be new layers, not embedded into the existing Local Ops core server.

## 3. Reuse versus new work

Reuse without redesigning:

- `config.py` for Local Ops user configuration and atomic YAML writes;
- `wizard.py` for First Run values and validation;
- `safety.py` for trusted roots, UNC/symlink rules, execution/computer-control policy, and redaction;
- `diagnostics.py` for Local Ops feature diagnostics;
- existing platform backends for Windows capability detection;
- Git/GitHub and repository-safety helpers where their semantics match;
- packaging metadata and optional extras in `pyproject.toml`;
- installer Python discovery policy;
- the existing principle of backing up Codex configuration before mutation;
- Manager and CLI as presentation/entry layers.

Add a new Setup Assistant layer for lifecycle state, runtime staging/activation, backup catalog, safe Codex configuration editing, managed instruction blocks, and bridge/harness status.

## 4. Proposed architecture

The implementation should keep orchestration independent from UI. PowerShell remains a bootstrap/launcher surface; Python owns state transitions and validation once the stable package can run.

Proposed modules:

### `setup_state.py`

Owns the persisted lifecycle model. Suggested state file:

`%USERPROFILE%\.codex-local-ops\setup\state.json`

Minimum state fields:

- schema version;
- install status: `ABSENT`, `STAGING`, `ACTIVE`, `DEGRADED`, `ROLLBACK_AVAILABLE`, `RECOVERY_REQUIRED`;
- active package version and build/source commit when available;
- active runtime path;
- previous known-good runtime record;
- last successful operation and timestamp;
- current transaction id and phase;
- requested feature set;
- config backup ids/hashes;
- MCP registration status;
- managed AGENTS block status;
- connection-chain status for Codex, Native2, Full Harness and ChatGPT Web;
- last validation summary;
- rollback eligibility and reason.

Writes must be atomic and state schema migration must be explicit.

### `setup_ops.py`

Pure lifecycle orchestration for:

- `preflight()`;
- `install()`;
- `update()`;
- `repair()`;
- `rollback()`;
- `diagnostics()`;
- interrupted transaction recovery.

It should call smaller modules and record every phase in `setup_state.py`. It must avoid importing GUI code.

### `runtime_ops.py`

Owns stable runtime creation, package install, staging validation, activation, and runtime identity inspection.

It must never perform editable installs.

### `backup_ops.py`

Owns bounded backup history and restore metadata for:

- Codex `config.toml`;
- managed `AGENTS.md` target(s);
- Setup Assistant state;
- Local Ops `config.yaml` when a lifecycle operation must migrate it;
- previous stable runtime directory during activation.

Backups require hashes, timestamps, source/target paths, transaction id, and restoration status.

### `codex_config.py`

Owns structured TOML read/modify/write for `%USERPROFILE%\.codex\config.toml`.

It must use `tomlkit`, preserve unrelated user content, and mutate only the managed `mcp_servers.codexLocalOps` entry unless a future explicitly versioned migration adds another owned key.

### `agents_config.py`

Owns managed `AGENTS.md` content through marker-delimited blocks. It never replaces an entire user file.

### `bridge_ops.py`

Models and validates the connection chain:

`ChatGPT Web -> Codex Native2 -> Full Harness -> Codex -> codexLocalOps -> Windows host`

This module records observable local state and produces manual instructions for steps that cannot be configured safely from Local Ops.

### Existing modules to extend

- `diagnostics.py`: lifecycle/runtime/config/bridge checks;
- `cli.py`: `clops setup ...` commands;
- `manager.py`: Setup Assistant screens later;
- `wizard.py`: reusable First Run values, possibly split into non-UI validation helpers if needed;
- `config.py`: schema migration hooks only if Setup Assistant requires new Local Ops settings.

## 5. Setup Assistant state machine

Every mutating operation should follow a transaction with an operation id.

Common lifecycle:

1. `PREFLIGHT`
2. `BACKUP`
3. `STAGE_RUNTIME` or `STAGE_CONFIG`
4. `VALIDATE_STAGING`
5. `ACTIVATE`
6. `REGISTER`
7. `POSTFLIGHT`
8. `COMMIT_KNOWN_GOOD`

On failure before activation, delete only the transaction staging area and leave the active runtime untouched.

On failure after activation, restore the immediately previous known-good runtime/config set when automatic rollback is safe. Otherwise mark `RECOVERY_REQUIRED` and provide a precise rollback command.

Only one Setup Assistant mutation may run at a time. Use a user-level setup lock under `%USERPROFILE%\.codex-local-ops\setup\`.

## 6. Stable runtime strategy

### Active target

The only stable active runtime path is:

`%USERPROFILE%\.codex-local-ops-runtime.venv`

Codex MCP registration points to:

`%USERPROFILE%\.codex-local-ops-runtime.venv\Scripts\python.exe -m codex_local_ops.server`

Keeping a fixed active path means normal package updates do not need to rewrite MCP registration merely because the version changed.

### Build and staging

For `INSTALL` and `UPDATE`:

1. Verify independent CPython 3.11+.
2. Build a wheel from the selected source/release into a transaction staging directory.
3. Create a fresh staging venv such as `%USERPROFILE%\.codex-local-ops\setup\staging\<transaction>\runtime.venv`.
4. Install the wheel non-editably into that venv with the requested stable extras. Windows defaults should cover core + `windows` + `desktop` + `browser`; OBS remains optional unless selected.
5. Install/verify Playwright Chromium when browser support is selected.
6. Run package import/version checks and noninteractive diagnostics against the staging interpreter.
7. Confirm entry points/server startup can be imported before activation.

The development repository may be the build source, but the activated runtime must contain only the installed package and dependencies. Moving or deleting the source tree must not break the stable runtime.

### Activation

Activation should use directory rename/swap semantics on the same volume:

- existing active runtime -> transaction-scoped previous-runtime slot;
- validated staging runtime -> `%USERPROFILE%\.codex-local-ops-runtime.venv`;
- validate the fixed active path;
- only then mark the new runtime known-good.

If an active Local Ops Python process prevents a Windows rename, activation must stop and report that the runtime is in use. The Setup Assistant should not partially overwrite files inside the active venv.

### UPDATE

`UPDATE` always builds a fresh staging venv. It must not run `pip install --upgrade` inside the active stable venv. This avoids dependency drift and leaves the previous runtime intact until activation.

### REPAIR

Repair begins with diagnostics and selects the smallest action:

- MCP registration broken -> rewrite only the managed MCP entry after backup;
- Local Ops config missing/corrupt -> repair/migrate configuration without replacing runtime when possible;
- optional package missing -> create a validated replacement staging runtime with the same package version and requested features;
- Playwright browser asset missing -> repair that asset using the stable interpreter;
- stable venv broken -> rebuild the same recorded known-good package version into staging and activate it;
- desktop unavailable because of restricted/noninteractive session -> report environment limitation; do not reinstall;
- trusted-root issue -> require explicit user selection; never add a broad root automatically.

## 7. Rollback strategy

Rollback is a first-class transaction, not a reinstall.

The known-good record must identify:

- package version;
- build/source commit or release id when available;
- Python version;
- selected extras/features;
- runtime directory retained from the previous activation;
- hashes and paths of Codex/AGENTS/config backups associated with activation;
- successful validation timestamp.

Default retention: keep the current runtime plus at least one immediately previous known-good runtime. Backup metadata can retain more history with a bounded policy.

Rollback flow:

1. preflight and lock;
2. verify the previous runtime and backup hashes;
3. back up current managed configuration again;
4. move the current active runtime to a failed/current transaction slot;
5. move the selected known-good runtime back to the fixed active path;
6. restore only Setup Assistant-managed configuration that must match that runtime;
7. run diagnostics and MCP registration verification;
8. mark the restored version active/known-good.

User-created Local Ops configuration, trusted roots and unrelated Codex settings should not roll back merely because the package version changes, unless a schema migration explicitly requires it. Any config schema downgrade must have a tested reverse migration or rollback must stop before destructive conversion.

Interrupted activation is recovered from transaction metadata and the existence of active/staging/previous slots. The assistant must never guess which directory is valid; it validates candidates before choosing recovery actions.

## 8. Safe `config.toml` management

The current regex replacement is adequate for the bootstrap script but should not be the long-term Setup Assistant writer.

The v2 rules are:

1. Read `%USERPROFILE%\.codex\config.toml` as bytes and create an exact backup before mutation.
2. Parse with `tomlkit` so comments, ordering and unrelated sections are preserved.
3. Refuse mutation if the existing TOML cannot be parsed; diagnostics should show the parse problem and backup location.
4. Own only `[mcp_servers.codexLocalOps]`.
5. Set `command` to the fixed stable runtime Python and `args` to `['-m', 'codex_local_ops.server']`.
6. Write to a same-directory temporary file and replace atomically.
7. Reparse the written file and verify the managed entry.
8. When Codex CLI is available, verify with `codex mcp list` or an equivalent structured command.
9. Record pre/post hashes and backup id in transaction state.

Uninstall/rollback should remove or restore only the managed entry. No operation may replace an entire user `config.toml` with a generated template.

## 9. Managed `AGENTS.md` configuration

Setup Assistant must treat `AGENTS.md` as user-owned text with one optional Local Ops-managed block.

Proposed markers:

```text
<!-- CODEX_LOCAL_OPS_SETUP_ASSISTANT_START -->
...managed instructions...
<!-- CODEX_LOCAL_OPS_SETUP_ASSISTANT_END -->
```

Rules:

- never overwrite text outside the markers;
- create a byte-for-byte backup before inserting, updating, or removing the managed block;
- if one valid managed block exists, replace only its contents;
- if duplicate/unbalanced markers exist, stop and report conflict instead of guessing;
- include a managed-block schema/version marker;
- keep machine-specific secrets, tokens and private paths out of the managed block;
- keep project-specific policy in project-level `AGENTS.md`; do not copy broad global permissions into arbitrary repositories;
- make the target scope explicit in state and diagnostics.

The managed content should describe the Local Ops workflow and safe execution expectations, not credentials or transient runtime state.

Before implementation, STAGE 2 should confirm the exact Codex version/scoping behavior for user-level versus repository-level `AGENTS.md`. The writer should support both, while Setup Assistant should only modify a target whose scope is known and intentionally selected.

## 10. Trusted-root configuration

Existing `wizard.configure_first_run()` and `safety.assert_trusted_path()` remain authoritative.

Setup Assistant behavior:

- discover candidate projects only to present choices;
- require explicit user selection before adding a trusted root;
- resolve the chosen path and require it to exist and be a directory;
- deduplicate canonical paths;
- preserve UNC rules;
- do not automatically trust `%USERPROFILE%`, a drive root, `D:\CODEX`, network shares, or the current directory merely because they are convenient;
- show the effective root before commit;
- allow later removal, but warn when existing configured projects would become inaccessible;
- validate symlink/junction behavior using the same safety functions as runtime operations.

The Setup Assistant may recommend the currently opened project as a candidate, but trust remains an explicit user decision.

## 11. ChatGPT Web / Codex Native2 / Full Harness integration strategy

Connection management must represent each hop separately. A successful Codex MCP registration does not prove that ChatGPT Web can reach the Windows host.

Suggested connection states:

1. `WINDOWS_HOST_READY`
   - stable Local Ops runtime imports and diagnostics pass;
   - requested desktop/browser components are present or explicitly marked optional/unavailable.

2. `CODEX_LOCAL_OPS_REGISTERED`
   - Codex `config.toml` points at the fixed stable runtime;
   - `codexLocalOps` appears in Codex MCP inventory;
   - a lightweight Local Ops tool call succeeds from Codex when a callable check is available.

3. `FULL_HARNESS_READY`
   - the configured Full Harness exposes the expected Native/MCP routing;
   - Setup Assistant records the harness identifier/version/status that can be observed locally;
   - no secrets from harness configuration are copied into logs/state.

4. `NATIVE2_READY`
   - Codex Native2 is installed/enabled in the relevant ChatGPT/Codex environment;
   - a non-destructive host evidence call such as platform/config status can cross the bridge.

5. `CHATGPT_WEB_READY`
   - the user has enabled/selected the required integration in ChatGPT Web;
   - a live end-to-end smoke call returns Windows-host evidence through Native2 and Full Harness.

`bridge_ops.py` should expose `status`, `configure_local`, and `test` semantics. It may configure local files owned by this project, but it must not claim to automate private ChatGPT account UI/state that has no supported local API.

The end-to-end smoke test should be read-only. A suitable success proof is a request that returns Local Ops platform/config status plus a nonce generated for that test, without performing filesystem mutation outside managed diagnostic state.

## 12. Manual human steps

Some steps must remain manual unless a supported API/connector is available at implementation time:

- signing in to ChatGPT/Codex accounts;
- installing/enabling Codex Native2 or the Full Harness when that action is controlled by ChatGPT/Codex UI;
- approving any browser/desktop permission prompts that Windows or a browser requires;
- selecting the correct ChatGPT Web integration/task when account UI state cannot be managed by Local Ops;
- explicitly choosing trusted project roots;
- entering credentials for services such as GitHub/SSH when no existing credential/session is available;
- resolving invalid or conflicting user-owned `config.toml` / `AGENTS.md` content;
- closing processes that hold the stable runtime open when Windows prevents an activation swap;
- confirming an end-to-end ChatGPT Web smoke result when the Web side cannot be queried programmatically.

Setup Assistant should generate exact instructions and detect completion where possible, but it should not automate sign-in, capture session cookies/tokens, or store ChatGPT credentials.

## 13. Security boundaries

The Setup Assistant must preserve these invariants:

- no editable stable install;
- no stable runtime import path pointing into the development checkout;
- no implicit trusted-root widening;
- no bypass of UNC/symlink escape protections;
- no secret values in state, logs, command summaries or backups metadata;
- no copying of ChatGPT/browser session tokens;
- no replacement of unrelated Codex configuration;
- no whole-file overwrite of user-owned `AGENTS.md`;
- no Git/GitHub mutation added to the MCP surface merely to support setup;
- no install/update activation before staging validation passes;
- no destructive repair when a smaller component repair is sufficient;
- no automatic claim that desktop/browser is broken based only on restricted harness behavior;
- no use of AI/model-bundled Python for the stable runtime;
- no concurrent setup/update/rollback transactions.

Backups containing user configuration should inherit the user's filesystem ACLs and remain under the Local Ops managed home. Their contents must not be printed in logs.

## 14. Future CLI and Manager surface

Proposed CLI:

```text
clops setup status
clops setup preflight
clops setup install
clops setup update
clops setup repair
clops setup rollback
clops setup backups
clops setup diagnostics
clops setup bridge status
clops setup bridge test
```

Proposed Manager pages:

- Overview
- Install / Update
- Connection / Harness
- Trusted Roots
- Features
- Backups / Rollback
- Diagnostics
- Logs

The GUI should display the same state/results returned by Python orchestration APIs and should not contain a second implementation of lifecycle rules.

## 15. Implementation phases

### Phase 2A — state, backup and safe configuration primitives

- add `setup_state.py`;
- add lifecycle lock and transaction ids;
- add `backup_ops.py`;
- add structured `codex_config.py` using `tomlkit`;
- add `agents_config.py` with managed-block parsing;
- unit-test failure recovery before any runtime activation code exists.

### Phase 2B — stable runtime staging and activation

- add `runtime_ops.py`;
- build/install wheel into fresh staging venv;
- add feature selection and Playwright asset handling;
- validate staging;
- add fixed-path activation swap and previous-runtime retention;
- add known-good records and interrupted-swap recovery tests.

### Phase 2C — lifecycle orchestration

- add `setup_ops.py`;
- implement `INSTALL`, `UPDATE`, `REPAIR`, `ROLLBACK`, `DIAGNOSTICS` state transitions;
- make repair diagnostic and component-specific;
- integrate config backups and postflight checks.

### Phase 2D — CLI integration

- add `clops setup ...` commands;
- keep current CLI commands backward compatible;
- return structured JSON-capable results for automation and Manager use.

### Phase 2E — bridge/harness integration

- add `bridge_ops.py`;
- discover supported local codex-chatgpt-web / Native2 / Full Harness configuration surfaces;
- implement local configuration/status where supported;
- provide explicit manual steps for account/UI-only actions;
- add read-only end-to-end smoke test.

### Phase 2F — Manager UI

- add Setup Assistant pages to `manager.py` or split the Manager into view modules if size justifies it;
- use the same orchestration APIs as CLI;
- present recovery/rollback state clearly.

### Phase 2G — installer migration and documentation

- make `setup.ps1` a thin Windows bootstrap that invokes Setup Assistant lifecycle APIs;
- convert `repair.ps1` to the real repair mode;
- update `uninstall.ps1` to use structured managed-config removal where practical;
- migrate documentation from the legacy `.codex-local-ops.venv` flow to the stable runtime path;
- preserve compatibility/migration guidance for existing users.

## 16. Files/modules expected to change

New files expected:

- `src/codex_local_ops/setup_state.py`
- `src/codex_local_ops/setup_ops.py`
- `src/codex_local_ops/runtime_ops.py`
- `src/codex_local_ops/backup_ops.py`
- `src/codex_local_ops/codex_config.py`
- `src/codex_local_ops/agents_config.py`
- `src/codex_local_ops/bridge_ops.py`
- focused tests such as `tests/test_setup_state.py`, `tests/test_setup_ops.py`, `tests/test_runtime_ops.py`, `tests/test_backup_ops.py`, `tests/test_codex_config.py`, `tests/test_agents_config.py`, and `tests/test_bridge_ops.py`.

Existing files likely to change:

- `setup.ps1`
- `repair.ps1`
- `uninstall.ps1`
- `pyproject.toml`
- `src/codex_local_ops/cli.py`
- `src/codex_local_ops/manager.py`
- `src/codex_local_ops/diagnostics.py`
- possibly `src/codex_local_ops/config.py` and `wizard.py` for schema/helpers;
- `README.md`
- `README_PL.md`
- `docs/installation.md`
- `docs/installation-pl.md`
- `docs/windows.md`
- `docs/troubleshooting.md`.

Existing `safety.py`, Git/GitHub modules and repository-safety code should preferably remain behaviorally unchanged unless tests expose a specific integration need.

## 17. Test strategy

Tests should be layered so most lifecycle logic can run without touching the real user profile.

### Unit tests with temporary homes

- state atomic write/read and schema migration;
- lifecycle lock contention;
- backup catalog hashing/retention/restore metadata;
- `config.toml` preservation of comments/unrelated sections;
- invalid TOML refusal without mutation;
- exact managed MCP entry add/update/remove;
- AGENTS managed-block insert/update/remove;
- duplicate/unbalanced AGENTS marker refusal;
- trusted-root canonicalization, UNC and symlink escape cases;
- redaction of secrets from lifecycle results/logs.

### Runtime transaction tests

- fresh staging venv receives a non-editable install;
- stable runtime does not import modules from the development source path;
- failed staging validation leaves active runtime unchanged;
- activation retains previous known-good runtime;
- failure after activation restores previous runtime/config when safe;
- interrupted activation can be recovered deterministically;
- update never mutates the active venv in place;
- repair is idempotent and only changes the diagnosed broken component;
- rollback restores the expected package/runtime identity;
- config/trusted roots survive package update and rollback.

### Integration tests

- bootstrap -> staged install -> MCP registration -> diagnostics using an isolated fake `%USERPROFILE%`;
- `codex mcp list` integration when Codex CLI is available;
- browser asset detection/repair without launching the user's real profile;
- bridge status distinguishes each connection hop;
- end-to-end read-only Native2/Full Harness smoke test where the harness is available.

### Windows interactive validation

Run separately in a normal interactive Windows session:

- pywinauto/window enumeration;
- screenshot capture;
- Playwright visible-browser start and basic navigation;
- any screen-recording capability;
- end-to-end ChatGPT Web -> Native2 -> Full Harness -> Codex -> Local Ops host proof.

A failure limited to a restricted Web/Native harness is reported as a harness/session limitation until reproduced in the normal Windows session.

### Regression validation

Keep the existing lightweight/core checks, including compile/import, MCP tool inventory policy, secret redaction, GitHub credential isolation, SSH guards, and the existing test suite. New setup tests must not expose mutating Git/GitHub tools through MCP.

## 18. STAGE 1 exit criteria

STAGE 1 is complete when this design is present and the documentation change passes lightweight repository validation. No runtime installation, stable runtime mutation, deployment, push, PR, or branch switch is part of this stage.
