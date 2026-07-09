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
