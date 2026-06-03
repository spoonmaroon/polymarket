# Spoon Deployment Runbook

The Python collector runbook below is retired design context. The legacy Docker
entrypoint and systemd unit now fail closed; do not use this runbook to restart
Python collection. The active read-only runtime is the Rust SDK state manager.
It is intentionally scoped to BTC/ETH 5m current and next windows
until the warm-state path and durable persistence are stable.

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
docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml up -d --build collector normalizer
python3 scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json --max-status-age-seconds 30 --max-price-age-ms 30000 --max-orderbook-age-ms 30000 --max-websocket-event-age-ms 30000
```

Set `POLYMARKET_PREWARM_WINDOWS=2` or rely on the compose default so spoon warms
BTC/ETH current and next 5m windows.
The normalizer sidecar defaults to `POLYMARKET_NORMALIZER_INTERVAL_SECONDS=0.25`.
That cadence is a home-server CPU compromise for spoon. After VPS migration,
re-test the normalizer on the new host and consider returning to
`POLYMARKET_NORMALIZER_INTERVAL_SECONDS=0.1` if CPU headroom, DuckDB freshness,
and status latency stay healthy.

## Production Image Deploy

Normal live deploys should load prebuilt images on spoon and restart them without
compiling Rust on the server.

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

For branch testing before merge:

```bash
POLYMARKET_DEPLOY_REF=origin/main DEPLOY_FORCE=1 /home/spoon/polymarket/scripts/deploy.sh
```

When testing the raw-normalizer deployment before merge, deploy explicitly with
`POLYMARKET_DEPLOY_REF=origin/codex/rust-raw-normalizer`. Do not let spoon's
local `main` remain ahead of `origin/main` after the branch is ready; either
merge/push main or keep the deploy ref explicit.

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
