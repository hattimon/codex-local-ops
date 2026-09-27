# Changelog

All notable changes to this project will be documented in this file.

The project follows Semantic Versioning for Git tags and releases. Python
package versions use the equivalent PEP 440 spelling where needed; for example,
Git tag `v0.1.0-beta.1` corresponds to package version `0.1.0b1`.

## 0.1.0-beta.1 - Unreleased

- Structured SSH `PERMISSION_DENIED` diagnostics identify the host profile, policy rule, and safe command context; `FULL` inherits bounded `OPERATIONS` commands without expert mode.
- Initial public prerelease preparation.
- Windows is the primary supported and interactively validated platform.
- Linux and macOS platform backends are implemented with native validation still in progress.
- STDIO MCP tools for platform/system operations, trusted files and shell workflows,
  SSH, WSL, Docker, Git, async jobs, browser automation, desktop automation,
  FFmpeg/media processing, optional OBS control, and optional animation backends.
- Cross-platform CI and package build validation.
- English and Polish public documentation.
