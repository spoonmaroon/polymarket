# Task 2 Report: Native GPU Node Runtime Keeper

## RED
- Test run (expected fail before file exists):
  - Command: `uv run pytest tests/scripts/test_runtime_keeper_scripts.py::test_gpu_node_runtime_keeper_installer_is_native_linux_without_wsl -q`
  - Result: `FAILED` with `FileNotFoundError` for `scripts/install_gpu_node_runtime_keeper.sh`

## GREEN
- Test run (after implementation):
  - Command: `uv run pytest tests/scripts/test_runtime_keeper_scripts.py::test_gpu_node_runtime_keeper_installer_is_native_linux_without_wsl -q`
  - Result: `1 passed in 0.00s`

## Changes
- Added test in `tests/scripts/test_runtime_keeper_scripts.py` to enforce native Linux installer constraints and confirm no WSL/Windows scheduling references.
- Added `scripts/install_gpu_node_runtime_keeper.sh` with:
  - env-consumable defaults `POLYMARKET_REPO`, `POLYMARKET_DATA_DIR`, `POLYMARKET_BIN_DIR`
  - generated loop script at `$POLYMARKET_BIN_DIR/polymarket-runtime-keeper-loop.sh`
  - generated user service `polymarket-runtime-keeper.service`
  - native Linux runtime keeper invocation (no `wsl.exe`, `powershell.exe`, `Register-ScheduledTask`)
  - `systemctl --user enable --now polymarket-runtime-keeper.service`

## Task 2 Persistence Hardening (Follow-up)
- Updated `scripts/install_gpu_node_runtime_keeper.sh` to:
  - attempt `loginctl enable-linger "$USER"` when `loginctl` is present, with non-blocking warning on failure
  - use existing user systemd path as before when `systemctl --user` is available
  - otherwise fallback to `nohup` background start, writing PID to `$DATA_DIR/live/runtime-keeper.pid` and logs to `$DATA_DIR/logs/runtime-keeper.log`
  - avoid exiting with failure in fallback mode
- Updated `tests/scripts/test_runtime_keeper_scripts.py` assertions to require:
  - `loginctl enable-linger` invocation and `command -v loginctl` guard
  - `nohup "$LOOP_SCRIPT"` start fallback, PID file write, and log redirection
  - continued absence of `wsl.exe`, `powershell.exe`, and `Register-ScheduledTask`

## Focused Task 2 Evidence (Updated)
- Test run:
  - `uv run pytest tests/scripts/test_runtime_keeper_scripts.py -q`
  - Result: `4 passed in 0.01s`

## Re-Review Fix
- RED test run:
  - Command: `uv run pytest tests/scripts/test_runtime_keeper_scripts.py -q -k literal_loop_script`
  - Result: failed because the generated loop script contained installer-expanded values instead of the literal runtime references
- GREEN test run:
  - Command: `uv run pytest tests/scripts/test_runtime_keeper_scripts.py -q`
  - Result: `5 passed in 0.34s`
- Fixed `scripts/install_gpu_node_runtime_keeper.sh` heredoc escaping so the generated loop script preserves:
  - `ENGINE_BIN="${POLYMARKET_ENGINE_BIN:-$HOME/.local/bin/polymarket-engine}"`
  - `exec "$ENGINE_BIN" runtime-keeper`
- Switched the systemd availability probe from `systemctl --user status` to `systemctl --user show-environment`
