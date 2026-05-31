# Part Two Live Collectors

Part Two turns the Part One data foundation into read-only live collection.

## First Supported Command

```bash
mkdir -p data/raw
touch data/raw/.polymarket_archive_root
uv run polymarket-engine collect --assets BTC,ETH --duration 10
```

The first network runner records:

- Polymarket BTC/ETH 5-minute market snapshots discovered by deterministic slugs.
- Polymarket CLOB order book snapshots for Up and Down token ids.
- Coinbase BTC/ETH ticker updates for live proxy price movement.
- Polymarket RTDS/reference updates when the RTDS stream emits messages.
- DuckDB ingest-file rows under `ops.ingest_files`.
- Immutable raw Parquet files under `data/raw/`.

## Source Rules

- Polymarket website chart prices are not model truth.
- Coinbase is the first live proxy feed for BTC/ETH price movement.
- Polymarket RTDS is the first settlement/reference feed candidate.
- Binance.com is disabled by default on this machine because it returned `HTTP 451`.
- Every source event must preserve both source timestamp and local receive timestamp.
- Raw writes are crash-durable: `.parquet.tmp` files are atomically published and orphaned temporary files are cleaned at startup.
- WebSocket outages are handled with capped reconnect backoff; other sources continue running when one feed disconnects.

## Safety

Part Two does not trade, does not build model probabilities, and does not place orders.

## Docker/VPS Migration Requirements

If the collector moves to Docker on a VPS, the deployment must account for:

- secrets: no API keys, wallet keys, tokens, or `.env` files baked into images;
- persistent data: `data/raw`, `data/db`, logs, model artifacts, and config snapshots mounted outside the container;
- clock sync: host NTP/chrony/systemd-timesyncd enabled, with source timestamp and observed timestamp stored;
- network reconnects: source-specific reconnect backoff so one failing feed does not kill the whole collector;
- process restarts: Docker/systemd restart policy plus startup cleanup of orphaned temporary files;
- order kill switch: a persistent external kill state before any trading container exists;
- disk durability: atomic Parquet writes, archive sentinel, disk-space checks, and backup/snapshot policy;
- server monitoring: liveness, source freshness, disk usage, restart count, clock drift, and latest write time;
- latency measurement: WebSocket message age, quote age, API round-trip time, and server-to-venue latency;
- API auth: read-only credentials separated from future trading credentials;
- private key handling: no private keys in images, logs, notebooks, or committed files; future live keys require hot-wallet limits and permission checks.

## Restart Behavior

The draft systemd unit is `ops/systemd/polymarket-live-collector.service`.
It is read-only and exists to restart collection after process failure,
Wi-Fi recovery, reboot, or power loss. It should not be installed until
the 10-second live smoke command works locally.

The service uses:

- `After=network-online.target` and `Wants=network-online.target` so it waits for networking.
- `RequiresMountsFor=/home/spoon/polymarket/data/raw` so it does not write to the wrong path.
- `Restart=on-failure` and `RestartSec=15` so transient failures do not require manual restart.
- `TimeoutStopSec=60` so shutdown has time to flush buffered Parquet files.
