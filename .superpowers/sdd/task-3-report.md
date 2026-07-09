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

## Task 3 Re-Review Fix: Branch Head Match and Singleton Writer Guard

## RED

Before the patch, the new regression assertions would have failed because `scripts/deploy_gpu_node.sh` still:

- hard-rejected `GPU_NODE_BRANCH` values other than `main`
- fetched and compared `origin/main` instead of the selected branch head
- started `api gpu-probability-worker` without checking that the old desktop GPU writer was absent

## GREEN

Commands:

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
uv run pytest -q tests/scripts/test_deploy_script.py -k 'gpu_node_deploy_script_targets_server2_native_linux_runtime or gpu_node_deploy_script_guards_old_writer_before_startup'
bash -n scripts/deploy_gpu_node.sh
```

Result:

```text
2 passed, 50 deselected in 0.08s
```

## Notes

- `GPU_NODE_BRANCH` is now honored as the deployed remote branch, with a strict `origin/$GPU_NODE_BRANCH` head match against `DEPLOY_REF`.
- `GPU_NODE_OLD_WRITER_HOST` defaults to `spoon@100.100.109.27`, and the deploy path fails closed unless the old GPU probability writer is verified absent or the check is explicitly skipped.
- The old-writer guard sits before `up -d --no-build api gpu-probability-worker`, which keeps the singleton-writer constraint in the startup path itself.

## Task 3 Final Review Fix: Old Writer State Semantics

## RED

Command:

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
uv run pytest -q tests/scripts/test_deploy_script.py -k 'gpu_node_deploy_script_targets_server2_native_linux_runtime or gpu_node_deploy_script_guards_old_writer_before_startup or gpu_node_deploy_script_treats_only_active_old_writer_states_as_blocking'
```

Result:

```text
FAILED tests/scripts/test_deploy_script.py::test_gpu_node_deploy_script_targets_server2_native_linux_runtime
FAILED tests/scripts/test_deploy_script.py::test_gpu_node_deploy_script_guards_old_writer_before_startup
FAILED tests/scripts/test_deploy_script.py::test_gpu_node_deploy_script_treats_only_active_old_writer_states_as_blocking
```

## GREEN

Command:

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
uv run pytest -q tests/scripts/test_deploy_script.py -k 'gpu_node_deploy_script_targets_server2_native_linux_runtime or gpu_node_deploy_script_guards_old_writer_before_startup or gpu_node_deploy_script_treats_only_active_old_writer_states_as_blocking'
bash -n scripts/deploy_gpu_node.sh
```

Result:

```text
3 passed, 50 deselected in 0.08s
```

## Notes

- The guard now checks container state with `docker inspect -f '{{.State.Status}}' ... 2>/dev/null || true`.
- Active states `running`, `restarting`, `paused`, and `created` block the deploy.
- Missing, empty, and `exited` states are allowed, and SSH or unexpected statuses fail closed.

## Task 3 Final Review Fix: Guard Order, Fail-Closed Probe, and API URL Forwarding

## RED

Command:

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
uv run pytest tests/scripts/test_deploy_script.py -k 'gpu_node_deploy_script_targets_server2_native_linux_runtime or gpu_node_deploy_script_guards_old_writer_before_startup or gpu_node_deploy_script_forwards_env_to_helper_scripts'
uv run pytest tests/scripts/test_runtime_keeper_scripts.py -k 'gpu_node_runtime_keeper_installer_is_native_linux_without_wsl'
bash -n scripts/deploy_gpu_node.sh scripts/install_gpu_node_runtime_keeper.sh
```

Result:

```text
3 failed, 50 deselected
1 failed, 4 deselected
scripts/deploy_gpu_node.sh: syntax error near unexpected token `)'
```

## GREEN

Commands:

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
uv run pytest tests/scripts/test_deploy_script.py
uv run pytest tests/scripts/test_runtime_keeper_scripts.py
bash -n scripts/deploy_gpu_node.sh scripts/install_gpu_node_runtime_keeper.sh
```

Result:

```text
53 passed in 0.72s
5 passed in 0.37s
```

## Notes

- The old-writer guard now runs before `install_gpu_node_runtime_keeper.sh` and before `up -d --no-build api gpu-probability-worker`.
- The remote probe first checks `docker info` and then falls back to `absent` only when `docker inspect` cannot find the container.
- `POLYMARKET_API_BASE_URL` now defaults to `http://127.0.0.1:8000` in the keeper installer and is forwarded from `deploy_gpu_node.sh` as `http://127.0.0.1:$GPU_NODE_API_PORT`.

## Task 3 Final Review Fix: Validate `GPU_NODE_DEPLOY_ROLE` Before Build/Install/Startup and Quote Smoke `cd` Path

## RED

Initial assertions for this finding were added and then fixed with the script change below.

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
.venv/bin/pytest tests/scripts/test_deploy_script.py -k 'gpu_node_deploy_script or validates_role_before_remote_mutations or quotes_smoke_repo_path_for_ssh'
```

Result before fix:

```text
8 tests selected, with the new role-order and smoke-quote assertions failing (role gate observed after helper calls; `cd $GPU_NODE_REPO` unquoted in final smoke check).
```

## GREEN

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
.venv/bin/pytest tests/scripts/test_deploy_script.py -k 'gpu_node_deploy_script or validates_role_before_remote_mutations or quotes_smoke_repo_path_for_ssh'
bash -n scripts/deploy_gpu_node.sh
```

Result:

```text
8 passed, 47 deselected
0
```

## Notes

- Moved `GPU_NODE_DEPLOY_ROLE` case validation to the top of the remote block before any repo mutation/build/install/startup path.
- Removed the later role-only dispatch and kept the startup path in the single supported role flow.
- Kept old-writer guard and runtime-keeper startup order intact once role is validated.
- Quoted the final smoke command `cd` path as `cd "$GPU_NODE_REPO"`.

## Task 3 Final Review Fix: Main-Only Deploy Ref and Dual-Container Old Runtime Guard

## RED

I updated the assertions first so the current script went red on the two review findings:

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
uv run --with pytest pytest -q tests/scripts/test_deploy_script.py -k 'gpu_node_deploy_script_targets_server2_native_linux_runtime or gpu_node_deploy_script_guards_old_writer_before_startup or gpu_node_deploy_script_treats_only_active_old_writer_states_as_blocking'
```

Result before the script fix:

```text
3 failed, 52 deselected
```

## GREEN

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
uv run --with pytest pytest -q tests/scripts/test_deploy_script.py -k 'gpu_node_deploy_script_targets_server2_native_linux_runtime or gpu_node_deploy_script_guards_old_writer_before_startup or gpu_node_deploy_script_treats_only_active_old_writer_states_as_blocking'
bash -n scripts/deploy_gpu_node.sh
```

Result:

```text
3 passed, 52 deselected
0
```

## Notes

- Removed `GPU_NODE_BRANCH` routing and forced the deploy check to compare the deploy ref against `origin/main`.
- Changed the old-host guard to probe Docker fail-closed and reject either `polymarket-rust-collector-gpu-probability-worker-1` or `polymarket-rust-collector-api-1` when active.
- Allowed only `absent`, `exited`, `dead`, and `removing` for the old host container state check.

## Task 3 Final Sequencing Fix: Old Writer Guard Before Helper Installs

## RED

I first encoded the expected sequencing failure explicitly in tests: old-writer guard text must come before both helper installs and startup. With the previous ordering, this test failed.

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
uv run -q pytest tests/scripts/test_deploy_script.py -k "gpu_node_deploy_script_guards_old_writer_before_startup"
```

Result:

```text
FAILED tests/scripts/test_deploy_script.py::test_gpu_node_deploy_script_guards_old_writer_before_startup
E       assert 7576 < 7497
```

## GREEN

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
uv run -q pytest tests/scripts/test_deploy_script.py -k "gpu_node_deploy_script"
bash -n scripts/deploy_gpu_node.sh
```

Result:

```text
9 passed, 47 deselected
0
```

## Notes

- Moved the old-runtime guard to run before `build_images_pc.sh`, `install_gpu_node_spoon_artifact_sync.sh`, and `install_gpu_node_runtime_keeper.sh`.
- Added/updated assertion in `tests/scripts/test_deploy_script.py` so `OLD_WRITER` guard ordering is validated against helper install and startup paths.
- Added a final worker-running smoke assertion in `deploy_gpu_node.sh` that fails deploy if `gpu-probability-worker` is not in a running service state after startup.

## Task 3 Latest Review Fix: Restore Branch Support and Brief Smoke Shape

## RED

Command:

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
.venv/bin/pytest tests/scripts/test_deploy_script.py -k 'gpu_node_deploy_script_targets_server2_native_linux_runtime or gpu_node_deploy_script_smoke_checks_gpu_probability_worker_running' -q
```

Result:

```text
FAILED tests/scripts/test_deploy_script.py::test_gpu_node_deploy_script_targets_server2_native_linux_runtime
FAILED tests/scripts/test_deploy_script.py::test_gpu_node_deploy_script_smoke_checks_gpu_probability_worker_running
```

## GREEN

Commands:

```bash
cd /Users/goon/polymarket-server2-cuda-sdd
.venv/bin/pytest tests/scripts/test_deploy_script.py -k 'gpu_node_deploy_script' -q
bash -n scripts/deploy_gpu_node.sh
```

Result:

```text
9 passed, 47 deselected in 0.01s
```

## Notes

- Restored `GPU_NODE_BRANCH` with default `main`, branch-aware local and remote fetches, and deploy-ref validation against `origin/$GPU_NODE_BRANCH`.
- Removed the immediate `gpu-probability-worker` running-state grep so the script ends with the brief's final `health` plus `docker compose ps` smoke shape.
