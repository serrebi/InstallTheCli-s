# AGENTS.md

This file is for coding agents working in this repo.

## What This Project Is

Windows/Linux installer for common AI CLIs.

Main entry points:
- `ai_cli_installer_gui.py` (wxPython GUI; supports Windows and Linux)
- `install_all_windows.ps1` (one-click PowerShell installer with subcommands/help)
- `install_all_linux.sh` (one-click Linux installer with subcommands/help + cron updater)
- `test_ai_cli_installer_gui.py` (unit tests; high coverage)
- `build_exe.bat` + `InstallTheCli.spec` (Windows EXE build via PyInstaller)

## Current Behavior (Do Not Break Quietly)

Installed CLIs currently include:
- Claude
- Codex
- Gemini
- Grok (`@vibe-kit/grok-cli`)
- Qwen
- GitHub Copilot CLI
- Mistral Vibe CLI (`mistral-vibe`)
- Ollama (official install)

Auto-update behavior:
- Windows GUI / PowerShell script: hidden Scheduled Task (`InstallTheCli - Update AI CLIs`)
  - Triggers: startup, logon, daily (`3:00AM` default)
  - No visible cmd/PowerShell window
- Linux script: cron updater
  - `@reboot`
  - daily (`0 3 * * *` default)

## Hard Requirements / Invariants

1. Keep installs quiet.
- npm commands should keep:
  - `--no-fund`
  - `--no-audit`
  - `--no-update-notifier`
  - `--loglevel error`
- Pip commands should stay quiet/non-interactive where practical.

2. When running `npm.cmd` by absolute path on Windows, ensure child processes can still find `node`.
- Prepend the npm directory (usually `C:\Program Files\nodejs`) to subprocess `PATH`.
- Also set `npm_config_update_notifier=false` for subprocesses and background updater scripts.
- This prevents failures like `'"node"' is not recognized...` during npm installs (seen with Gemini).

3. Keep Ollama official.
- Windows: `winget` package `Ollama.Ollama`
- Linux: official installer script (`https://ollama.com/install.sh`)

4. Keep Mistral Vibe aligned with docs.
- Reference: `https://docs.mistral.ai/mistral-vibe/introduction`
- Windows path uses Python `3.14` (install if needed) + `pip`/`uv`
- Linux path supports Python `3.12+`, plus `pip`/`uv` (and handles PEP 668 via `--break-system-packages`)

5. `install_all_linux.sh` must remain LF-only line endings.
- CRLF causes Bash errors (`$'\r': command not found`) when run on Linux.
- If editing on Windows, normalize to LF before testing.

6. Preserve hidden/background updater behavior.
- No visible cmd/PowerShell windows for auto-updates on Windows.
- Cron script should remain non-interactive on Linux.

## Python / Build Version

Use Python `3.14` on Windows for tests/builds in this repo.

Preferred commands:
- `py -3.14 -m unittest -q test_ai_cli_installer_gui.py`
- `py -3.14 -m coverage run -m unittest -q test_ai_cli_installer_gui.py`
- `py -3.14 -m coverage report -m ai_cli_installer_gui.py test_ai_cli_installer_gui.py`
- `cmd /c build_exe.bat`

`build_exe.bat` is expected to use `py -3.14 -m PyInstaller`.

## Testing Expectations

Before shipping behavior changes:
- Run the full unit test file: `test_ai_cli_installer_gui.py`
- Keep coverage at/near current level (currently designed for `100%`)
- Add tests for:
  - new CLI specs
  - installer branching/fallbacks
  - updater changes
  - script content changes (PowerShell/Bash one-click scripts)

If you change platform-specific behavior:
- Validate at least one dry-run path locally.
- For Linux script changes, prefer:
  - `bash -n install_all_linux.sh`
  - `./install_all_linux.sh help`
  - `./install_all_linux.sh list`
  - `./install_all_linux.sh install codex --dry-run --no-cron`

## Editing Guidance

- Prefer small, targeted patches.
- Keep logs explicit; users rely on the installer log output to diagnose failures.
- Do not remove fallback behaviors without replacement:
  - Codex locked-file (`EBUSY`) retry and fallback-to-existing-install
  - Windows Scheduled Task registration warning handling
  - Linux distro detection / package manager branching

## User-Facing Scripts (Command UX)

`install_all_windows.ps1` should continue to support:
- `install-all`
- `install <target>`
- `setup-updater`
- `list`
- `help`
- `Get-Help .\install_all_windows.ps1 -Detailed`

`install_all_linux.sh` should continue to support:
- `install-all`
- `install <target>`
- `setup-cron`
- `list`
- `help`
- convenience alias: `./install_all_linux.sh codex`

## Packaging Notes

`InstallTheCli.spec` is intentionally simple:
- `datas=[]`
- `hiddenimports=[]`

Auto-update scripts are generated at runtime under user/system state locations, not bundled.

## If You Add A New CLI

Update all of these together:
- `CLI_SPECS` in `ai_cli_installer_gui.py`
- install logic (if not plain npm)
- desktop shortcut resolution rules if needed
- auto-update (Windows + Linux one-click updater scripts, if applicable)
- one-click scripts (`install_all_windows.ps1`, `install_all_linux.sh`)
- tests in `test_ai_cli_installer_gui.py`
- `README.md`

