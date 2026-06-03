# Move Polymarket Runtime To PC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the active Polymarket collector/normalizer runtime and its data from `spoon` to the Windows PC, then decommission Polymarket runtime assets on `spoon` only after the PC runtime is verified stable.

**Architecture:** Treat this as a cutover with rollback, not a deletion-first cleanup. Prepare the PC as the new Docker/Linux runtime host, copy data from `spoon`, start the PC collector from the copied data, verify status freshness and normalized health, keep `spoon` stopped but intact for a rollback window, then remove containers/images/artifacts from `spoon` after explicit approval.

**Tech Stack:** Windows 11 Pro PC over Tailscale SSH, WSL2 Ubuntu, Docker Desktop Linux containers, Docker Compose, Tailscale, rsync/tar over SSH, Python health gates, existing `deploy/collector/docker-compose.yml`.

---

## Current Facts From 2026-06-03 Read-Only Checks

- PC SSH works over Tailscale: `ssh ender@100.72.104.49`.
- PC identity: `THEPC`, user `ender`, Windows 11 Pro.
- PC disk: `C:` has about `229 GB` free.
- PC has Git installed.
- PC has WSL installed but no Linux distribution.
- PC does not have `docker` on PATH yet.
- `spoon` data size: `/home/spoon/polymarket-data` is about `12G`.
- `spoon` image artifact dir: `/home/spoon/polymarket-image-artifacts` is about `206M`.
- `spoon` Docker images total about `35.84GB`; Docker build cache about `48.5GB`.
- `spoon` normalizer container is up and healthy.
- `spoon` collector container is up but Docker-healthcheck unhealthy because price rows were stale at inspection time.

## Non-Negotiable Safety Rules

- Do not run two active collectors writing the same logical stream at the same time.
- Do not delete `/home/spoon/polymarket-data` until the PC runtime has run cleanly and Enoch explicitly approves deletion.
- Do not delete the `spoon` repo checkout until the PC has a working clone, verified health, and rollback is no longer needed.
- Keep all work read-only/paper-first. No private keys, order placement, or live trading paths.
- Preserve raw journals before compacting, pruning, or deleting anything.

---

### Task 1: Prepare The PC Runtime Host

**Files/Hosts:**
- Host: `THEPC` over `ssh ender@100.72.104.49`
- Create on PC/WSL: Linux workspace for repo and data
- No repo files changed

- [ ] **Step 1: Install WSL Ubuntu on the PC**

Run in elevated PowerShell on the PC:

```powershell
wsl --install -d Ubuntu
```

Expected: Ubuntu installs. If Windows asks for reboot, reboot the PC and reconnect via:

```bash
ssh ender@100.72.104.49
```

- [ ] **Step 2: Install Docker Desktop or Docker Engine for WSL**

Recommended first path: Docker Desktop with WSL2 backend and Ubuntu integration enabled.

Verify from Windows PowerShell:

```powershell
docker version
docker compose version
```

Verify from WSL Ubuntu:

```bash
docker version
docker compose version
```

Expected: both commands work from WSL. If Docker only works from Windows but not WSL, enable Docker Desktop WSL integration for Ubuntu.

- [ ] **Step 3: Create PC directories**

Run from WSL Ubuntu:

```bash
mkdir -p ~/polymarket ~/polymarket-data/{raw,db,live,logs} ~/polymarket-image-artifacts
touch ~/polymarket-data/raw/.polymarket_archive_root
```

Expected: directories exist inside WSL, not inside the slower Windows-mounted `/mnt/c` path.

- [ ] **Step 4: Clone the repo on the PC**

Run from WSL Ubuntu:

```bash
git clone git@github.com:AnimeWeeb9000/polymarket.git ~/polymarket
cd ~/polymarket
git checkout codex/spoon-cpu-optimization
```

If the PC does not have GitHub SSH auth yet, use HTTPS temporarily for read-only clone:

```bash
git clone https://github.com/AnimeWeeb9000/polymarket.git ~/polymarket
cd ~/polymarket
git checkout codex/spoon-cpu-optimization
```

- [ ] **Step 5: Configure compose env for PC**

Run from WSL Ubuntu:

```bash
cd ~/polymarket
cp deploy/collector/.env.example deploy/collector/.env
sed -i "s|^POLYMARKET_DATA_DIR=.*|POLYMARKET_DATA_DIR=$HOME/polymarket-data|" deploy/collector/.env
sed -i "s/^POLYMARKET_UID=.*/POLYMARKET_UID=$(id -u)/" deploy/collector/.env
sed -i "s/^POLYMARKET_GID=.*/POLYMARKET_GID=$(id -g)/" deploy/collector/.env
sed -i "s/^POLYMARKET_PREWARM_WINDOWS=.*/POLYMARKET_PREWARM_WINDOWS=2/" deploy/collector/.env
sed -i "s/^POLYMARKET_NORMALIZER_INTERVAL_SECONDS=.*/POLYMARKET_NORMALIZER_INTERVAL_SECONDS=0.25/" deploy/collector/.env
```

Expected: the PC uses `~/polymarket-data`, current/next windows, and the same conservative normalizer cadence as `spoon`.

---

### Task 2: Bulk Copy Spoon Data To The PC Without Stopping Spoon

**Files/Hosts:**
- Source: `spoon:/home/spoon/polymarket-data`
- Destination: `THEPC:~/polymarket-data`
- No deletion

- [ ] **Step 1: Record spoon state before copy**

Run from the Mac:

```bash
ssh spoon 'date -u; docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}" | grep -E "polymarket|NAMES"; du -sh /home/spoon/polymarket-data/*'
```

Expected: capture current runtime status and data sizes.

- [ ] **Step 2: First bulk copy through the Mac relay**

Use `rsync` if available on the PC WSL SSH path. If not, use tar streaming.

Preferred from the Mac:

```bash
ssh spoon 'tar -C /home/spoon -cf - polymarket-data' \
  | ssh ender@100.72.104.49 'wsl -d Ubuntu -- bash -lc "tar -C \$HOME -xf -"'
```

Expected: `~/polymarket-data` appears in WSL and contains `raw`, `db`, `live`, and `logs`.

- [ ] **Step 3: Verify copied data on the PC**

Run from the Mac:

```bash
ssh ender@100.72.104.49 'wsl -d Ubuntu -- bash -lc "du -sh \$HOME/polymarket-data \$HOME/polymarket-data/*; test -f \$HOME/polymarket-data/live/status.json"'
```

Expected: copied size is close to `spoon` and `status.json` exists.

---

### Task 3: Build Or Load Images On The PC

**Files/Hosts:**
- Host: PC WSL
- Repo: `~/polymarket`

- [ ] **Step 1: Build images locally on the PC**

Run from PC WSL:

```bash
cd ~/polymarket
./scripts/build_images_pc.sh
```

Expected: Docker builds `polymarket-rust-collector:<short-sha>` and `polymarket-normalizer:<short-sha>`, then writes tarballs under `dist/docker/`.

- [ ] **Step 2: Verify images exist**

Run from PC WSL:

```bash
docker images | grep -E 'polymarket-rust-collector|polymarket-normalizer'
ls -lh dist/docker/
```

Expected: SHA-tagged collector and normalizer images exist.

- [ ] **Step 3: Do not start the collector yet**

Stop here if `spoon` is still active. Starting the PC collector before the final cutover would create two live collectors.

---

### Task 4: Cut Over From Spoon To PC

**Files/Hosts:**
- Stop active Polymarket containers on `spoon`
- Final sync data to PC
- Start active Polymarket containers on PC

- [ ] **Step 1: Confirm cutover window**

Before running this task, Enoch must explicitly approve the cutover. This is the first step that stops live collection on `spoon`.

- [ ] **Step 2: Stop spoon Polymarket runtime**

Run from the Mac:

```bash
ssh spoon 'cd /home/spoon/polymarket && docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml stop collector normalizer'
```

Expected: `collector` and `normalizer` stop, but containers/data/images remain for rollback.

- [ ] **Step 3: Final data sync after spoon is stopped**

Run from the Mac:

```bash
ssh spoon 'tar -C /home/spoon -cf - polymarket-data' \
  | ssh ender@100.72.104.49 'wsl -d Ubuntu -- bash -lc "tar -C \$HOME -xf -"'
```

Expected: PC data has the final stopped-spoon state.

- [ ] **Step 4: Start PC runtime**

Run from the Mac:

```bash
ssh ender@100.72.104.49 'wsl -d Ubuntu -- bash -lc "cd \$HOME/polymarket && docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml up -d collector normalizer"'
```

Expected: PC starts collector and normalizer against `~/polymarket-data`.

- [ ] **Step 5: Verify PC health**

Run from the Mac:

```bash
ssh ender@100.72.104.49 'wsl -d Ubuntu -- bash -lc "cd \$HOME/polymarket && python3 scripts/check_collector_status.py --status-path \$HOME/polymarket-data/live/status.json --raw-root \$HOME/polymarket-data/raw --normalized-health-path \$HOME/polymarket-data/live/normalized_health.json --expected-prewarm-windows 2"'
```

Expected: health check returns `ok: True`. If it fails, inspect logs before making any deletion decision.

- [ ] **Step 6: Verify spoon remains stopped**

Run from the Mac:

```bash
ssh spoon 'docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}" | grep -E "polymarket|NAMES"'
```

Expected: no running Polymarket containers on `spoon`, or stopped containers only if using `docker ps -a`.

---

### Task 5: Rollback Plan

**Files/Hosts:**
- `spoon` remains intact until this rollback window closes

- [ ] **Step 1: If PC fails, stop PC runtime**

Run from the Mac:

```bash
ssh ender@100.72.104.49 'wsl -d Ubuntu -- bash -lc "cd \$HOME/polymarket && docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml stop collector normalizer"'
```

- [ ] **Step 2: Restart spoon runtime**

Run from the Mac:

```bash
ssh spoon 'cd /home/spoon/polymarket && docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml start collector normalizer'
```

- [ ] **Step 3: Verify spoon health**

Run from the Mac:

```bash
ssh spoon 'cd /home/spoon/polymarket && python3 scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json --raw-root /home/spoon/polymarket-data/raw --normalized-health-path /home/spoon/polymarket-data/live/normalized_health.json --expected-prewarm-windows 2'
```

Expected: spoon returns to live truth host status.

---

### Task 6: Decommission Spoon Polymarket Runtime After PC Stability

**Files/Hosts:**
- Destructive changes on `spoon`

- [ ] **Step 1: Wait for stability**

Run the PC runtime through at least one meaningful observation window before deletion. Minimum gate:

```bash
ssh ender@100.72.104.49 'wsl -d Ubuntu -- bash -lc "cd \$HOME/polymarket && python3 scripts/check_collector_status.py --status-path \$HOME/polymarket-data/live/status.json --raw-root \$HOME/polymarket-data/raw --normalized-health-path \$HOME/polymarket-data/live/normalized_health.json --expected-prewarm-windows 2"'
```

Expected: repeated health checks pass.

- [ ] **Step 2: Remove spoon containers only**

Requires explicit approval.

Run from the Mac:

```bash
ssh spoon 'cd /home/spoon/polymarket && docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml down'
```

Expected: Polymarket containers and compose network are removed; data is still present.

- [ ] **Step 3: Remove spoon Polymarket images**

Requires explicit approval.

Run from the Mac:

```bash
ssh spoon 'docker images --format "{{.Repository}}:{{.Tag}} {{.ID}}" | awk "/^polymarket-rust-collector:|^polymarket-normalizer:/ {print \$2}" | sort -u | xargs -r docker rmi'
```

Expected: Polymarket-specific images are removed. Do not run broad `docker system prune` yet because spoon also hosts other workloads.

- [ ] **Step 4: Archive or delete image artifacts**

Requires explicit approval.

Archive:

```bash
ssh spoon 'mkdir -p /home/spoon/archive && tar -C /home/spoon -czf /home/spoon/archive/polymarket-image-artifacts-$(date -u +%Y%m%dT%H%M%SZ).tar.gz polymarket-image-artifacts'
```

Delete after archive:

```bash
ssh spoon 'rm -rf /home/spoon/polymarket-image-artifacts'
```

- [ ] **Step 5: Archive or delete spoon data**

Requires separate explicit approval. Recommended default: keep `/home/spoon/polymarket-data` as cold backup until the PC has run stable for multiple days.

Archive:

```bash
ssh spoon 'mkdir -p /home/spoon/archive && tar -C /home/spoon -czf /home/spoon/archive/polymarket-data-$(date -u +%Y%m%dT%H%M%SZ).tar.gz polymarket-data'
```

Delete after archive:

```bash
ssh spoon 'rm -rf /home/spoon/polymarket-data'
```

- [ ] **Step 6: Archive or delete spoon repo checkout**

Requires explicit approval.

Archive:

```bash
ssh spoon 'mkdir -p /home/spoon/archive && tar -C /home/spoon -czf /home/spoon/archive/polymarket-repo-$(date -u +%Y%m%dT%H%M%SZ).tar.gz polymarket'
```

Delete after archive:

```bash
ssh spoon 'rm -rf /home/spoon/polymarket'
```

---

## Execution Gate

Stop here until Enoch approves execution. The first safe execution slice is Task 1 only: install and verify WSL/Docker on the PC. Do not stop `spoon`, copy final data, start the PC collector, or delete anything until the PC runtime host is ready and the cutover window is approved.
