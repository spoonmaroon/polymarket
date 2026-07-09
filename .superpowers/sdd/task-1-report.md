# Task 1 Report

## RED
- Added tests `test_gpu_node_spoon_artifact_sync_installer_is_native_linux_and_role_safe` and `test_thepc_artifact_sync_installer_delegates_to_generic_gpu_node_installer`.
- Ran:
  - `cd /Users/goon/polymarket-server2-cuda-sdd && uv run pytest tests/scripts/test_deploy_script.py::test_gpu_node_spoon_artifact_sync_installer_is_native_linux_and_role_safe tests/scripts/test_deploy_script.py::test_thepc_artifact_sync_installer_delegates_to_generic_gpu_node_installer -q`
- Observed 2 failures: new file missing and THEPC installer no longer delegated.

## GREEN
- Implemented:
  - `scripts/install_gpu_node_spoon_artifact_sync.sh` (new generic Linux installer)
  - `scripts/install_thepc_spoon_artifact_sync.sh` (compatibility wrapper)
- Updated tests in `tests/scripts/test_deploy_script.py` with both required assertions.
- Ran:
  - `cd /Users/goon/polymarket-server2-cuda-sdd && uv run pytest tests/scripts/test_deploy_script.py::test_gpu_node_spoon_artifact_sync_installer_is_native_linux_and_role_safe tests/scripts/test_deploy_script.py::test_thepc_artifact_sync_installer_delegates_to_generic_gpu_node_installer -q`
- Result: `2 passed`
- Also verified legacy compatibility test passes:
  - `cd /Users/goon/polymarket-server2-cuda-sdd && uv run pytest tests/scripts/test_deploy_script.py::test_thepc_spoon_artifact_sync_installer_is_role_safe tests/scripts/test_deploy_script.py::test_gpu_node_spoon_artifact_sync_installer_is_native_linux_and_role_safe tests/scripts/test_deploy_script.py::test_thepc_artifact_sync_installer_delegates_to_generic_gpu_node_installer -q`
- Result: `3 passed`

## TASK 1.1 SSH `Match` Block Preservation Fix
- Added regression test:
  - `test_gpu_node_spoon_artifact_sync_preserves_match_blocks_after_managed_host` in `tests/scripts/test_deploy_script.py`.
  - The test extracts and executes the embedded Python rewrite block from
    `scripts/install_gpu_node_spoon_artifact_sync.sh` against a fixture config containing:
    - a managed `Host spoon` block
    - a following `Match` block
- RED evidence (before fix):
  - `cd /Users/goon/polymarket-server2-cuda-sdd && ./.venv/bin/pytest tests/scripts/test_deploy_script.py -k 'preserves_match_blocks_after_managed_host'`
  - Result: `1 failed` (the `Match` block was dropped from rewritten config)
- GREEN evidence (after fix):
  - `cd /Users/goon/polymarket-server2-cuda-sdd && ./.venv/bin/pytest tests/scripts/test_deploy_script.py -k "thepc_spoon_artifact_sync_installer_is_role_safe or gpu_node_spoon_artifact_sync_installer_is_native_linux_and_role_safe or thepc_artifact_sync_installer_delegates_to_generic_gpu_node_installer or preserves_match_blocks_after_managed_host"`
  - Result: `4 passed`
- Fix in `scripts/install_gpu_node_spoon_artifact_sync.sh`:
  - changed managed-host skip-stop condition from only `Host ` to `Host ` or `Match ` so rewrite now preserves subsequent top-level `Match` blocks instead of deleting them while skipping.

## TASK 1.2 SSH `Include` Preservation Fix
- Added regression test:
  - `test_gpu_node_spoon_artifact_sync_preserves_include_after_managed_host` in `tests/scripts/test_deploy_script.py`.
  - The test extracts and executes the embedded Python rewrite block from
    `scripts/install_gpu_node_spoon_artifact_sync.sh` against a fixture config containing:
    - a managed `Host spoon` block
    - a following top-level `Include ~/.ssh/conf.d/*` line
- RED evidence (before fix):
  - `cd /Users/goon/polymarket-server2-cuda-sdd && uv run pytest tests/scripts/test_deploy_script.py -k 'artifact_sync'`
  - Result: `1 failed, 4 passed`
  - Failure showed `Include ~/.ssh/conf.d/*` was deleted by the line-based skip loop.
- GREEN evidence (after fix):
  - `cd /Users/goon/polymarket-server2-cuda-sdd && uv run pytest tests/scripts/test_deploy_script.py -k 'artifact_sync'`
  - Result: `5 passed`
- Fix in `scripts/install_gpu_node_spoon_artifact_sync.sh`:
  - replaced the lossy skip loop with exact removal of the old managed `Host spoon` stanza and in-place replacement with the rewritten managed block, so later top-level directives like `Include ~/.ssh/conf.d/*` survive unchanged.
