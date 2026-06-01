# Part Two Live Collectors

Part Two originally turned the Part One data foundation into Python read-only
live collection. That Python collector is now retired. Treat this document as
historical design context only; the active live path is the Rust SDK runtime.
`polymarket-engine collect`, the legacy Docker entrypoint, and the legacy
systemd unit fail closed so the old framework cannot be restarted by accident.

## First Supported Command

```bash
mkdir -p data/raw
touch data/raw/.polymarket_archive_root
uv run polymarket-engine collect --assets BTC,ETH --duration 10
```

## Always-On Local Command

```bash
uv run polymarket-engine collect \
  --assets BTC,ETH \
  --intervals 5m,15m \
  --forever \
  --windows-to-track 2 \
  --snapshot-interval 1 \
  --market-refresh-interval 30 \
  --raw-root data/raw \
  --duckdb-path data/db/polymarket.duckdb \
  --status-path data/live/status.json
```

This tracks only the accepted BTC/ETH UP/DOWN contracts for the current and
next 5-minute and 15-minute windows. The collector remains read-only.

In another terminal:

```bash
uv run polymarket-engine monitor \
  --duckdb-path data/db/polymarket.duckdb \
  --status-path data/live/status.json \
  --refresh 1 \
  --limit 8
```

The monitor prefers the atomic status file so it can run while DuckDB is being
written by the collector. DuckDB remains the durable normalized store.

The first network runner records:

- Polymarket BTC/ETH 5-minute and 15-minute market snapshots discovered by deterministic slugs.
- Polymarket CLOB order book snapshots for Up and Down token ids.
- Coinbase BTC/ETH ticker updates for live proxy price movement.
- Polymarket RTDS Chainlink reference updates from `crypto_prices_chainlink`.
- Polymarket RTDS Binance proxy updates from `crypto_prices` for BTC/USDT and ETH/USDT.
- DuckDB ingest-file rows under `ops.ingest_files`.
- Normalized-table counts/latest timestamps and source/orderbook freshness rows in the status file.
- Immutable raw Parquet files under `data/raw/`.

## Source Rules

- Polymarket website chart prices are not model truth.
- Coinbase is a live exchange proxy for BTC/ETH price movement, not a settlement proxy.
- Polymarket RTDS Chainlink is the first settlement/reference feed candidate.
- Polymarket RTDS Binance is collected as an additional no-auth proxy. It can improve source-disagreement diagnostics, but it still must not replace Chainlink for settlement, volatility, or `sigma_tau`.
- Volatility and `sigma_tau` must use only the Chainlink settlement/reference rows from `polymarket_rtds_chainlink`.
- Coinbase, Binance, and other proxies are for source-disagreement checks and feed-health diagnostics, not realized-volatility construction.
- A historical proxy row that exactly matches the same timestamped Chainlink value may be kept as validation evidence. It must not become an additional realized-volatility observation, because that would double-count one move.
- Binance.com is disabled by default on this machine because it returned `HTTP 451`.
- Every source event must preserve both source timestamp and local receive timestamp.
- Raw writes are crash-durable: `.parquet.tmp` files are atomically published and orphaned temporary files are cleaned at startup.
- WebSocket outages are handled with capped reconnect backoff; other sources continue running when one feed disconnects.
- RTDS subscribes to all Chainlink crypto symbols and filters locally to configured assets, because filtered multi-symbol subscriptions can omit live ETH updates. It also subscribes to per-symbol RTDS Binance proxy filters for the configured assets.

## Safety

Part Two does not trade, does not build model probabilities, and does not place orders.

## Retention Policy

Raw event data is retained hot for 90 days. Hot raw data includes Polymarket market snapshots, CLOB market WebSocket events, REST order-book backup snapshots, RTDS Chainlink price updates, RTDS Binance proxy price updates, Coinbase price ticks, source errors, and raw collector payloads.

After 90 days, raw events should be compacted into replay-safe research tables before deletion is enabled. The compact layer should preserve 1-second price bars, 1-second top-of-book rows, source freshness, contract windows, rule hashes, decision states, and final labels. Automatic deletion remains disabled until replay tests prove compacted tables reproduce the same as-of state for sampled contracts.

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
