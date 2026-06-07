# Spoon Deployment Runbook

The Python collector runbook below is retired design context. The legacy Docker
entrypoint and systemd unit now fail closed; do not use this runbook to restart
Python collection. The active read-only runtime is the Rust SDK state manager.
It is intentionally scoped to BTC/ETH 5m current and next windows
until the warm-state path and durable persistence are stable.

Current live runtime note, 2026-06-03: the active runtime moved from spoon to
THEPC over Tailscale. The known operator API host is configurable; THEPC is
reachable as user `ender` at `100.72.104.49`, but this runbook section should
not be read as an instruction to SSH, deploy, restart, or manage the PC.

Persistent data lives outside the repo at `/home/spoon/polymarket-data`.
The Rust collector writes raw WebSocket journals and state snapshots under
`/home/spoon/polymarket-data/raw`; DuckDB replay/research tables live under
`/home/spoon/polymarket-data/db` and are populated separately by the raw Rust
event normalizer.

## Time Policy

All stored timestamps are UTC. Operator displays use `America/Chicago`. Venue rule text is stored raw so ET wording from Polymarket can be audited later.

## One-Time Setup On Spoon

```bash
cd /home/spoon
git clone git@github.com:AnimeWeeb9000/polymarket.git polymarket
mkdir -p /home/spoon/polymarket-data/{raw,db,live,logs}
touch /home/spoon/polymarket-data/raw/.polymarket_archive_root
cd /home/spoon/polymarket
cp deploy/collector/.env.example deploy/collector/.env
sed -i "s/^POLYMARKET_UID=.*/POLYMARKET_UID=$(id -u)/" deploy/collector/.env
sed -i "s/^POLYMARKET_GID=.*/POLYMARKET_GID=$(id -g)/" deploy/collector/.env
docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml up -d --build collector normalizer api
python3 scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json --max-status-age-seconds 30 --max-price-age-ms 30000 --max-orderbook-age-ms 30000 --max-websocket-event-age-ms 30000
```

Set `POLYMARKET_PREWARM_WINDOWS=2` or rely on the compose default so spoon warms
BTC/ETH current and next 5m windows.
On THEPC, the current normalizer cadence is
`POLYMARKET_NORMALIZER_INTERVAL_SECONDS=1.0`. The older spoon sidecar used
`POLYMARKET_NORMALIZER_INTERVAL_SECONDS=0.25` as a home-server CPU compromise.
After VPS migration, re-test the normalizer on the new host and lower the
interval only if CPU headroom, DuckDB freshness, CUDA worker lock behavior, and
status latency stay healthy.

## Production Image Deploy

Normal live deploys should load prebuilt images on the runtime host and restart
them without compiling Rust on the host.

### THEPC Deploy

THEPC is the active runtime host. Do not use blind auto-pull for this lane. The
Mac-side helper below builds pinned Linux images, creates a git bundle for the
exact local commit, streams the bundle and image tarballs into THEPC's Ubuntu
WSL environment, runs the existing prebuilt-image deploy gate, and finishes with
the collector status check. The same deploy also installs the matching
`polymarket-cockpit-tui` binary to `/home/ender/bin/polymarket-cockpit-tui`, so
the Windows desktop shortcut opens the TUI for the deployed commit. It also
builds `ui/dist` into the API image and refreshes a Windows desktop shortcut
named `Polymarket Runtime Monitor.lnk`; opening that shortcut starts the
runtime if needed and launches `http://127.0.0.1:8000/` in the browser.

```bash
cd /Users/goon/polymarket
./scripts/deploy_pc.sh
```

`./scripts/deploy_pc.sh` is the only supported CUDA runtime deployment path.
The generic spoon deploy path does not start gpu-probability-worker.

Defaults:

- `PC_HOST=ender@100.72.104.49`
- `PC_WSL_DISTRO=Ubuntu`
- `PC_REPO=/home/ender/polymarket`
- `PC_BUNDLE=/home/ender/polymarket.bundle`
- `PC_DATA_DIR=/home/ender/polymarket-data`
- `PC_BIN_DIR=/home/ender/bin`
- `PC_NORMALIZER_INTERVAL_SECONDS=1.0`

Set `PC_DEPLOY_BUILD_IMAGES=0` only when matching
`dist/docker/polymarket-rust-collector-<sha>.tar` and
`dist/docker/polymarket-normalizer-<sha>.tar`,
`dist/docker/polymarket-cuda-probability-<sha>.tar`, plus
`dist/docker/polymarket-cockpit-tui-<sha>` already exist for the checked-out
commit.

### Spoon Deploy

Build images on the Mac. The default target platform is
`TARGET_PLATFORM=linux/amd64`, matching spoon's Linux runtime:

```bash
cd /Users/goon/polymarket
./scripts/build_images_pc.sh
```

Ship and start the matching images on spoon:

```bash
cd /Users/goon/polymarket
./scripts/deploy_prebuilt_images.sh
```

The deploy helper copies the collector and normalizer tarballs to
`/home/spoon/polymarket-image-artifacts`, runs `docker load`, then calls
`scripts/deploy.sh` with `POLYMARKET_DEPLOY_USE_PREBUILT=1`,
`POLYMARKET_DEPLOY_REF` and `POLYMARKET_EXPECTED_DEPLOY_SHA` pinned to the
exact full commit SHA,
`POLYMARKET_COLLECTOR_IMAGE`, `POLYMARKET_NORMALIZER_IMAGE`,
`POLYMARKET_DATA_DIR=/home/spoon/polymarket-data`, and `DEPLOY_FORCE=1`.
It finishes with the collector status gate for the active status, raw, and
normalized-health paths.

## Auto Deploy

Install a cron entry on spoon only after deciding how images reach the host:

```cron
*/5 * * * * /home/spoon/polymarket/scripts/deploy.sh >> /home/spoon/polymarket/logs/deploy.cron.log 2>&1
```

The deploy script fetches the configured deploy ref, refuses dirty server worktrees, fast-forwards only, restarts the collector and normalizer, and smoke-checks the status file. A bare cron entry does not compile Rust by default. With `POLYMARKET_DEPLOY_USE_PREBUILT=1`, deploy requires `POLYMARKET_EXPECTED_DEPLOY_SHA` and SHA-tagged collector and normalizer images that exactly match the deploy ref. Host builds are available only when explicitly enabled with `POLYMARKET_DEPLOY_ALLOW_SPOON_BUILD=1`, and that fallback limits Rust build pressure with `CARGO_BUILD_JOBS=1` by default.
It only skips a rebuild/restart when both the checked-out commit and the deployed marker match the target commit, so a manual `git pull` cannot accidentally leave an older healthy container running.
If `deploy/collector/.env` exists, the deploy script uses it explicitly. The collector is read-only and runs Chainlink RTDS plus Polymarket CLOB WebSocket state-manager mode with `POLYMARKET_INTERVAL=5m`.

For manual deploy testing, pin the exact `main` commit or full SHA being tested:

```bash
POLYMARKET_DEPLOY_REF=origin/main DEPLOY_FORCE=1 /home/spoon/polymarket/scripts/deploy.sh
```

GitHub should stay clean with `main` as the only long-lived branch. Do not let
a runtime host's local `main` remain ahead of `origin/main`; push `main` first,
then deploy the pinned commit.

## Retention

Raw data remains hot for 90 days. Do not enable deletion until compact replay tests prove 1-second compacted research tables reproduce the same as-of state for sampled contracts.

## Normalize Rust Raw Journals

Run this after raw journals exist or after a deliberate fresh-slate reset:

```bash
cd /home/spoon/polymarket
uv run polymarket-engine normalize-rust-events \
  --raw-root /home/spoon/polymarket-data/raw \
  --duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb
```

This writes normalized Chainlink price ticks, Polymarket top-of-book rows, and
ingest manifests from the Rust raw journals. It is the replay bridge; it does
not start the retired Python collector and it does not place orders.
Direct WebSocket journals are normalized by default. Add
`--include-state-snapshots` only for an explicit state-snapshot audit or
recovery backfill.

Then snapshot current as-of decision state:

```bash
uv run polymarket-engine build-current-decision-states \
  --duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb \
  --status-path /home/spoon/polymarket-data/live/status.json
```

Then write normalized table health after snapshot building:

```bash
uv run polymarket-engine write-normalized-health \
  --duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb \
  --out /home/spoon/polymarket-data/live/normalized_health.json
```

## THEPC Outcome Backfill

Run historical outcome repair explicitly on THEPC, outside the 0.1s normalizer
loop:

```bash
cd /home/ender/polymarket
uv run polymarket-engine backfill-outcomes \
  --duckdb-path /home/ender/polymarket-data/db/polymarket.duckdb \
  --outcomes-path /home/ender/polymarket-data/live/outcomes.json \
  --start-date 2026-06-01 \
  --end-date 2026-06-04 \
  --limit 500 \
  --write
```

The command repairs source-backed outcome history and rewrites the status file
for the TUI. It does not compute local winners.

## Hot Replay Gate

Run this operational gate after normalized DuckDB rows are current:

```bash
python3 scripts/run_hot_replay_gate.py --raw-root /home/spoon/polymarket-data/raw --duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb --snapshot-dir /home/spoon/polymarket-data/live/hot-replay-snapshot --report-out /home/spoon/polymarket-data/live/hot_decision_replay_report.json --limit 40 --scan-limit 5000
```

The gate avoids normalizer DB lock collisions by verifying hot-state replay
against a copied read-only snapshot. It does not pause collector or normalizer,
and it must not enter the hot live decision path.

Restart warm-state policy: if the Rust collector restarts inside an already
open contract window and has not observed that window's Chainlink threshold in
memory, hot `DecisionState` rows for that window remain visible but blocked with
`MissingThreshold` and `RestartWarmupBlocked`. They continue to appear in hot
JSONL and replay reports until the next warmed window starts, or until the
threshold tick is observed in memory. Do not recover the threshold from raw
journals or DuckDB in the hot path; those stores are for replay and audit.

Durability decision: persist exact `DecisionState` snapshots as the pre-
probability live decision boundary, and keep append-only Chainlink/CLOB raw
event journals as the replay/audit trail. Live decision work does not need to
block until every raw event has already been normalized into DuckDB, but replay
tests must prove the stored raw journals can reconstruct sampled
`DecisionState` rows before probability or trading work starts.

## Manual Health Checks

```bash
docker compose -f /home/spoon/polymarket/deploy/collector/docker-compose.yml ps
docker compose -f /home/spoon/polymarket/deploy/collector/docker-compose.yml logs --tail=100 collector
python3 /home/spoon/polymarket/scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json --max-status-age-seconds 30 --max-price-age-ms 30000 --max-orderbook-age-ms 30000 --max-websocket-event-age-ms 30000
python3 /home/spoon/polymarket/scripts/verify_state_manager_report.py /home/spoon/polymarket-data/live/status.json
du -sh /home/spoon/polymarket-data/*
```

## Read-Only Cockpit TUI

The `polymarket-cockpit-tui` is read-only. It consumes
`/api/runtime/live/stream` first, falls back to `/api/runtime/live`, and keeps
the legacy `/status`, `/monitor`, and `/gates` endpoints for manual debugging.
It displays runtime status, gate failures, freshness, latency, health, grouped
market books, cached probability outputs, and read-only outcome history. It must
not place orders, deploy containers, rebuild images, write collector state,
restart services, or access auth secrets.

The browser monitor is also read-only. The FastAPI app serves `ui/dist` at `/`
when the built files exist, while `/api/*` routes keep precedence. The browser
polls `/api/runtime/live` and `/api/runtime/probabilities`; it shows missing or
disabled probability fields as health state, not as operator actions.

Cached probability outputs are display-only. The deployed normalizer and API
default CPU runtime probability generation off; the PC-only
`gpu-probability-worker` container refreshes `features.probability_grid_cache`
and `/var/lib/polymarket/live/probabilities.json` with NVIDIA CUDA/CuPy. Keep
`POLYMARKET_NORMALIZER_ENABLE_PROBABILITIES=0` and
`POLYMARKET_ENABLE_RUNTIME_PROBABILITIES=0` unless intentionally moving Monte
Carlo load back onto the CPU path.

### Probability Persistence And Latency

The live probability display can update from
`/home/ender/polymarket-data/live/probabilities.json` before historical DuckDB
tables are checkpointed. The normalizer remains the DuckDB writer owner.
External GPU probability workers should write compact forward-only event rows to
`/home/ender/polymarket-data/live/probability-events.jsonl`; the normalizer
drains that file into `features.probability_event_log`. This avoids DuckDB
writer-lock contention while preserving probability evidence for calibration.

The status payload separates lanes with `lanes`, `rows`, and `nowcast_rows`.
`NOWCAST` is the fast operator lane and must not be scored as Monte Carlo
history. `MC` and `CACHE` rows remain the confirmed probability lanes. Check
status freshness with:

```bash
python3 scripts/check_probability_latency.py \
  --path /home/ender/polymarket-data/live/probabilities.json \
  --max-total-lag-ms 1000 \
  --require-lane NOWCAST
```

Live data changes should appear through the runtime API polling path. TUI code,
layout, or parser changes require a fresh THEPC deploy and reopening the
desktop shortcut so the new binary is loaded.

Each displayed BTC/ETH 5m row is one binary market window. The CLOB books remain
separate Up and Down token books internally, and the selected Book panel renders
both sides for the chosen market. The Outcomes tab shows only the official
winner, winning token id, and status. Labels come from Polymarket CLOB market
metadata where a known Up or Down token has `winner=true`; if the source is
missing or ambiguous, the row stays pending.

The Market tab `K` column is the read-only Chainlink start-reference target for
the market window as surfaced by the Rust state-manager status payload. Current
windows should show a numeric `K`; future windows can show `pending` until the
start tick has been observed. The Market tab also keeps a bounded in-memory
BTC/ETH price path panel with the active target label for each asset. This chart
is display state only and is rebuilt from runtime price rows after reopening the
TUI.

Expired market rows stay visible for the normal handoff window, and can remain
visible longer while a fresh pending outcome feed is actively tracking that
market. Once the TUI first sees an official winner for that market, the row
remains visible for 30 seconds so the Market tab can show the handoff, then it
disappears and the Outcomes tab remains the history surface. A stale pending
outcome feed must not pin an expired row forever.

The normalizer publishes outcome history to
`/var/lib/polymarket/live/outcomes.json`, and the API reads that file before any
DuckDB fallback. This avoids read contention with the normalizer's live writer
connection while preserving `validation.market_outcome_history` for replay and
audit. The normalizer refreshes outcome history on a slower cadence than the hot
state loop so expired-market labeling does not dominate the 0.1s runtime path.
Each official outcome refresh is also capped by
`POLYMARKET_OFFICIAL_OUTCOME_REFRESH_LIMIT` and processes the newest expired
market windows first. Keep the cap small on live collector hosts; use explicit
offline/backfill jobs for deep historical labeling.

Local API:

```bash
uv run uvicorn polymarket_engine.app:app --host 127.0.0.1 --port 8000
cargo run --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui -- --engine-api-url http://127.0.0.1:8000 --poll-interval-ms 1000
```

THEPC over Tailscale, using a configurable URL:

```bash
cargo run --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui -- --engine-api-url http://100.72.104.49:8000 --poll-interval-ms 1000
```

Endpoint smoke checks:

```bash
curl -fsS "$POLYMARKET_ENGINE_API_URL/api/runtime/live?limit=8" | python3 -m json.tool | head -80
curl -fsS -N "$POLYMARKET_ENGINE_API_URL/api/runtime/live/stream?limit=8&interval_ms=250&max_events=1" | head -20
curl -fsS "$POLYMARKET_ENGINE_API_URL/api/runtime/outcomes?limit=8" | python3 -m json.tool | head -80
```

The deploy compose file runs `collector`, `normalizer`, and `api` with
`restart: unless-stopped`, so the API should come back with Docker after a host
restart. Container status is an operator-only API-side feature; enable it only
for an API process that has Docker CLI access and
`POLYMARKET_ENABLE_CONTAINER_STATUS=1`.

## No-Auth Latency Probe

Measure hypothetical order-submit network timing separately from hot decision
construction. This no-auth probe builds a synthetic payload, hashes it to
approximate local signing work, performs HTTP GET round trips, and writes a
report. It does not place orders and does not load private keys.

```bash
cd /home/spoon/polymarket
docker compose -f deploy/collector/docker-compose.yml run --rm \
  --entrypoint /usr/local/bin/polymarket-live-probe \
  collector \
  --mode latency-probe \
  --order-latency-probe-url https://clob-v2.polymarket.com \
  --order-latency-probe-iterations 10 \
  --out /var/lib/polymarket/live/order_latency_probe.json
```

## Mac-To-Spoon Migration

Run this only after the CLOB WebSocket collector passes local smoke checks and the spoon deploy workflow exists.

```bash
cd /Users/goon/polymarket
REMOTE_HOST=spoon REMOTE_DATA_DIR=/home/spoon/polymarket-data ./scripts/migrate_mac_data_to_spoon.sh
ssh spoon 'cd /home/spoon/polymarket && ./scripts/deploy.sh'
ssh spoon 'python3 /home/spoon/polymarket/scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json'
```

After spoon is fresh, keep the Mac collector stopped. The Mac can still run the read-only monitor against copied files, but it should not write to the same logical data stream while spoon is the active collector.
