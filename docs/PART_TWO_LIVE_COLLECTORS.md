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
  --decision-snapshot-dir ../data/raw \
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
  --prewarm-windows 3 \
  --forever \
  --state-snapshot-dir /var/lib/polymarket/raw/polymarket_state_manager/state_snapshot \
  --decision-snapshot-dir /var/lib/polymarket/raw \
  --out /var/lib/polymarket/live/status.json
```

The runtime remains read-only. It keeps the current, next, and next-next 5m
contracts warm so rollover does not require contract discovery on the hot path.
The spoon deployment tracks current, next, and next-next 5m windows with
`POLYMARKET_PREWARM_WINDOWS=3`. The collector process owns live WebSocket state
and append-only raw JSONL journals. A normalizer sidecar reads those raw
journals, writes normalized DuckDB rows, builds current/next `DecisionState`
snapshots, and refreshes `data/live/normalized_health.json`.

The Rust state-manager status file records:

- BTC/ETH 5m current, next, and next-next contract windows.
- Up and Down token ids discovered before rollover.
- Polymarket CLOB WebSocket top-of-book state for warmed token ids.
- REST order-book backup snapshots during refresh.
- Polymarket RTDS Chainlink BTC/USD and ETH/USD reference ticks.
- Source/order-book freshness rows.
- WebSocket connection status for Chainlink and CLOB streams.
- First-class `latency_marks` for Chainlink and order-book observed age plus
  event-to-observed lag.
- `hot_decision_telemetry` when hot decision journaling is enabled.
- Health flags that force the checker to fail closed.
- Append-only raw Chainlink RTDS and Polymarket CLOB WebSocket journals under
  `/var/lib/polymarket/raw/polymarket_rtds_chainlink/price_update` and
  `/var/lib/polymarket/raw/polymarket_clob_market_ws/best_bid_ask`.
- Append-only UTC-hour state snapshots under
  `/var/lib/polymarket/raw/polymarket_state_manager/state_snapshot`.
- Append-only hot decision `DecisionState` snapshots under
  `/var/lib/polymarket/raw/polymarket_decision_state/hot_state`.

Durable boundary: the Rust state-manager owns hot read-only collection and
append-only raw journals. Hot decision construction stays inside the Rust
state-manager in memory; DuckDB owns normalized replay/research tables and
must not sit on the live decision path. Use
`polymarket-engine normalize-rust-events` to convert Rust raw journals into
`core.price_ticks`, `core.orderbook_snapshots`, and ingest manifests. Probability
work remains blocked until normalized replay rows are current and reproducible
from the raw journals.

By default, `normalize-rust-events` reads direct Chainlink/CLOB WebSocket event
journals. Use `--include-state-snapshots` only for recovery/audit backfills,
because state snapshots repeat the latest known price/book state every second.
Use `polymarket-engine build-current-decision-states` after normalization to
write exact current as-of `DecisionState` snapshots into DuckDB. That
`DecisionState` snapshot is the live pre-probability boundary: future decisions
should persist this exact state first, while raw Chainlink/CLOB event journals
remain the replay/audit trail. Use `polymarket-engine write-normalized-health`
after snapshot building to publish `normalized_health.json` with final DuckDB
table counts and latest timestamps.

The safe hot replay gate verifies append-only hot `DecisionState` rows against a
copied read-only snapshot of the normalized DuckDB database. This avoids
normalizer DB lock collisions, does not pause collector or normalizer, and
must not enter the hot live decision path.

Database expectation: `core.price_ticks`, `core.orderbook_snapshots`, and
`features.asof_state_inputs` should stay fresh while the normalizer sidecar is
running. `core.contract_rules remains empty` for Rust status-derived contracts
because the Rust status file does not contain full venue rule text; do not
synthesize rule text. `features.decision_snapshots remains empty until probability`
because no probability model or decision policy exists yet.

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
- The active Rust runtime writes append-only raw WebSocket journals and state
  snapshots. Normalized DuckDB replay rows are produced by
  `polymarket-engine normalize-rust-events`; do not restart the retired Python
  collector to fill those tables.
- Live decisions should persist exact `DecisionState` snapshots before
  probability work. They do not need to synchronously wait for every raw event to
  be normalized, because raw event journals remain append-only and replayable.
- WebSocket outages are handled with capped reconnect backoff; other sources continue running when one feed disconnects.
- RTDS subscribes to all Chainlink crypto symbols and filters locally to configured assets, because filtered multi-symbol subscriptions can omit live ETH updates. It also subscribes to per-symbol RTDS Binance proxy filters for the configured assets.

## Safety

Part Two does not trade, does not build model probabilities, and does not place orders.

## Retention Policy

State snapshots and raw WebSocket event journals should be retained hot for 90
days with the rest of raw data. Hot raw data should include Polymarket market
snapshots, CLOB market WebSocket events, REST order-book backup snapshots, RTDS
Chainlink price updates, proxy price updates, source errors, and raw collector
payloads.

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

Hot decision restart policy: after a Rust process restart, current-window hot
`DecisionState` rows are explicitly blocked when the process cannot prove the
window-start Chainlink threshold from in-memory ticks. Those rows stay visible
in hot JSONL and replay reports with `MissingThreshold` and
`RestartWarmupBlocked` until the next warmed window starts, unless the threshold
tick is observed in memory. The hot path must not recover this threshold from
raw journals or DuckDB; raw/DuckDB recovery is replay-only.
