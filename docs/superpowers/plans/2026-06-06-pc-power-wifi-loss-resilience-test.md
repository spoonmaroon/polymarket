# PC Power And Wi-Fi Loss Resilience Test Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Test whether THEPC Polymarket runtime recovers automatically after controlled service restart, controlled reboot, Wi-Fi loss, and hard power-loss scenarios without corrupting data or requiring manual container repair.

**Architecture:** Use staged failure injection from least destructive to most destructive. Each stage captures a baseline, triggers one failure mode, waits for recovery, verifies runtime health through Docker/API/status files, and records exact evidence under `docs/superpowers/reports/` without touching trading, credentials, or live orders.

**Tech Stack:** macOS Codex shell, SSH over Tailscale to `ender@100.72.104.49`, THEPC Windows + WSL Ubuntu, Docker Desktop Linux containers, `docker compose`, FastAPI runtime endpoints, existing `scripts/check_collector_status.py`, PowerShell for controlled reboot only after approval.

---

## Critical Preflight Finding

At plan creation time, THEPC baseline is not fully green:

```text
collector: healthy
normalizer: healthy
api: healthy
gpu-probability-worker: running
outcome-refresh: unhealthy
```

Observed root cause for `outcome-refresh`: it repeatedly cannot lock `/home/ender/polymarket-data/db/polymarket.duckdb`, so `/home/ender/polymarket-data/live/outcomes.json` becomes stale.

That means there are two valid test modes:

1. **Runtime-only recovery test:** pass criteria exclude outcome-refresh and focus on collector/API/normalizer/GPU worker/live rows.
2. **Full-stack recovery test:** first fix outcome-refresh lock handling, then include outcome freshness in pass criteria.

Recommended path: run runtime-only recovery first, then fix outcome-refresh, then rerun full-stack recovery.

## Safety Rules

- Do not do a hard power cut from SSH.
- Do not intentionally disable Wi-Fi while SSH is the only control path unless the user is physically at THEPC or has another recovery path.
- Do not run `git reset --hard`, wipe Docker volumes, or delete runtime data.
- Do not test live trading recovery. This is read-only runtime resilience only.
- Stop after the first failed recovery stage and write the evidence before trying the next stage.

## Pass Criteria

Runtime-only pass:

```text
SSH returns after failure injection.
Docker Desktop / WSL Docker engine is reachable.
collector, normalizer, api are running.
/api/runtime/live?limit=8 returns ok=true.
runtime live payload has at least 1 orderbook row.
runtime live payload has price rows.
status_age_ms <= 30000.
scripts/check_collector_status.py exits 0.
No raw data path missing; /home/ender/polymarket-data/raw/.polymarket_archive_root exists.
```

Full-stack pass adds:

```text
outcome-refresh container is healthy.
/home/ender/polymarket-data/live/outcomes.json has schema_version polymarket-outcome-runtime-v1.
outcomes.json mtime age is under 120 seconds.
```

## File Structure

- Create `docs/superpowers/reports/2026-06-06-pc-resilience-test.md`
  - Append baseline, action, recovery time, command outputs, pass/fail decision, and follow-up actions.
- Use existing `deploy/collector/docker-compose.yml`
  - Verify restart policies and healthchecks.
- Use existing `scripts/check_collector_status.py`
  - Verify live state freshness after each recovery stage.
- Optionally modify `scripts/deploy_pc.sh` only if the launcher or boot recovery path needs a durable repo-side fix.

## Task 1: Baseline Snapshot

**Files:**
- Create: `docs/superpowers/reports/2026-06-06-pc-resilience-test.md`

- [ ] **Step 1: Create the report header**

Run:

```bash
cat > docs/superpowers/reports/2026-06-06-pc-resilience-test.md <<'EOF'
# THEPC Power And Wi-Fi Loss Resilience Test

Date: 2026-06-06
Host: THEPC
SSH: ender@100.72.104.49
Repo: /home/ender/polymarket
Data: /home/ender/polymarket-data

## Baseline

EOF
```

Expected: report file exists and starts with the baseline section.

- [ ] **Step 2: Capture deployed commit**

Run:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -lc 'cd /home/ender/polymarket && git rev-parse HEAD'" | tee -a docs/superpowers/reports/2026-06-06-pc-resilience-test.md
```

Expected: prints deployed commit SHA.

- [ ] **Step 3: Capture Docker state**

Run:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -lc 'cd /home/ender/polymarket && docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml ps'" | tee -a docs/superpowers/reports/2026-06-06-pc-resilience-test.md
```

Expected: collector, normalizer, and api are `Up`; outcome-refresh may be unhealthy if running runtime-only mode.

- [ ] **Step 4: Capture API live summary**

Run:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -s" <<'REMOTE' | tee -a docs/superpowers/reports/2026-06-06-pc-resilience-test.md
set -euo pipefail
curl -fsS --max-time 3 'http://127.0.0.1:8000/api/runtime/live?limit=8' > /tmp/polymarket-runtime-live.json
python3 - <<'PY'
import json
p=json.load(open('/tmp/polymarket-runtime-live.json'))
m=p.get('monitor') or {}
l=p.get('latency') or {}
s=p.get('status') or {}
print({
    'ok': p.get('ok'),
    'orderbooks': len(m.get('orderbooks') or []),
    'prices': len(m.get('price_rows') or []),
    'status_age_ms': l.get('status_age_ms'),
    'health_flags': s.get('health_flags'),
})
PY
REMOTE
```

Expected: `ok=True`, nonzero orderbooks and prices, status age under 30000 ms.

- [ ] **Step 5: Run status validator**

Run:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -s" <<'REMOTE' | tee -a docs/superpowers/reports/2026-06-06-pc-resilience-test.md
set -euo pipefail
cd /home/ender/polymarket
python3 scripts/check_collector_status.py \
  --status-path /home/ender/polymarket-data/live/status.json \
  --max-status-age-seconds 30 \
  --max-price-age-ms 30000 \
  --max-orderbook-age-ms 30000 \
  --max-websocket-event-age-ms 30000 \
  --raw-root /home/ender/polymarket-data/raw \
  --max-raw-event-age-ms 30000 \
  --normalized-health-path /home/ender/polymarket-data/live/normalized_health.json \
  --max-normalized-health-age-ms 30000 \
  --expected-prewarm-windows 2
REMOTE
```

Expected: prints `{'ok': True, ...}`.

## Task 2: Controlled Container Restart Recovery

**Files:**
- Modify: `docs/superpowers/reports/2026-06-06-pc-resilience-test.md`

- [ ] **Step 1: Append test section**

Run:

```bash
printf '\n## Controlled Container Restart\n\n' >> docs/superpowers/reports/2026-06-06-pc-resilience-test.md
```

Expected: report has a controlled restart section.

- [ ] **Step 2: Restart read-only runtime containers**

Run:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -s" <<'REMOTE' | tee -a docs/superpowers/reports/2026-06-06-pc-resilience-test.md
set -euo pipefail
cd /home/ender/polymarket
started_at=$(date -Iseconds)
docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml restart collector normalizer api
echo "restart_started_at=$started_at"
REMOTE
```

Expected: Docker restarts the three services without deleting volumes.

- [ ] **Step 3: Wait for runtime recovery**

Run:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -s" <<'REMOTE' | tee -a docs/superpowers/reports/2026-06-06-pc-resilience-test.md
set -euo pipefail
deadline=$((SECONDS + 120))
while [ "$SECONDS" -lt "$deadline" ]; do
  if curl -fsS --max-time 2 'http://127.0.0.1:8000/api/runtime/live?limit=8' > /tmp/polymarket-runtime-live.json 2>/dev/null; then
    python3 - <<'PY' && exit 0
import json, sys
p=json.load(open('/tmp/polymarket-runtime-live.json'))
m=p.get('monitor') or {}
l=p.get('latency') or {}
ok = p.get('ok') is True and len(m.get('orderbooks') or []) > 0 and (l.get('status_age_ms') or 999999) <= 30000
print({'ok': p.get('ok'), 'orderbooks': len(m.get('orderbooks') or []), 'status_age_ms': l.get('status_age_ms')})
sys.exit(0 if ok else 1)
PY
  fi
  sleep 2
done
echo "runtime did not recover inside 120 seconds" >&2
exit 1
REMOTE
```

Expected: exits 0 and prints a healthy live summary within 120 seconds.

- [ ] **Step 4: Validate collector status**

Run the Task 1 Step 5 validator again.

Expected: validator exits 0.

## Task 3: Controlled Windows Reboot Recovery

**Files:**
- Modify: `docs/superpowers/reports/2026-06-06-pc-resilience-test.md`

- [ ] **Step 1: Ask for explicit approval**

Do not run this task until the user explicitly approves a Windows reboot of THEPC. This will temporarily drop SSH, Docker, WSL, and the TUI.

- [ ] **Step 2: Trigger controlled reboot**

Run only after approval:

```bash
printf '\n## Controlled Windows Reboot\n\n' >> docs/superpowers/reports/2026-06-06-pc-resilience-test.md
ssh ender@100.72.104.49 "powershell.exe -NoProfile -Command \"Restart-Computer -Force\""
```

Expected: SSH disconnects because THEPC reboots.

- [ ] **Step 3: Wait for SSH to return**

Run:

```bash
for _ in $(seq 1 90); do
  if ssh -o ConnectTimeout=3 ender@100.72.104.49 "echo online" 2>/dev/null; then
    break
  fi
  sleep 5
done
```

Expected: prints `online` within 7.5 minutes.

- [ ] **Step 4: Check Docker Desktop / WSL recovery**

Run:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -s" <<'REMOTE' | tee -a docs/superpowers/reports/2026-06-06-pc-resilience-test.md
set -euo pipefail
for _ in $(seq 1 60); do
  if docker info >/dev/null 2>&1; then
    echo "docker=online"
    exit 0
  fi
  sleep 5
done
echo "docker did not come online inside 5 minutes" >&2
exit 1
REMOTE
```

Expected: Docker engine comes online. If it does not, Docker Desktop is not configured to start automatically after boot.

- [ ] **Step 5: Check runtime self-recovery**

Run Task 2 Step 3 and Task 1 Step 5.

Expected: live runtime and validator recover without manually running `deploy_pc.sh`. If not, record whether Docker is offline, containers are stopped, or API/status files are stale.

## Task 4: Wi-Fi Loss Recovery

**Files:**
- Modify: `docs/superpowers/reports/2026-06-06-pc-resilience-test.md`

- [ ] **Step 1: Confirm physical fallback**

Do not start this task unless the user is physically near THEPC or there is a non-Wi-Fi control path. A Wi-Fi drop may sever SSH and require local intervention.

- [ ] **Step 2: Start observer loop from Mac**

Run:

```bash
printf '\n## Wi-Fi Loss\n\n' >> docs/superpowers/reports/2026-06-06-pc-resilience-test.md
for i in $(seq 1 180); do
  ts=$(date -Iseconds)
  if ssh -o ConnectTimeout=2 ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -lc 'curl -fsS --max-time 2 http://127.0.0.1:8000/api/runtime/live?limit=2 >/dev/null && echo api-ok'" 2>/dev/null; then
    echo "$ts ssh=ok api=ok" | tee -a docs/superpowers/reports/2026-06-06-pc-resilience-test.md
  else
    echo "$ts ssh_or_api=down" | tee -a docs/superpowers/reports/2026-06-06-pc-resilience-test.md
  fi
  sleep 5
done
```

Expected: observer records downtime and recovery. Stop the loop with Ctrl-C after Wi-Fi is restored and health is observed.

- [ ] **Step 3: User disconnects Wi-Fi for 60 seconds**

The user should disconnect/reconnect Wi-Fi manually on THEPC, or unplug/replug the network adapter. Do not run remote Wi-Fi disable commands over SSH.

Expected: observer sees SSH/API down, then restored.

- [ ] **Step 4: Validate runtime after Wi-Fi returns**

Run Task 2 Step 3 and Task 1 Step 5.

Expected: collector reconnects, status becomes fresh, raw event writes continue, API returns live rows.

## Task 5: Hard Power Loss Recovery

**Files:**
- Modify: `docs/superpowers/reports/2026-06-06-pc-resilience-test.md`

- [ ] **Step 1: Confirm physical fallback and data risk**

Do not run this task unless the user is physically near THEPC. Hard power loss can interrupt Docker, WSL, and file writes. The test is useful, but it is intentionally the last stage.

- [ ] **Step 2: Record pre-cut state**

Run Task 1 Steps 2-5 immediately before the power cut.

Expected: baseline is fresh.

- [ ] **Step 3: User cuts power for 30 seconds, then restores power**

The user should use the PC power button or power source manually. Do not try to simulate a hard power loss with remote commands.

Expected: SSH drops, then returns after Windows boots.

- [ ] **Step 4: Wait for host and Docker to return**

Run Task 3 Steps 3-4.

Expected: SSH and Docker return without manual repair.

- [ ] **Step 5: Validate runtime and data path**

Run Task 2 Step 3 and Task 1 Step 5.

Also run:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -s" <<'REMOTE' | tee -a docs/superpowers/reports/2026-06-06-pc-resilience-test.md
set -euo pipefail
test -f /home/ender/polymarket-data/raw/.polymarket_archive_root
find /home/ender/polymarket-data/raw -name '*.tmp' -o -name '*.parquet.tmp' | head -20
REMOTE
```

Expected: sentinel exists; no concerning orphaned temp files remain. If temporary files exist, record them and do not delete until inspected.

## Task 6: Report Results And Next Fixes

**Files:**
- Modify: `docs/superpowers/reports/2026-06-06-pc-resilience-test.md`

- [ ] **Step 1: Add pass/fail summary**

Append a summary with:

```text
runtime-only recovery: PASS/FAIL
full-stack recovery: PASS/FAIL/SKIPPED
controlled container restart: PASS/FAIL
controlled reboot: PASS/FAIL/SKIPPED
wifi loss: PASS/FAIL/SKIPPED
hard power loss: PASS/FAIL/SKIPPED
manual intervention required: yes/no
time to healthy runtime:
known gaps:
```

- [ ] **Step 2: If recovery fails, classify the layer**

Use this classification:

```text
host_boot_failure
tailscale_or_ssh_failure
wsl_not_started
docker_desktop_not_started
containers_not_restarted
collector_unhealthy
normalizer_unhealthy
api_unhealthy
outcome_refresh_unhealthy
data_path_missing
status_stale
raw_writes_stale
```

- [ ] **Step 3: Stop and plan fixes**

Do not proceed to a more destructive stage after a failed stage. Write the root-cause evidence and create a separate fix plan.

## Self-Review

- Scope coverage: plan covers baseline, container restart, controlled reboot, Wi-Fi loss, hard power loss, pass criteria, evidence capture, and report output.
- Safety check: destructive/disruptive steps require explicit user approval and physical fallback.
- Known issue check: current `outcome-refresh` unhealthy state is called out so the test does not produce a false failure.
- Placeholder scan: no TBD/TODO placeholders; each executable step includes concrete commands and expected results.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-06-pc-power-wifi-loss-resilience-test.md`.

Recommended execution order:

1. Run Task 1 baseline now.
2. Run Task 2 controlled container restart.
3. Stop and review results.
4. Ask for explicit approval before Task 3 controlled Windows reboot.
5. Run Task 4 and Task 5 only with the user physically near THEPC.

