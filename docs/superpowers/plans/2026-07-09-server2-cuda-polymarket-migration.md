# Server2 CUDA Polymarket Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retire the unused `server2` Windows GPU VM path, reclaim its RTX 3060 Ti for Linux CUDA, and move the Polymarket GPU probability/API lane from the main desktop PC to `server2`.

**Architecture:** Keep Spoon as the single collector, normalizer, DuckDB, and live-input authority. Add a native Linux GPU-node deploy path for `server2` that runs only `api` and `gpu-probability-worker`, pulls Spoon-owned live artifacts into `/home/enoch/polymarket-data/live`, and writes server2-owned probability outputs. Preserve the existing THEPC/WSL scripts as legacy-compatible wrappers where practical, but document `server2` as the active GPU runtime.

**Tech Stack:** Bash, Docker Compose, NVIDIA Linux driver, NVIDIA Container Toolkit, libvirt/virsh, rsync over SSH, systemd user services, pytest, existing Polymarket deploy scripts and compose files.

## Global Constraints

- Live mode remains read-only first and paper-only; do not add real trading, private keys, or order placement.
- Do not run two collectors, two normalizers, or two probability writers against the same canonical output path.
- Spoon remains canonical for `status.json`, `normalized_health.json`, `probability_inputs.json`, `probability_fragments.json`, `outcomes.json`, and `volatility.json`.
- `server2` owns `probabilities.json`, `probability-events.jsonl`, and `cluster_status.server2.json` after migration.
- The main desktop PC GPU worker must be stopped before `server2` is declared the active probability writer.
- `win11-gaming` on `server2` may be destroyed only after backing up its libvirt XML and requiring an explicit confirmation environment variable.
- Existing unrelated dirty worktree changes must not be reverted.
- All code changes follow TDD: write the failing test, run it and see the expected failure, implement the smallest passing change, rerun the test.

---

## File Structure

- Create `scripts/install_gpu_node_spoon_artifact_sync.sh`
  - Generic native Linux artifact sync installer for GPU nodes.
  - Pulls Spoon-owned live inputs into `$POLYMARKET_DATA_DIR/live`.
  - Installs `polymarket-spoon-artifact-sync.service` as a user service when possible, with a nohup fallback.

- Modify `scripts/install_thepc_spoon_artifact_sync.sh`
  - Keep as a compatibility wrapper that delegates to `install_gpu_node_spoon_artifact_sync.sh`.
  - Preserve existing THEPC defaults: home `/home/ender`, data `/home/ender/polymarket-data`.

- Create `scripts/install_gpu_node_runtime_keeper.sh`
  - Native Linux runtime keeper installer for `server2`.
  - Installs `polymarket-runtime-keeper.service` as a user service.
  - No WSL, Windows Scheduled Task, or PowerShell paths.

- Modify `scripts/install_thepc_runtime_keeper.sh`
  - Leave WSL/Windows behavior intact for legacy THEPC.
  - Do not make this script the `server2` path.

- Create `scripts/deploy_gpu_node.sh`
  - Native Linux deploy script for a GPU API/probability host.
  - Defaults to `GPU_NODE_HOST=server2`, `GPU_NODE_REPO=/home/enoch/polymarket`, `GPU_NODE_DATA_DIR=/home/enoch/polymarket-data`, and `GPU_NODE_BIN_DIR=/home/enoch/bin`.
  - Fetches/pins GitHub `main`, builds or loads images, writes `.env`, installs artifact sync and runtime keeper, and starts only `api` and `gpu-probability-worker`.

- Create `scripts/prepare_server2_cuda_host.sh`
  - Idempotent host-prep script with dry-run default.
  - Backs up and destroys `win11-gaming`, removes VFIO GPU binding, installs/validates NVIDIA Docker runtime prerequisites, and requires reboot.

- Modify `deploy/cluster/cluster.local.example.json`
  - Add `server2` as the active `gpu_api` node.
  - Change GPU-owned output artifact owner/mirrors from `thepc` to `server2`.
  - Keep THEPC only if needed as an inactive/comment-free legacy node in docs, not as the active manifest target.

- Modify `docs/SPOON_DEPLOYMENT.md`
  - Replace active THEPC GPU runtime instructions with `server2` native Linux instructions.
  - Keep a short "Legacy THEPC WSL" section only for old recovery context.

- Modify `tests/scripts/test_deploy_script.py`
  - Add tests for `deploy_gpu_node.sh`, generic artifact sync, and server2 host-prep safety.
  - Adjust active runtime expectations away from hardcoded THEPC defaults where this migration intentionally changes them.

- Modify `tests/scripts/test_runtime_keeper_scripts.py`
  - Add tests for `install_gpu_node_runtime_keeper.sh`.
  - Keep existing THEPC WSL runtime keeper tests passing.

- Modify `tests/docs/test_active_runtime_docs.py`
  - Assert docs describe `server2` as the active CUDA runtime and `deploy_gpu_node.sh` as the active native Linux deploy path.

---

### Task 1: Generic GPU Node Artifact Sync

**Files:**
- Create: `scripts/install_gpu_node_spoon_artifact_sync.sh`
- Modify: `scripts/install_thepc_spoon_artifact_sync.sh`
- Test: `tests/scripts/test_deploy_script.py`

**Interfaces:**
- Consumes: remote Spoon alias from `POLYMARKET_SPOON_SSH_ALIAS`, default `spoon`.
- Consumes: data directory from `POLYMARKET_DATA_DIR`, default `$HOME/polymarket-data`.
- Produces: `$HOME/bin/polymarket-sync-spoon-artifacts.sh`.
- Produces: user service `polymarket-spoon-artifact-sync.service`.
- Produces: wrapper compatibility for `scripts/install_thepc_spoon_artifact_sync.sh`.

- [ ] **Step 1: Write the failing tests**

Append these tests to `tests/scripts/test_deploy_script.py`:

```python
def test_gpu_node_spoon_artifact_sync_installer_is_native_linux_and_role_safe() -> None:
    script = (ROOT / "scripts" / "install_gpu_node_spoon_artifact_sync.sh").read_text(
        encoding="utf-8"
    )

    assert 'DATA_DIR="${POLYMARKET_DATA_DIR:-$HOME/polymarket-data}"' in script
    assert 'BIN_DIR="${POLYMARKET_BIN_DIR:-$HOME/bin}"' in script
    assert 'SPOON_ALIAS="${POLYMARKET_SPOON_SSH_ALIAS:-spoon}"' in script
    assert 'src="$SPOON_ALIAS:/home/spoon/polymarket-data/live"' in script
    assert "status.json normalized_health.json probability_inputs.json probability_fragments.json outcomes.json volatility.json" in script
    assert "probabilities.json" not in script
    assert "probability-events.jsonl" not in script
    assert "cluster_status.thepc.json" not in script
    assert "cluster_status.server2.json" not in script
    assert "polymarket-spoon-artifact-sync.service" in script
    assert "systemctl --user enable --now polymarket-spoon-artifact-sync.service" in script
    assert "nohup bash -lc" in script
    assert "wsl.exe" not in script
    assert "powershell.exe" not in script


def test_thepc_artifact_sync_installer_delegates_to_generic_gpu_node_installer() -> None:
    script = (ROOT / "scripts" / "install_thepc_spoon_artifact_sync.sh").read_text(
        encoding="utf-8"
    )

    assert "install_gpu_node_spoon_artifact_sync.sh" in script
    assert 'export POLYMARKET_DATA_DIR="${POLYMARKET_DATA_DIR:-$HOME/polymarket-data}"' in script
    assert 'exec "$SCRIPT_DIR/install_gpu_node_spoon_artifact_sync.sh"' in script
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/scripts/test_deploy_script.py::test_gpu_node_spoon_artifact_sync_installer_is_native_linux_and_role_safe tests/scripts/test_deploy_script.py::test_thepc_artifact_sync_installer_delegates_to_generic_gpu_node_installer -q
```

Expected: FAIL because `scripts/install_gpu_node_spoon_artifact_sync.sh` does not exist and the THEPC installer does not delegate.

- [ ] **Step 3: Implement the generic artifact sync installer**

Create `scripts/install_gpu_node_spoon_artifact_sync.sh` with:

```bash
#!/usr/bin/env bash
set -euo pipefail

SPOON_HOSTNAME="${SPOON_HOSTNAME:-100.126.126.1}"
SPOON_USER="${SPOON_USER:-spoon}"
SPOON_ALIAS="${POLYMARKET_SPOON_SSH_ALIAS:-spoon}"
DATA_DIR="${POLYMARKET_DATA_DIR:-$HOME/polymarket-data}"
BIN_DIR="${POLYMARKET_BIN_DIR:-$HOME/bin}"
LIVE_DIR="$DATA_DIR/live"
LOG_DIR="$DATA_DIR/logs"
SYNC_SCRIPT="$BIN_DIR/polymarket-sync-spoon-artifacts.sh"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_PATH="$SERVICE_DIR/polymarket-spoon-artifact-sync.service"

mkdir -p "$BIN_DIR" "$LIVE_DIR" "$LOG_DIR" "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/config"
chmod 600 "$HOME/.ssh/config"

python3 - "$HOME/.ssh/config" "$SPOON_HOSTNAME" "$SPOON_USER" "$SPOON_ALIAS" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
hostname = sys.argv[2]
user = sys.argv[3]
alias = sys.argv[4]
text = path.read_text(encoding="utf-8") if path.exists() else ""
block = f"""
Host {alias}
  HostName {hostname}
  User {user}
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
"""
lines = text.splitlines()
out = []
skip = False
for line in lines:
    if line.strip().lower() == f"host {alias}".lower():
        skip = True
        continue
    if skip and line.startswith("Host "):
        skip = False
    if not skip:
        out.append(line)
path.write_text("\n".join(out).rstrip() + block + "\n", encoding="utf-8")
PY

cat > "$SYNC_SCRIPT" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

SPOON_ALIAS="${POLYMARKET_SPOON_SSH_ALIAS:-spoon}"
src="$SPOON_ALIAS:/home/spoon/polymarket-data/live"
dst="${POLYMARKET_DATA_DIR:-$HOME/polymarket-data}/live"
mkdir -p "$dst"
for file in status.json normalized_health.json probability_inputs.json probability_fragments.json outcomes.json volatility.json; do
  if ! rsync -az --delay-updates --partial --timeout=5 "$src/$file" "$dst/$file"; then
    printf 'artifact sync skipped %s\n' "$file" >&2
  fi
done
SH
chmod 755 "$SYNC_SCRIPT"

if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  mkdir -p "$SERVICE_DIR"
  cat > "$SERVICE_PATH" <<UNIT
[Unit]
Description=Polymarket Spoon artifact sync loop
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/bash -lc 'while true; do $SYNC_SCRIPT; sleep 1; done'
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now polymarket-spoon-artifact-sync.service
else
  if [ -f "$LIVE_DIR/artifact-sync.pid" ]; then
    old_pid="$(cat "$LIVE_DIR/artifact-sync.pid" || true)"
    if [ -n "$old_pid" ]; then
      kill "$old_pid" >/dev/null 2>&1 || true
    fi
  fi
  nohup bash -lc "while true; do $SYNC_SCRIPT; sleep 1; done" > "$LOG_DIR/artifact-sync.log" 2>&1 &
  echo "$!" > "$LIVE_DIR/artifact-sync.pid"
fi

"$SYNC_SCRIPT"
```

Replace `scripts/install_thepc_spoon_artifact_sync.sh` with this wrapper:

```bash
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export POLYMARKET_DATA_DIR="${POLYMARKET_DATA_DIR:-$HOME/polymarket-data}"
export POLYMARKET_BIN_DIR="${POLYMARKET_BIN_DIR:-$HOME/bin}"
exec "$SCRIPT_DIR/install_gpu_node_spoon_artifact_sync.sh"
```

Run:

```bash
chmod 755 scripts/install_gpu_node_spoon_artifact_sync.sh scripts/install_thepc_spoon_artifact_sync.sh
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/scripts/test_deploy_script.py::test_gpu_node_spoon_artifact_sync_installer_is_native_linux_and_role_safe tests/scripts/test_deploy_script.py::test_thepc_artifact_sync_installer_delegates_to_generic_gpu_node_installer -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/install_gpu_node_spoon_artifact_sync.sh scripts/install_thepc_spoon_artifact_sync.sh tests/scripts/test_deploy_script.py
git commit -m "feat: add native gpu node artifact sync"
```

---

### Task 2: Native GPU Node Runtime Keeper

**Files:**
- Create: `scripts/install_gpu_node_runtime_keeper.sh`
- Test: `tests/scripts/test_runtime_keeper_scripts.py`

**Interfaces:**
- Consumes: `POLYMARKET_REPO`, default `/home/enoch/polymarket`.
- Consumes: `POLYMARKET_DATA_DIR`, default `/home/enoch/polymarket-data`.
- Consumes: `POLYMARKET_BIN_DIR`, default `/home/enoch/bin`.
- Produces: `$POLYMARKET_BIN_DIR/polymarket-runtime-keeper-loop.sh`.
- Produces: user service `polymarket-runtime-keeper.service`.

- [ ] **Step 1: Write the failing test**

Append this test to `tests/scripts/test_runtime_keeper_scripts.py`:

```python
def test_gpu_node_runtime_keeper_installer_is_native_linux_without_wsl() -> None:
    script = (REPO / "scripts" / "install_gpu_node_runtime_keeper.sh").read_text(
        encoding="utf-8"
    )

    assert 'REPO="${POLYMARKET_REPO:-/home/enoch/polymarket}"' in script
    assert 'DATA_DIR="${POLYMARKET_DATA_DIR:-/home/enoch/polymarket-data}"' in script
    assert 'BIN_DIR="${POLYMARKET_BIN_DIR:-/home/enoch/bin}"' in script
    assert 'exec "$ENGINE_BIN" runtime-keeper' in script
    assert '--compose-file "$REPO/deploy/collector/docker-compose.yml"' in script
    assert '--compose-file "$REPO/deploy/collector/docker-compose.thepc-gpu-api.yml"' in script
    assert '--required-service "api"' in script
    assert '--required-service "gpu-probability-worker"' in script
    assert '--loop-interval-seconds 30' in script
    assert "polymarket-runtime-keeper.service" in script
    assert "systemctl --user enable --now polymarket-runtime-keeper.service" in script
    assert "wsl.exe" not in script
    assert "powershell.exe" not in script
    assert "Register-ScheduledTask" not in script
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/scripts/test_runtime_keeper_scripts.py::test_gpu_node_runtime_keeper_installer_is_native_linux_without_wsl -q
```

Expected: FAIL because `scripts/install_gpu_node_runtime_keeper.sh` does not exist.

- [ ] **Step 3: Implement the native runtime keeper installer**

Create `scripts/install_gpu_node_runtime_keeper.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO="${POLYMARKET_REPO:-/home/enoch/polymarket}"
DATA_DIR="${POLYMARKET_DATA_DIR:-/home/enoch/polymarket-data}"
BIN_DIR="${POLYMARKET_BIN_DIR:-/home/enoch/bin}"
LOOP_SCRIPT="$BIN_DIR/polymarket-runtime-keeper-loop.sh"
SERVICE_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
SERVICE_PATH="$SERVICE_DIR/polymarket-runtime-keeper.service"

mkdir -p "$BIN_DIR" "$DATA_DIR/live" "$SERVICE_DIR"

cd "$REPO"
python3 -m pip install --user --break-system-packages -e "$REPO"

cat > "$LOOP_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export PATH="\$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:\$PATH"
cd "$REPO"
ENGINE_BIN="\${POLYMARKET_ENGINE_BIN:-\$HOME/.local/bin/polymarket-engine}"
if [ ! -x "\$ENGINE_BIN" ]; then
  ENGINE_BIN="polymarket-engine"
fi
exec "\$ENGINE_BIN" runtime-keeper \\
  --repo "$REPO" \\
  --data-dir "$DATA_DIR" \\
  --api-base-url "http://127.0.0.1:8000" \\
  --compose-file "$REPO/deploy/collector/docker-compose.yml" \\
  --compose-file "$REPO/deploy/collector/docker-compose.thepc-gpu-api.yml" \\
  --required-service "api" \\
  --required-service "gpu-probability-worker" \\
  --recovery-warmup-min-seconds 15 \\
  --recovery-required-healthy-cycles 1 \\
  --loop \\
  --loop-interval-seconds 30
EOF
chmod 755 "$LOOP_SCRIPT"

cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=Polymarket runtime keeper loop
After=default.target

[Service]
Type=simple
ExecStart=$LOOP_SCRIPT
Restart=always
RestartSec=15

[Install]
WantedBy=default.target
EOF

if command -v systemctl >/dev/null 2>&1 && systemctl --user status >/dev/null 2>&1; then
  systemctl --user daemon-reload
  systemctl --user enable --now polymarket-runtime-keeper.service
else
  echo "systemd user service unavailable; start $LOOP_SCRIPT manually or enable linger for this user" >&2
  exit 1
fi

echo "Installed $LOOP_SCRIPT"
echo "Installed $SERVICE_PATH"
```

Run:

```bash
chmod 755 scripts/install_gpu_node_runtime_keeper.sh
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/scripts/test_runtime_keeper_scripts.py::test_gpu_node_runtime_keeper_installer_is_native_linux_without_wsl -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/install_gpu_node_runtime_keeper.sh tests/scripts/test_runtime_keeper_scripts.py
git commit -m "feat: add native gpu node runtime keeper"
```

---

### Task 3: Native `server2` GPU Deploy Script

**Files:**
- Create: `scripts/deploy_gpu_node.sh`
- Test: `tests/scripts/test_deploy_script.py`

**Interfaces:**
- Consumes: clean pushed `main`, same as `scripts/deploy_pc.sh`.
- Consumes: `GPU_NODE_HOST`, default `server2`.
- Consumes: `GPU_NODE_REPO`, default `/home/enoch/polymarket`.
- Consumes: `GPU_NODE_DATA_DIR`, default `/home/enoch/polymarket-data`.
- Produces: running `api` and `gpu-probability-worker` containers on `server2`.
- Produces: no collector, normalizer, or outcome sidecar on `server2`.

- [ ] **Step 1: Write the failing test**

Append this test to `tests/scripts/test_deploy_script.py`:

```python
def test_gpu_node_deploy_script_targets_server2_native_linux_runtime() -> None:
    script = (ROOT / "scripts" / "deploy_gpu_node.sh").read_text(encoding="utf-8")

    assert 'GPU_NODE_HOST="${GPU_NODE_HOST:-server2}"' in script
    assert 'GPU_NODE_REPO="${GPU_NODE_REPO:-/home/enoch/polymarket}"' in script
    assert 'GPU_NODE_DATA_DIR="${GPU_NODE_DATA_DIR:-/home/enoch/polymarket-data}"' in script
    assert 'GPU_NODE_BIN_DIR="${GPU_NODE_BIN_DIR:-/home/enoch/bin}"' in script
    assert 'GPU_NODE_DEPLOY_ROLE="${GPU_NODE_DEPLOY_ROLE:-server2-gpu-api}"' in script
    assert 'GPU_NODE_REMOTE_BUILD_SAVE_TARS="${GPU_NODE_REMOTE_BUILD_SAVE_TARS:-0}"' in script
    assert "server2 native Linux will fetch GitHub main and build images locally" in script
    assert "wsl.exe" not in script
    assert "powershell.exe" not in script
    assert 'git clone "$GPU_NODE_GIT_REMOTE" "$GPU_NODE_REPO"' in script
    assert 'set_env POLYMARKET_DATA_DIR "$GPU_NODE_DATA_DIR" deploy/collector/.env' in script
    assert 'set_env POLYMARKET_CUDA_PROBABILITY_IMAGE "$CUDA_PROBABILITY_IMAGE" deploy/collector/.env' in script
    assert "./scripts/install_gpu_node_spoon_artifact_sync.sh" in script
    assert "./scripts/install_gpu_node_runtime_keeper.sh" in script
    assert "stop collector normalizer outcome-refresh" in script
    assert "up -d --no-build api gpu-probability-worker" in script
    assert "docker compose --env-file deploy/collector/.env" in script
    assert "-f deploy/collector/docker-compose.thepc-gpu-api.yml" in script
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/scripts/test_deploy_script.py::test_gpu_node_deploy_script_targets_server2_native_linux_runtime -q
```

Expected: FAIL because `scripts/deploy_gpu_node.sh` does not exist.

- [ ] **Step 3: Implement `scripts/deploy_gpu_node.sh`**

Create `scripts/deploy_gpu_node.sh` by copying the native parts of `scripts/deploy_pc.sh`, then apply these exact interface changes:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_REF="${POLYMARKET_DEPLOY_REF:-HEAD}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"

GPU_NODE_HOST="${GPU_NODE_HOST:-server2}"
GPU_NODE_REPO="${GPU_NODE_REPO:-/home/enoch/polymarket}"
GPU_NODE_GIT_REMOTE="${GPU_NODE_GIT_REMOTE:-git@github.com:AnimeWeeb9000/polymarket.git}"
GPU_NODE_DATA_DIR="${GPU_NODE_DATA_DIR:-/home/enoch/polymarket-data}"
GPU_NODE_BIN_DIR="${GPU_NODE_BIN_DIR:-/home/enoch/bin}"
GPU_NODE_DIST_DIR="${GPU_NODE_DIST_DIR:-/home/enoch/polymarket-image-artifacts}"
GPU_NODE_DEPLOY_ROLE="${GPU_NODE_DEPLOY_ROLE:-server2-gpu-api}"
GPU_NODE_PROBABILITY_CPU_TARGET_PERCENT="${GPU_NODE_PROBABILITY_CPU_TARGET_PERCENT:-15.0}"
GPU_NODE_PROBABILITY_CPU_SOFT_MAX_PERCENT="${GPU_NODE_PROBABILITY_CPU_SOFT_MAX_PERCENT:-20.0}"
GPU_NODE_PROBABILITY_MAX_CYCLE_RUNTIME_MS="${GPU_NODE_PROBABILITY_MAX_CYCLE_RUNTIME_MS:-10000}"
GPU_NODE_PROBABILITY_MAX_TOTAL_PATHS="${GPU_NODE_PROBABILITY_MAX_TOTAL_PATHS:-10000}"
GPU_NODE_PROBABILITY_MIN_TOTAL_PATHS="${GPU_NODE_PROBABILITY_MIN_TOTAL_PATHS:-2000}"
GPU_NODE_ENABLE_LIVE_PRIOR_FRAGMENTS="${GPU_NODE_ENABLE_LIVE_PRIOR_FRAGMENTS:-0}"
GPU_NODE_GPU_WORKER_MEM_LIMIT="${GPU_NODE_GPU_WORKER_MEM_LIMIT:-1536m}"
GPU_NODE_API_PORT="${GPU_NODE_API_PORT:-8000}"
GPU_NODE_REMOTE_BUILD_SAVE_TARS="${GPU_NODE_REMOTE_BUILD_SAVE_TARS:-0}"
GPU_NODE_BRANCH="${GPU_NODE_BRANCH:-main}"
```

The body must keep these behaviors from `deploy_pc.sh`:

```bash
git -C "$ROOT" diff --quiet
git -C "$ROOT" diff --cached --quiet
test -z "$(git -C "$ROOT" ls-files --others --exclude-standard)"
git -C "$ROOT" fetch --quiet origin main
```

Inside the remote SSH heredoc, use native Linux commands only:

```bash
mkdir -p "$GPU_NODE_DATA_DIR/raw" "$GPU_NODE_DATA_DIR/db" "$GPU_NODE_DATA_DIR/live" "$GPU_NODE_DATA_DIR/live/bug-reports" "$GPU_NODE_DATA_DIR/logs" "$GPU_NODE_DIST_DIR" "$GPU_NODE_BIN_DIR"
touch "$GPU_NODE_DATA_DIR/raw/.polymarket_archive_root"

if [ ! -d "$GPU_NODE_REPO/.git" ]; then
  git clone "$GPU_NODE_GIT_REMOTE" "$GPU_NODE_REPO"
fi

cd "$GPU_NODE_REPO"
git fetch --quiet origin "$GPU_NODE_BRANCH"
git checkout --quiet "$FULL_SHA"

cp deploy/collector/.env.example deploy/collector/.env
set_env POLYMARKET_UID "$(id -u)" deploy/collector/.env
set_env POLYMARKET_GID "$(id -g)" deploy/collector/.env
set_env POLYMARKET_DATA_DIR "$GPU_NODE_DATA_DIR" deploy/collector/.env
set_env POLYMARKET_CUDA_PROBABILITY_IMAGE "$CUDA_PROBABILITY_IMAGE" deploy/collector/.env
set_env POLYMARKET_ENABLE_RUNTIME_PROBABILITIES 1 deploy/collector/.env
set_env POLYMARKET_ALLOW_RUNTIME_PROBABILITY_COMPUTE 0 deploy/collector/.env
set_env POLYMARKET_PROBABILITY_MAX_CYCLE_RUNTIME_MS "$GPU_NODE_PROBABILITY_MAX_CYCLE_RUNTIME_MS" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS "$GPU_NODE_PROBABILITY_MAX_TOTAL_PATHS" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS "$GPU_NODE_PROBABILITY_MIN_TOTAL_PATHS" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_CPU_TARGET_PERCENT "$GPU_NODE_PROBABILITY_CPU_TARGET_PERCENT" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT "$GPU_NODE_PROBABILITY_CPU_SOFT_MAX_PERCENT" deploy/collector/.env
set_env POLYMARKET_GPU_WORKER_MEM_LIMIT "$GPU_NODE_GPU_WORKER_MEM_LIMIT" deploy/collector/.env
set_env POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS "$GPU_NODE_ENABLE_LIVE_PRIOR_FRAGMENTS" deploy/collector/.env
set_env POLYMARKET_API_PORT "$GPU_NODE_API_PORT" deploy/collector/.env

POLYMARKET_BUILD_SAVE_TARS="$GPU_NODE_REMOTE_BUILD_SAVE_TARS" POLYMARKET_DEPLOY_REF="$FULL_SHA" ./scripts/build_images_pc.sh

./scripts/install_gpu_node_spoon_artifact_sync.sh
./scripts/install_gpu_node_runtime_keeper.sh

docker compose --env-file deploy/collector/.env \
  -f deploy/collector/docker-compose.yml \
  -f deploy/collector/docker-compose.thepc-gpu-api.yml \
  stop collector normalizer outcome-refresh >/dev/null 2>&1 || true

docker compose --env-file deploy/collector/.env \
  -f deploy/collector/docker-compose.yml \
  -f deploy/collector/docker-compose.thepc-gpu-api.yml \
  up -d --no-build api gpu-probability-worker
```

The script must end with these smoke checks:

```bash
ssh "$GPU_NODE_HOST" "curl -fsS http://127.0.0.1:$GPU_NODE_API_PORT/health >/dev/null"
ssh "$GPU_NODE_HOST" "cd $GPU_NODE_REPO && docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml -f deploy/collector/docker-compose.thepc-gpu-api.yml ps"
```

Run:

```bash
chmod 755 scripts/deploy_gpu_node.sh
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/scripts/test_deploy_script.py::test_gpu_node_deploy_script_targets_server2_native_linux_runtime -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/deploy_gpu_node.sh tests/scripts/test_deploy_script.py
git commit -m "feat: add server2 gpu deploy path"
```

---

### Task 4: Safe `server2` VM Retirement And CUDA Host Prep

**Files:**
- Create: `scripts/prepare_server2_cuda_host.sh`
- Test: `tests/scripts/test_deploy_script.py`

**Interfaces:**
- Consumes: root execution on `server2`.
- Consumes: environment variable `CONFIRM_DESTROY_WIN11_VM=destroy-win11-gaming` for destructive execution.
- Produces: backup directory `/root/server2-vm-retirement`.
- Produces: VFIO config backup under `/root/server2-vm-retirement/vfio-passthrough.conf.backup` when present.
- Produces: `NEEDS_REBOOT=1` marker in output after changing driver binding.

- [ ] **Step 1: Write the failing test**

Append this test to `tests/scripts/test_deploy_script.py`:

```python
def test_prepare_server2_cuda_host_is_dry_run_first_and_requires_destroy_confirmation() -> None:
    script = (ROOT / "scripts" / "prepare_server2_cuda_host.sh").read_text(
        encoding="utf-8"
    )

    assert 'MODE="${1:---dry-run}"' in script
    assert 'if [ "$MODE" != "--execute" ]; then' in script
    assert 'CONFIRM_DESTROY_WIN11_VM="${CONFIRM_DESTROY_WIN11_VM:-}"' in script
    assert 'destroy-win11-gaming' in script
    assert 'virsh dumpxml win11-gaming > "$BACKUP_DIR/win11-gaming.xml"' in script
    assert 'virsh undefine win11-gaming --nvram --remove-all-storage' in script
    assert 'mv /etc/modprobe.d/vfio-passthrough.conf "$BACKUP_DIR/vfio-passthrough.conf.backup"' in script
    assert "update-initramfs -u" in script
    assert "ubuntu-drivers install" in script
    assert "nvidia-container-toolkit" in script
    assert "NEEDS_REBOOT=1" in script
    assert "reboot" not in script
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/scripts/test_deploy_script.py::test_prepare_server2_cuda_host_is_dry_run_first_and_requires_destroy_confirmation -q
```

Expected: FAIL because `scripts/prepare_server2_cuda_host.sh` does not exist.

- [ ] **Step 3: Implement the host-prep script**

Create `scripts/prepare_server2_cuda_host.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

MODE="${1:---dry-run}"
CONFIRM_DESTROY_WIN11_VM="${CONFIRM_DESTROY_WIN11_VM:-}"
BACKUP_DIR="/root/server2-vm-retirement"
VFIO_CONF="/etc/modprobe.d/vfio-passthrough.conf"

run() {
  if [ "$MODE" = "--execute" ]; then
    "$@"
  else
    printf '[dry-run] %q' "$1"
    shift
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
  fi
}

if [ "$(id -u)" != "0" ]; then
  echo "run as root on server2" >&2
  exit 1
fi

host="$(hostname)"
if [ "$host" != "docker" ] && [ "$host" != "server2" ]; then
  echo "refusing to run on unexpected host: $host" >&2
  exit 1
fi

if [ "$MODE" != "--dry-run" ] && [ "$MODE" != "--execute" ]; then
  echo "usage: $0 [--dry-run|--execute]" >&2
  exit 2
fi

if virsh dominfo win11-gaming >/dev/null 2>&1; then
  state="$(virsh domstate win11-gaming | tr -d '\r')"
  if [ "$state" != "shut off" ]; then
    echo "win11-gaming must be shut off before retirement; current state: $state" >&2
    exit 1
  fi
  if [ "$MODE" = "--execute" ] && [ "$CONFIRM_DESTROY_WIN11_VM" != "destroy-win11-gaming" ]; then
    echo "set CONFIRM_DESTROY_WIN11_VM=destroy-win11-gaming to destroy the VM" >&2
    exit 1
  fi
  run mkdir -p "$BACKUP_DIR"
  if [ "$MODE" = "--execute" ]; then
    virsh dumpxml win11-gaming > "$BACKUP_DIR/win11-gaming.xml"
  else
    echo '[dry-run] virsh dumpxml win11-gaming > "$BACKUP_DIR/win11-gaming.xml"'
  fi
  run virsh undefine win11-gaming --nvram --remove-all-storage
fi

run mkdir -p "$BACKUP_DIR"
if [ -f "$VFIO_CONF" ]; then
  run mv /etc/modprobe.d/vfio-passthrough.conf "$BACKUP_DIR/vfio-passthrough.conf.backup"
fi

run update-initramfs -u
run apt-get update
run ubuntu-drivers install
run apt-get install -y nvidia-container-toolkit
run nvidia-ctk runtime configure --runtime=docker
run systemctl restart docker

echo "NEEDS_REBOOT=1"
```

Run:

```bash
chmod 755 scripts/prepare_server2_cuda_host.sh
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/scripts/test_deploy_script.py::test_prepare_server2_cuda_host_is_dry_run_first_and_requires_destroy_confirmation -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/prepare_server2_cuda_host.sh tests/scripts/test_deploy_script.py
git commit -m "feat: add server2 cuda host prep"
```

---

### Task 5: Cluster Manifest And Docs Move Active GPU Runtime To `server2`

**Files:**
- Modify: `deploy/cluster/cluster.local.example.json`
- Modify: `docs/SPOON_DEPLOYMENT.md`
- Modify: `tests/docs/test_active_runtime_docs.py`
- Modify: `tests/scripts/test_deploy_script.py`

**Interfaces:**
- Consumes: `server2` node identity from the SSH alias and Linux host.
- Produces: active manifest target `server2`.
- Produces: docs that tell operators to run `./scripts/deploy_gpu_node.sh`.

- [ ] **Step 1: Write failing docs and manifest tests**

Append this test to `tests/scripts/test_deploy_script.py`:

```python
def test_cluster_manifest_declares_server2_as_active_gpu_probability_owner() -> None:
    manifest = (ROOT / "deploy" / "cluster" / "cluster.local.example.json").read_text(
        encoding="utf-8"
    )

    assert '"server2": {' in manifest
    assert '"host": "server2"' in manifest
    assert '"role": "gpu_api"' in manifest
    assert '"owner": "server2"' in manifest
    assert '"/home/enoch/polymarket-data/live/probabilities.json"' in manifest
    assert '"/home/spoon/polymarket-data/live/probabilities.server2.json"' in manifest
    assert '"target_node": "server2"' in manifest
```

Add this test to `tests/docs/test_active_runtime_docs.py`:

```python
def test_spoon_docs_mark_server2_as_active_cuda_runtime_path() -> None:
    text = (ROOT / "docs" / "SPOON_DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "server2 is the active API/GPU runtime host" in text
    assert "./scripts/deploy_gpu_node.sh" in text
    assert "/home/enoch/polymarket-data/live" in text
    assert "generic spoon deploy path does not start gpu-probability-worker" in text
    assert "Legacy THEPC WSL" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/scripts/test_deploy_script.py::test_cluster_manifest_declares_server2_as_active_gpu_probability_owner tests/docs/test_active_runtime_docs.py::test_spoon_docs_mark_server2_as_active_cuda_runtime_path -q
```

Expected: FAIL because docs and manifest still describe THEPC as active.

- [ ] **Step 3: Update manifest**

In `deploy/cluster/cluster.local.example.json`, change the active GPU node and output owners to:

```json
{
  "schema_version": "polymarket-cluster-manifest-v1",
  "nodes": {
    "spoon": {
      "host": "spoon",
      "role": "cpu_authority"
    },
    "server2": {
      "host": "server2",
      "role": "gpu_api"
    }
  },
  "artifacts": {
    "status.json": {
      "owner": "spoon",
      "canonical_path": "/home/spoon/polymarket-data/live/status.json",
      "mirrors": {
        "server2": "/home/enoch/polymarket-data/live/status.json"
      }
    },
    "normalized_health.json": {
      "owner": "spoon",
      "canonical_path": "/home/spoon/polymarket-data/live/normalized_health.json",
      "mirrors": {
        "server2": "/home/enoch/polymarket-data/live/normalized_health.json"
      }
    },
    "probability_inputs.json": {
      "owner": "spoon",
      "canonical_path": "/home/spoon/polymarket-data/live/probability_inputs.json",
      "mirrors": {
        "server2": "/home/enoch/polymarket-data/live/probability_inputs.json"
      }
    },
    "probability_fragments.json": {
      "owner": "spoon",
      "canonical_path": "/home/spoon/polymarket-data/live/probability_fragments.json",
      "mirrors": {
        "server2": "/home/enoch/polymarket-data/live/probability_fragments.json"
      }
    },
    "outcomes.json": {
      "owner": "spoon",
      "canonical_path": "/home/spoon/polymarket-data/live/outcomes.json",
      "mirrors": {
        "server2": "/home/enoch/polymarket-data/live/outcomes.json"
      }
    },
    "volatility.json": {
      "owner": "spoon",
      "canonical_path": "/home/spoon/polymarket-data/live/volatility.json",
      "mirrors": {
        "server2": "/home/enoch/polymarket-data/live/volatility.json"
      }
    },
    "probabilities.json": {
      "owner": "server2",
      "canonical_path": "/home/enoch/polymarket-data/live/probabilities.json",
      "mirrors": {
        "spoon": "/home/spoon/polymarket-data/live/probabilities.server2.json"
      }
    },
    "probability-events.jsonl": {
      "owner": "server2",
      "canonical_path": "/home/enoch/polymarket-data/live/probability-events.jsonl",
      "mirrors": {
        "spoon": "/home/spoon/polymarket-data/live/probability-events.server2.jsonl"
      }
    },
    "cluster_status.server2.json": {
      "owner": "server2",
      "canonical_path": "/home/enoch/polymarket-data/live/cluster_status.server2.json",
      "mirrors": {
        "spoon": "/home/spoon/polymarket-data/live/cluster_status.server2.json"
      }
    }
  },
  "mirror": {
    "source_node": "spoon",
    "target_node": "server2",
    "max_age_seconds": 5.0
  }
}
```

- [ ] **Step 4: Update docs**

In `docs/SPOON_DEPLOYMENT.md`, replace the active "THEPC Deploy" section with:

```markdown
### server2 GPU Deploy

server2 is the active API/GPU runtime host, not the collector authority. It reads
Spoon-owned live artifacts from `/home/enoch/polymarket-data/live`, runs the
FastAPI API and `gpu-probability-worker`, and serves the browser/TUI API
surface. Do not use blind auto-pull for this lane. The Mac pushes `main`;
server2 fetches `git@github.com:AnimeWeeb9000/polymarket.git`, checks out the
exact pushed SHA, then builds and restarts from that checkout. Do not deploy
local-only commits. `./scripts/deploy_gpu_node.sh` is the supported native Linux
CUDA runtime deployment path; the generic spoon deploy path does not start
gpu-probability-worker by default.

```bash
cd /Users/goon/polymarket
./scripts/deploy_gpu_node.sh
```

Defaults:

- `GPU_NODE_HOST=server2`
- `GPU_NODE_REPO=/home/enoch/polymarket`
- `GPU_NODE_GIT_REMOTE=git@github.com:AnimeWeeb9000/polymarket.git`
- `GPU_NODE_DATA_DIR=/home/enoch/polymarket-data`
- `GPU_NODE_BIN_DIR=/home/enoch/bin`
- `GPU_NODE_DEPLOY_ROLE=server2-gpu-api`
```

Add this later in the same document:

```markdown
### Legacy THEPC WSL

THEPC was the previous API/GPU runtime host. Keep `scripts/deploy_pc.sh` and the
THEPC runtime keeper scripts for historical recovery, but do not run them as the
active probability writer while server2 owns CUDA probabilities.
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/scripts/test_deploy_script.py::test_cluster_manifest_declares_server2_as_active_gpu_probability_owner tests/docs/test_active_runtime_docs.py::test_spoon_docs_mark_server2_as_active_cuda_runtime_path -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add deploy/cluster/cluster.local.example.json docs/SPOON_DEPLOYMENT.md tests/docs/test_active_runtime_docs.py tests/scripts/test_deploy_script.py
git commit -m "docs: mark server2 as active gpu runtime"
```

---

### Task 6: Retire VM, Reclaim NVIDIA, Deploy, And Verify Live Runtime

**Files:**
- Runtime only: `server2`, main desktop PC, Spoon.
- No repository file edits in this task.

**Interfaces:**
- Consumes: committed tasks 1 through 5 pushed to `origin/main`.
- Produces: `server2` Linux host with NVIDIA driver and NVIDIA Container Toolkit active.
- Produces: `server2` Docker containers `api` and `gpu-probability-worker`.
- Produces: main desktop PC with old `gpu-probability-worker` stopped.

- [ ] **Step 1: Push committed repo changes**

Run:

```bash
cd /Users/goon/polymarket
git status --short
git push origin main
```

Expected: only unrelated pre-existing dirty files remain uncommitted, or the worktree is clean; pushed commits are on `origin/main`.

- [ ] **Step 2: Dry-run server2 CUDA host prep**

Run:

```bash
cd /Users/goon/polymarket
scp scripts/prepare_server2_cuda_host.sh server2:/tmp/prepare_server2_cuda_host.sh
ssh server2 'sudo bash /tmp/prepare_server2_cuda_host.sh --dry-run'
```

Expected: printed dry-run commands include backing up `win11-gaming`, removing VFIO config, installing drivers/toolkit, and `NEEDS_REBOOT=1`. No VM or config is changed.

- [ ] **Step 3: Execute server2 CUDA host prep**

Run:

```bash
ssh server2 'sudo CONFIRM_DESTROY_WIN11_VM=destroy-win11-gaming bash /tmp/prepare_server2_cuda_host.sh --execute'
```

Expected: `win11-gaming` XML is backed up under `/root/server2-vm-retirement/win11-gaming.xml`, VM storage is removed, VFIO config is moved to `/root/server2-vm-retirement/vfio-passthrough.conf.backup`, initramfs updates, NVIDIA packages install, Docker runtime config updates, and output includes `NEEDS_REBOOT=1`.

- [ ] **Step 4: Reboot server2**

Run:

```bash
ssh server2 'sudo reboot'
```

Wait for it to return:

```bash
until ssh -o ConnectTimeout=5 server2 'hostname; uptime'; do sleep 5; done
```

Expected: SSH returns `docker` or `server2` hostname and fresh uptime.

- [ ] **Step 5: Verify NVIDIA and Docker GPU runtime**

Run:

```bash
ssh server2 'nvidia-smi'
ssh server2 'docker run --rm --gpus all nvidia/cuda:13.2.1-base-ubuntu24.04 nvidia-smi'
```

Expected: both commands show the RTX 3060 Ti. If the CUDA image tag is unavailable, run `docker run --rm --gpus all nvidia/cuda:12.6.3-base-ubuntu24.04 nvidia-smi` and record the working tag in `docs/SPOON_DEPLOYMENT.md` before continuing.

- [ ] **Step 6: Stop old main PC GPU writer**

Run:

```bash
ssh spoon@100.100.109.27 'docker compose --env-file /home/spoon/polymarket/deploy/collector/.env -f /home/spoon/polymarket/deploy/collector/docker-compose.yml -f /home/spoon/polymarket/deploy/collector/docker-compose.thepc-gpu-api.yml stop gpu-probability-worker api || true'
```

Expected: old main PC `gpu-probability-worker` and `api` are stopped. Do not stop browser, desktop, Steam, Lunar, or unrelated containers.

- [ ] **Step 7: Deploy to server2**

Run:

```bash
cd /Users/goon/polymarket
./scripts/deploy_gpu_node.sh
```

Expected: server2 fetches the pushed SHA, builds images, installs sync and runtime keeper, starts `api` and `gpu-probability-worker`, and `curl http://127.0.0.1:8000/health` succeeds on server2.

- [ ] **Step 8: Verify live probabilities on server2**

Run:

```bash
ssh server2 "curl -sS 'http://127.0.0.1:8000/api/runtime/live?limit=8' | jq '{status, recovery, offload, probability_state: .probability.state}'"
ssh server2 "curl -sS 'http://127.0.0.1:8000/api/runtime/offload' | jq '{offload_allowed, reason_codes, mc_eligible_input_count, blocked_input_count, max_input_state_lag_ms}'"
ssh server2 "curl -sS 'http://127.0.0.1:8000/api/runtime/probabilities?limit=8' | jq '{state, lanes, rows_written, budget, offload, first_row: .rows[0]}'"
ssh server2 'docker logs --tail=80 polymarket-rust-collector-gpu-probability-worker-1'
```

Expected: probabilities are fresh, `gpu-probability-worker` logs do not show CUDA import/runtime errors, and no `no_probability_inputs` gate persists after artifact sync has had at least 10 seconds.

- [ ] **Step 9: Verify Spoon still owns collector health**

Run:

```bash
ssh spoon 'python3 /home/spoon/polymarket/scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json --max-status-age-seconds 30 --max-price-age-ms 30000 --max-orderbook-age-ms 30000 --max-websocket-event-age-ms 30000 --raw-root /home/spoon/polymarket-data/raw --max-raw-event-age-ms 30000 --normalized-health-path /home/spoon/polymarket-data/live/normalized_health.json --max-normalized-health-age-ms 90000 --expected-prewarm-windows 2'
```

Expected: collector status check exits 0.

- [ ] **Step 10: Commit live-operation notes if docs changed during verification**

If Step 5 required a different CUDA validation image tag or Step 8 exposed a useful server2-specific note, update `docs/SPOON_DEPLOYMENT.md`, then run:

```bash
cd /Users/goon/polymarket
uv run pytest tests/docs/test_active_runtime_docs.py tests/scripts/test_deploy_script.py tests/scripts/test_runtime_keeper_scripts.py -q
git add docs/SPOON_DEPLOYMENT.md
git commit -m "docs: record server2 cuda runtime verification"
git push origin main
```

Expected: tests pass and docs changes are pushed.

---

## Self-Review

**Spec coverage:** The plan retires the `server2` Windows VM path, reclaims its RTX 3060 Ti for Linux CUDA, adds a native `server2` GPU deploy path, keeps Spoon as the CPU/data authority, stops the main PC writer before server2 writes probabilities, and includes live verification commands.

**Placeholder scan:** No `TBD`, `TODO`, or "implement later" placeholders remain. Every code task includes concrete test code, commands, and expected outcomes.

**Type and name consistency:** The plan consistently uses `server2`, `/home/enoch/polymarket`, `/home/enoch/polymarket-data`, `install_gpu_node_spoon_artifact_sync.sh`, `install_gpu_node_runtime_keeper.sh`, `deploy_gpu_node.sh`, and `cluster_status.server2.json`.
