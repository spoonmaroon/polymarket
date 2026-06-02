# Spoon Deployment Runbook

The Python collector runbook below is retired design context. The legacy Docker
entrypoint and systemd unit now fail closed; do not use this runbook to restart
Python collection. The active read-only runtime is the Rust SDK state manager.
It is intentionally scoped to BTC/ETH 5m current, next, and next-next windows
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
docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml up -d --build collector
python3 scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json --max-status-age-seconds 30 --max-price-age-ms 30000 --max-orderbook-age-ms 30000 --max-websocket-event-age-ms 30000
```

## Auto Deploy

Install a cron entry on spoon:

```cron
*/5 * * * * /home/spoon/polymarket/scripts/deploy.sh >> /home/spoon/polymarket/logs/deploy.cron.log 2>&1
```

The deploy script fetches the configured deploy ref, refuses dirty server worktrees, fast-forwards only, rebuilds the Rust collector image, restarts the collector, and smoke-checks the status file. It only skips a rebuild when both the checked-out commit and the deployed marker match the target commit, so a manual `git pull` cannot accidentally leave an older healthy container running.
If `deploy/collector/.env` exists, the deploy script uses it explicitly. The collector is read-only and runs Chainlink RTDS plus Polymarket CLOB WebSocket state-manager mode with `POLYMARKET_INTERVAL=5m`.

For branch testing before merge:

```bash
POLYMARKET_DEPLOY_REF=origin/main DEPLOY_FORCE=1 /home/spoon/polymarket/scripts/deploy.sh
```

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

Then write normalized table health:

```bash
uv run polymarket-engine write-normalized-health \
  --duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb \
  --out /home/spoon/polymarket-data/live/normalized_health.json
```

Then snapshot current as-of decision state:

```bash
uv run polymarket-engine build-current-decision-states \
  --duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb \
  --status-path /home/spoon/polymarket-data/live/status.json
```

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

## Mac-To-Spoon Migration

Run this only after the CLOB WebSocket collector passes local smoke checks and the spoon deploy workflow exists.

```bash
cd /Users/goon/polymarket
REMOTE_HOST=spoon REMOTE_DATA_DIR=/home/spoon/polymarket-data ./scripts/migrate_mac_data_to_spoon.sh
ssh spoon 'cd /home/spoon/polymarket && ./scripts/deploy.sh'
ssh spoon 'python3 /home/spoon/polymarket/scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json'
```

After spoon is fresh, keep the Mac collector stopped. The Mac can still run the read-only monitor against copied files, but it should not write to the same logical data stream while spoon is the active collector.
