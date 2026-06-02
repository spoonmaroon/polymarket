# Current System Architecture Report

Date: 2026-06-02

Source of truth for this report:

- Spoon repo: `/home/spoon/polymarket`
- Spoon branch: `main`
- Spoon HEAD: `531387b Validate raw websocket journal freshness`
- Live data root: `/home/spoon/polymarket-data`
- Live collector: Docker service `polymarket-rust-collector-collector-1`

This report explains the system as it exists now, after the migration to the Rust state-manager runtime. If "REST migration" meant something else, the current reality is: REST is still used for discovery and backup snapshots, but the hot live runtime is Rust + WebSockets.

## One-Sentence Summary

The current system is a read-only Rust live collector on Spoon that keeps BTC/ETH 5-minute Polymarket contracts warm using WebSockets, writes a fresh atomic status file and raw JSONL journals, while the older Python/DuckDB research layer still exists but is not currently caught up with the live Rust stream.

## Current Big Picture

```text
Polymarket Gamma REST
  -> discover BTC/ETH 5m market slugs and token ids

Polymarket CLOB REST
  -> seed/refresh full order-book snapshots

Polymarket CLOB WebSocket
  -> hot best-bid/best-ask updates for warmed token ids

Polymarket RTDS Chainlink WebSocket
  -> BTC/USD and ETH/USD settlement/reference ticks

Rust state manager
  -> keeps warm state in memory
  -> writes /home/spoon/polymarket-data/live/status.json
  -> appends raw JSONL event journals under /home/spoon/polymarket-data/raw/

Python/DuckDB research layer
  -> stores normalized rows and replay builders
  -> currently stale relative to live Rust raw/status data
```

## What Runs On Spoon

The deployed service is defined by:

- `deploy/collector/docker-compose.yml`
- `deploy/collector/collector-entrypoint.sh`
- `scripts/deploy.sh`

The Docker entrypoint launches:

```text
polymarket-live-probe
  --mode state-manager
  --assets BTC,ETH
  --interval 5m
  --prewarm-windows 2
  --forever
  --status-interval-ms 1000
  --prewarm-before-expiry-ms 30000
  --stale-chainlink-after-ms 5000
  --stale-orderbook-after-ms 30000
  --rest-backup-interval-ms 15000
  --state-snapshot-dir /var/lib/polymarket/raw/polymarket_state_manager/state_snapshot
  --raw-event-dir /var/lib/polymarket/raw
  --raw-event-buffer-size 16384
  --out /var/lib/polymarket/live/status.json
```

The host mounts these container paths:

| Container path | Spoon path | Purpose |
|---|---|---|
| `/var/lib/polymarket/raw` | `/home/spoon/polymarket-data/raw` | Raw event/state journals |
| `/var/lib/polymarket/db` | `/home/spoon/polymarket-data/db` | DuckDB normalized/research database |
| `/var/lib/polymarket/live` | `/home/spoon/polymarket-data/live` | Atomic live status file |
| `/var/lib/polymarket/logs` | `/home/spoon/polymarket-data/logs` | Logs |

The collector refuses to start if this sentinel is missing:

```text
/home/spoon/polymarket-data/raw/.polymarket_archive_root
```

That prevents accidental writes to the wrong data root.

## What "Rust Migration" Changed

Before: the important live collector path was Python-oriented. It wrote raw Parquet and normalized DuckDB rows.

Now: the live hot path is Rust:

- Rust discovers the contracts.
- Rust subscribes to CLOB WebSocket best bid/ask updates.
- Rust subscribes to RTDS Chainlink BTC/USD and ETH/USD.
- Rust keeps the warm state in memory.
- Rust writes the status file.
- Rust appends raw JSONL journals.

The old Python collector is retired and should not be restarted.

The current missing bridge is not live collection. The missing bridge is:

```text
Rust raw JSONL/state status
  -> normalize into DuckDB
  -> build replay-safe DecisionState rows
  -> then calculate probability outputs
```

## REST Versus WebSockets

This is the clean way to think about it:

```text
REST = discovery, seeding, backup snapshots
WebSocket = hot live updates
DuckDB = research/replay truth
Status JSON = operator/live health state
Raw JSONL = durable Rust event trail
```

REST is not the hot data feed. It is used for:

- Gamma market discovery: find active `btc-updown-5m-<epoch>` and `eth-updown-5m-<epoch>` markets.
- CLOB order-book fetches: seed the book state and periodically refresh backup snapshots every 15 seconds.

WebSockets are used for:

- Chainlink settlement/reference prices.
- CLOB best-bid/best-ask top-of-book updates.

That is the right direction for future low-latency VPS migration: keep the hot lane on WebSockets, use REST to seed and recover.

## The Rust Runtime Modules

| File | Role |
|---|---|
| `rust/crates/polymarket-live-probe/src/main.rs` | CLI entrypoint; dispatches `probe` or `state-manager`; wires status output, raw journal, and snapshot journal |
| `state_manager.rs` | Main runtime loop; refreshes contracts, maintains warm state, builds status snapshots |
| `polymarket.rs` | Gamma REST discovery and CLOB REST order-book snapshots |
| `clob_ws.rs` | CLOB WebSocket best-bid/ask subscription manager and raw event conversion |
| `prices.rs` | RTDS Chainlink WebSocket subscription manager and price tick conversion |
| `book_state.rs` | In-memory top-of-book/order-book state keyed by token id |
| `report.rs` | JSON schema for status reports and WebSocket telemetry |
| `raw_event_journal.rs` | Append-only raw event JSONL writer |
| `snapshot_journal.rs` | Append-only state snapshot JSONL writer |
| `windows.rs` | 5m/15m window scheduling helpers |

Runtime data types live in `rust/crates/polymarket-runtime-types/src/`:

| File | Role |
|---|---|
| `contract.rs` | `ContractWindow`, `ContractToken`, `WarmedContract` |
| `orderbook.rs` | `NormalizedOrderBook`, best bid/ask, spread, depth |
| `price.rs` | `NormalizedPriceTick`, source disagreement |
| `state.rs` | `WarmStateSnapshot`, freshness rows, health flags |

## What Happens In One 5-Minute Window

Example for BTC/ETH 5m markets:

1. The runtime computes the current 5-minute epoch.
2. It builds expected slugs:
   ```text
   btc-updown-5m-<epoch>
   eth-updown-5m-<epoch>
   btc-updown-5m-<next_epoch>
   eth-updown-5m-<next_epoch>
   ```
3. It calls Polymarket Gamma REST for those slugs.
4. It extracts UP/DOWN CLOB token ids from each market.
5. It calls CLOB REST to seed the full order books.
6. It subscribes to CLOB WebSocket best-bid/ask for every warmed token id.
7. It subscribes to RTDS Chainlink for BTC/USD and ETH/USD.
8. Every second it writes a fresh status JSON.
9. Every status write can also append a state snapshot JSONL row.
10. Every hot WebSocket event can append a raw JSONL event row.
11. Near rollover, it refreshes market discovery so the next window is already warm.

The current active runtime attempts `prewarm-windows=2`, so the intended live set is:

```text
BTC current UP/DOWN
BTC next UP/DOWN
ETH current UP/DOWN
ETH next UP/DOWN
```

That produces 8 CLOB token subscriptions.

`next_next` exists in the status schema, but during the 2026-06-02 300-second sample it stayed empty because Spoon is configured with `prewarm-windows=2`.

## Live Status File

Path:

```text
/home/spoon/polymarket-data/live/status.json
```

Schema:

```text
rust-live-probe-state-manager-v1
```

Main fields:

| Field | Meaning |
|---|---|
| `generated_at` | When the status file was written |
| `elapsed_ms` | Runtime elapsed time |
| `current` | Current live 5m contracts |
| `next` | Next 5m contracts |
| `next_next` | Future third window, only present if warmed/configured |
| `chainlink_prices` | Latest BTC/USD and ETH/USD reference ticks |
| `orderbooks` | Current warmed token order-book/top-of-book states |
| `freshness` | Age/staleness rows for prices and order books |
| `health_flags` | Fail-closed flags; should be empty |
| `subscriptions` | CLOB token subscriptions |
| `websocket_status` | WebSocket connection and event telemetry |

Important: the status file is live operational state. It is not yet the same thing as normalized replay storage.

## Raw Journals

Rust currently writes raw JSONL journals here:

```text
/home/spoon/polymarket-data/raw/polymarket_state_manager/state_snapshot/date=YYYY-MM-DD/hour=HH/state-manager.jsonl
/home/spoon/polymarket-data/raw/polymarket_clob_market_ws/best_bid_ask/date=YYYY-MM-DD/hour=HH/events.jsonl
/home/spoon/polymarket-data/raw/polymarket_rtds_chainlink/price_update/date=YYYY-MM-DD/hour=HH/events.jsonl
```

What they mean:

- `state_snapshot`: full Rust status snapshot appended over time.
- `polymarket_clob_market_ws/best_bid_ask`: CLOB WebSocket top-of-book events.
- `polymarket_rtds_chainlink/price_update`: Chainlink BTC/USD and ETH/USD RTDS events.

The raw journal is durable enough to prove what the Rust runtime saw, but it still needs normalizers before it becomes replay-safe DuckDB state.

## DuckDB / Python Research Layer

DuckDB path:

```text
/home/spoon/polymarket-data/db/polymarket.duckdb
```

Python storage/replay files:

| File | Role |
|---|---|
| `src/polymarket_engine/storage/duckdb_store.py` | Writes contracts, prices, order books, as-of states, decisions, labels |
| `src/polymarket_engine/storage/schema.sql` | Table definitions |
| `src/polymarket_engine/features/state_builder.py` | Builds `DecisionState` from as-of observations |
| `src/polymarket_engine/features/state_replay.py` | Queries DuckDB at an `asof_ts` and builds replay state |
| `src/polymarket_engine/features/volatility.py` | Builds Chainlink-only volatility and `sigma_tau` snapshots |
| `src/polymarket_engine/status/normalized_health.py` | Writes a normalized DB health status file |

The Python layer is still the research/replay layer, not the active hot collector.

Current DuckDB truth from Spoon:

| Table | Rows |
|---|---:|
| `core.contract_rules` | 466 |
| `core.contracts` | 916 |
| `core.orderbook_snapshots` | 78936 |
| `core.price_ticks` | 487952 |
| `features.asof_state_inputs` | 0 |
| `features.decision_snapshots` | 0 |
| `validation.contract_labels` | 0 |
| `validation.decision_labels` | 0 |

Problem: DuckDB normalized rows stopped around `2026-06-02 01:32 UTC`, while live Rust status/raw journals were current around `2026-06-02 05:33-05:40 UTC` during inspection.

So the live collector is working, but the normalized research DB is not caught up to the Rust runtime.

## Health Checks

Docker health check runs:

```text
python3 /usr/local/bin/check_collector_status.py
  --status-path /var/lib/polymarket/live/status.json
  --max-status-age-seconds 30
  --max-price-age-ms 30000
  --max-orderbook-age-ms 30000
  --max-websocket-event-age-ms 30000
  --raw-root /var/lib/polymarket/raw
  --max-raw-event-age-ms 30000
```

It checks:

- status file exists and is fresh;
- Chainlink price rows exist and are fresh;
- order-book rows exist and are fresh;
- state-manager schema is correct;
- current and next BTC/ETH contracts exist;
- WebSocket sources are connected;
- WebSocket event age is acceptable;
- raw websocket journals are fresh;
- health flags are empty.

This check validates live operation, not backtest/replay readiness.

## How To Think About The System

There are four layers:

### 1. Live Runtime Layer

This is Rust on Spoon.

Job:

- stay connected;
- keep BTC/ETH 5m contracts warm;
- maintain current live top-of-book state;
- keep Chainlink reference prices fresh;
- write operator status and raw evidence.

This layer answers:

```text
What does the live system see right now?
```

### 2. Raw Evidence Layer

This is JSONL under `raw/`.

Job:

- preserve event/source timestamps;
- preserve observed timestamps;
- provide a durable trail from the Rust runtime.

This layer answers:

```text
What did the Rust collector receive, and when?
```

### 3. Normalized Replay Layer

This is Python + DuckDB.

Job:

- normalize raw events into typed tables;
- build `DecisionState` at as-of timestamps;
- enforce no-future-data rules;
- power backtests, labels, and model training.

This layer answers:

```text
What could the model have known at time t?
```

This layer is currently the weak link because the Rust raw journals are not yet fully normalized into DuckDB.

### 4. Probability / Decision Layer

This is planned, not live.

Job:

- calculate `p_finish`;
- calculate `p_no_touch`;
- calculate `z_path`;
- compare fair probability to executable price;
- block on stale/noisy/unsafe data;
- eventually produce paper decisions and labels.

This layer should not be built on live-only status fields. It should consume replay-safe `DecisionState` rows.

## What Is Current Versus Legacy

Current:

- Rust `polymarket-live-probe --mode state-manager`.
- Docker collector on Spoon.
- CLOB WebSocket best bid/ask.
- RTDS Chainlink WebSocket BTC/USD and ETH/USD.
- REST discovery and backup order-book refresh.
- Atomic live status JSON.
- Raw Rust JSONL journals.

Legacy or stale:

- Old Python `polymarket-engine collect` live collector.
- Older Parquet raw feeds from the Python path.
- DuckDB normalized tables from older ingestion that are not currently caught up with Rust live data.

Useful but not yet live:

- `DecisionState` builder.
- Chainlink-only `sigma_tau` volatility builder.
- Normalized health status writer.
- Replay/state construction tests.

Not built yet:

- Rust raw journal normalizer.
- Live `DecisionState` snapshots from Rust status/raw data.
- Probability outputs.
- Monte Carlo.
- XGBoost.
- Paper trading.
- Real trading.

## The Current Architecture Hole

The live collector and the research layer are no longer perfectly connected.

Right now:

```text
Rust live collector -> status.json + raw JSONL journals
```

Older/partial path:

```text
Python ingestion -> DuckDB normalized rows
```

Missing/currently needed:

```text
Rust raw JSONL journals
  -> Rust event normalizer
  -> DuckDB core.price_ticks + core.orderbook_snapshots
  -> features.asof_state_inputs
  -> features.decision_snapshots
```

This is why Sections 1-4 are not fully done. The live raw capture is good enough to continue plumbing, but not good enough to start probability honestly.

## Recommended Next Build Order

1. Fresh-slate Spoon data wipe, if desired.
2. Restart live Rust collector and verify raw/status freshness.
3. Implement Rust raw event normalizer into DuckDB:
   - Chainlink raw JSONL -> `core.price_ticks`
   - CLOB best-bid/ask raw JSONL -> `core.orderbook_snapshots`
   - state snapshots -> optional audit table or direct snapshot parser
4. Add replay equivalence tests:
   - every row must satisfy `event_ts <= asof_ts` and `observed_ts <= asof_ts`;
   - no future Chainlink/order-book data enters `DecisionState`.
5. Start writing `features.asof_state_inputs`.
6. Only then implement first probability outputs.

## Plain-English Mental Model

The system is not yet a trading model. It is a live evidence machine.

It currently watches the next few BTC/ETH 5-minute binary contracts, keeps their Polymarket order books warm, watches the Chainlink reference price, and writes down what it sees.

The next job is to make those raw observations replayable. Once replay is reliable, the probability model can ask:

```text
At this exact second, using only what was known then,
what was the current contract, price, order book, volatility, and time left?
```

That replay state becomes the input to `p_finish`, `p_no_touch`, and edge.

Until that bridge is finished, the live system can collect data, but it should not claim to price contracts.
