# Part Two Live Collectors

Part Two originally turned the Part One data foundation into Python read-only
live collection. That Python collector is now retired. Treat this document as
historical design context only; the active live path is the Rust SDK state-manager runtime.
`polymarket-engine collect`, the legacy Docker entrypoint, and the legacy
systemd unit fail closed so the old framework cannot be restarted by accident.

## Active 5m State-Manager Command

The current operational scope is BTC/ETH 5m current, next, and next-next windows.
15m support remains a planned modeling horizon, but the active
always-on runtime is intentionally 5m-only until the warm-state path and
durable persistence are stable.

```bash
cd rust
cargo run -p polymarket-live-probe -- \
  --mode state-manager \
  --assets BTC,ETH \
  --interval 5m \
  --prewarm-windows 3 \
  --run-for-seconds 30 \
  --out ../reports/live_probe/state_manager.json
```

Verify the generated report:

```bash
python3 scripts/verify_state_manager_report.py reports/live_probe/state_manager.json
```

## Always-On Docker Runtime

The deployed spoon runtime uses `deploy/collector/docker-compose.yml` and
`deploy/collector/collector-entrypoint.sh`. It starts the Rust binary with:

```bash
polymarket-live-probe \
  --mode state-manager \
  --assets BTC,ETH \
  --interval 5m \
  --prewarm-windows 2 \
  --forever \
  --state-snapshot-dir /var/lib/polymarket/raw/polymarket_state_manager/state_snapshot \
  --out /var/lib/polymarket/live/status.json
```

The runtime remains read-only. It keeps the current, next, and next-next 5m
contracts warm so rollover does not require contract discovery on the hot path.

The Rust state-manager status file records:

- BTC/ETH 5m current, next, and next-next contract windows.
- Up and Down token ids discovered before rollover.
- Polymarket CLOB WebSocket top-of-book state for warmed token ids.
- REST order-book backup snapshots during refresh.
- Polymarket RTDS Chainlink BTC/USD and ETH/USD reference ticks.
- Source/order-book freshness rows.
- WebSocket connection status for Chainlink and CLOB streams.
- Health flags that force the checker to fail closed.
- Append-only UTC-hour state snapshots under
  `/var/lib/polymarket/raw/polymarket_state_manager/state_snapshot`.

Current caveat: the Rust state-manager now persists replayable state snapshots,
but it still does not persist every raw WebSocket event into Parquet or
normalized DuckDB tables. That full raw-event persistence step remains required
before long-run research labels and probability backtests can rely on the Rust
runtime as the sole data source.

## Source Rules

- Polymarket website chart prices are not model truth.
- Proxy exchange feeds are quality-check inputs, not settlement proxies.
- Polymarket RTDS Chainlink is the first settlement/reference feed candidate.
- Polymarket RTDS Binance can improve source-disagreement diagnostics, but it still must not replace Chainlink for settlement, volatility, or `sigma_tau`.
- Volatility and `sigma_tau` must use only the Chainlink settlement/reference rows from `polymarket_rtds_chainlink`.
- Coinbase, Binance, and other proxies are for source-disagreement checks and feed-health diagnostics, not realized-volatility construction.
- A historical proxy row that exactly matches the same timestamped Chainlink value may be kept as validation evidence. It must not become an additional realized-volatility observation, because that would double-count one move.
- Binance.com is disabled by default on this machine because it returned `HTTP 451`.
- Every source event must preserve both source timestamp and local receive timestamp.
- The active Rust runtime writes append-only state snapshots. Full raw Parquet event persistence remains a required follow-up before replay/backtest work.
- WebSocket outages are handled with capped reconnect backoff; other sources continue running when one feed disconnects.
- RTDS subscribes to all Chainlink crypto symbols and filters locally to configured assets, because filtered multi-symbol subscriptions can omit live ETH updates. It also subscribes to per-symbol RTDS Binance proxy filters for the configured assets.

## Safety

Part Two does not trade, does not build model probabilities, and does not place orders.

## Retention Policy

State snapshots should be retained hot for 90 days with the rest of raw data. Full raw event data should also be retained hot for 90 days once Rust event persistence is added. Hot raw data should include Polymarket market snapshots, CLOB market WebSocket events, REST order-book backup snapshots, RTDS Chainlink price updates, proxy price updates, source errors, and raw collector payloads.

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
