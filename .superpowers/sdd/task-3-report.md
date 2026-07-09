# Task 3 Report: Native `server2` GPU Deploy Script

## RED

Command:

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
uv run pytest tests/scripts/test_deploy_script.py::test_gpu_node_deploy_script_targets_server2_native_linux_runtime -q
```

Result:

```text
FAILED tests/scripts/test_deploy_script.py::test_gpu_node_deploy_script_targets_server2_native_linux_runtime
FileNotFoundError: [Errno 2] No such file or directory: '/Users/goon/polymarket-server2-cuda-sdd/scripts/deploy_gpu_node.sh'
```

## GREEN

Command:

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
uv run pytest tests/scripts/test_deploy_script.py::test_gpu_node_deploy_script_targets_server2_native_linux_runtime -q
```

Result:

```text
1 passed in 0.01s
```

Syntax check:

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
bash -n scripts/deploy_gpu_node.sh
```

Result:

```text
0
```

## Notes

- Added `scripts/deploy_gpu_node.sh` for the native `server2` GPU deploy path.
- Added the regression test in `tests/scripts/test_deploy_script.py`.
- Script keeps the local clean-tree and `origin/main` checks, uses native Linux remote commands, and ends with the required health and compose smoke checks.

## Task 3 Fix: Safety Guards, Env Forwarding, and Role Gate

## RED

Command:

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
uv run pytest -q tests/scripts/test_deploy_script.py -k 'gpu_node_deploy_script_refuses_dirty_or_unreachable_remote or gpu_node_deploy_script_forwards_env_to_helper_scripts or gpu_node_deploy_script_rejects_unsupported_role or gpu_node_deploy_script_targets_server2_native_linux_runtime'
```

Result:

```text
FAILED tests/scripts/test_deploy_script.py::test_gpu_node_deploy_script_refuses_dirty_or_unreachable_remote
FAILED tests/scripts/test_deploy_script.py::test_gpu_node_deploy_script_forwards_env_to_helper_scripts
FAILED tests/scripts/test_deploy_script.py::test_gpu_node_deploy_script_rejects_unsupported_role
```

## GREEN

Command:

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
uv run pytest -q tests/scripts/test_deploy_script.py -k 'gpu_node_deploy_script_refuses_dirty_or_unreachable_remote or gpu_node_deploy_script_forwards_env_to_helper_scripts or gpu_node_deploy_script_rejects_unsupported_role or gpu_node_deploy_script_targets_server2_native_linux_runtime'
bash -n scripts/deploy_gpu_node.sh
```

Result:

```text
4 passed, 47 deselected in 0.01s
```

## Notes

- `scripts/deploy_gpu_node.sh` now checks `git ls-remote` before clone/checkout and refuses dirty tracked, staged, or untracked repo state.
- Helper scripts now receive the advertised `TARGET_PLATFORM`, `POLYMARKET_DATA_DIR`, `POLYMARKET_BIN_DIR`, and `POLYMARKET_REPO` values.
- `GPU_NODE_DEPLOY_ROLE` now gates startup with an explicit `server2-gpu-api` case and exits `2` for unsupported roles.
