# Live Data Plumbing Report - Spoon

Date: 2026-06-02

Scope: read-only inspection of `/home/spoon/polymarket`, `/home/spoon/polymarket-data`, and a 300-second live sample of `/home/spoon/polymarket-data/live/status.json`.

No Spoon data was deleted during this report.

## Short Answer

Sections 1-4 are not fully closed.

- Section 1 contract parsing/storage is mostly implemented, but the live Rust contract state is not currently normalized into DuckDB after the latest Rust runtime change.
- Section 2 data/as-of state is partially live: Rust status and raw journals are fresh, but normalized DuckDB is stale relative to the live collector.
- Section 3 probability outputs are not implemented yet. `p_finish`, `p_no_touch`, and executable edge should not start until the data path is replay-safe.
- Section 4 Monte Carlo path generation is not implemented yet. The method is documented, and volatility/sigma_tau support exists, but the live replay bridge is still the blocker.

The immediate hole is the bridge from Rust raw/status data into replay-safe normalized rows and `DecisionState` snapshots.

## Spoon Runtime State

- Repo path: `/home/spoon/polymarket`
- Active branch on Spoon: `main`
- Spoon HEAD: `531387b Validate raw websocket journal freshness`
- Collector container: `polymarket-rust-collector-collector-1`
- Container status during inspection: `Up 33 minutes (healthy)`
- Status verifier: passed
- State-manager verifier: passed
- Raw websocket journal freshness verifier: passed

Latest verifier output:

```text
ok mode=state-manager current=2 next=2 orderbooks=8 subscriptions=8 websocket_status=2 health_flags=0
health_flags: none
```

## Live 5-Minute Status Sample

Sample source: `/home/spoon/polymarket-data/live/status.json`

- Duration: 300.829 seconds
- Samples: 300
- Read errors: 0
- Health flags: 0 throughout
- Chainlink price rows: 2 throughout, BTC/USD and ETH/USD
- CLOB order books: 8 throughout
- CLOB subscriptions: 8 throughout
- Current contracts: 2 throughout
- Next contracts: ranged 0 to 2 during rollover
- Next-next contracts: 0 throughout

Latency and freshness derived from timestamps:

| Metric | Min ms | P50 ms | P95 ms | Max ms |
|---|---:|---:|---:|---:|
| Status file age | 7.949 | 583.555 | 1084.278 | 1524.234 |
| Chainlink observed age | 61.420 | 1099.178 | 1885.610 | 2538.802 |
| Order-book observed age | 27.554 | 696.917 | 1449.939 | 6124.889 |
| Chainlink event-to-observed lag | 822.111 | 1259.156 | 1936.854 | 2231.183 |
| Order-book event-to-observed lag | 64.173 | 1885.706 | 10658.914 | 12098.652 |

Interpretation:

- Chainlink freshness is acceptable for current monitoring thresholds.
- CLOB top-of-book freshness is usually sub-2 seconds but had a 6.1 second observed-age max in the sample.
- CLOB event-to-observed lag can spike above 10 seconds. That is a serious measurement to keep in the report, because future execution/probability must either block, haircut, or demand more edge when book events are old.
- The live status schema has no `latency_marks` field right now. Latency is inferred from status timestamps and event/observed timestamps.

## Live Contracts Seen During Sample

Current window during the sample:

| Asset | Window UTC | UP token | DOWN token |
|---|---|---|---|
| BTC | 2026-06-02T05:35:00Z to 2026-06-02T05:40:00Z | `401665860578...471085663` | `516638297108...444554562` |
| ETH | 2026-06-02T05:35:00Z to 2026-06-02T05:40:00Z | `848079591937...502127237` | `110412734892...399604598` |

Next window during the sample:

| Asset | Window UTC | UP token | DOWN token |
|---|---|---|---|
| BTC | 2026-06-02T05:40:00Z to 2026-06-02T05:45:00Z | `182007946429...39117448` | `103263470364...938707880` |
| ETH | 2026-06-02T05:40:00Z to 2026-06-02T05:45:00Z | `271147892711...912676676` | `615797311692...00342604` |

Order-book slugs seen included:

- `btc-updown-5m-1780378500`
- `eth-updown-5m-1780378500`
- `btc-updown-5m-1780378800`
- `eth-updown-5m-1780378800`

Older expiring slugs were still visible for a short rollover period:

- `btc-updown-5m-1780378200`
- `eth-updown-5m-1780378200`

## What Is Being Read And Written

Live read inputs:

- Polymarket RTDS Chainlink BTC/USD and ETH/USD.
- Polymarket CLOB market WebSocket best bid/ask for warmed UP/DOWN token ids.
- REST order-book backup snapshots during refresh.
- Contract discovery for BTC/ETH 5m windows.

Live status output:

- `/home/spoon/polymarket-data/live/status.json`

Fresh Rust raw journals:

- `/home/spoon/polymarket-data/raw/polymarket_state_manager/state_snapshot/date=2026-06-02/hour=04/state-manager.jsonl`
- `/home/spoon/polymarket-data/raw/polymarket_state_manager/state_snapshot/date=2026-06-02/hour=05/state-manager.jsonl`
- `/home/spoon/polymarket-data/raw/polymarket_clob_market_ws/best_bid_ask/date=2026-06-02/hour=04/events.jsonl`
- `/home/spoon/polymarket-data/raw/polymarket_clob_market_ws/best_bid_ask/date=2026-06-02/hour=05/events.jsonl`
- `/home/spoon/polymarket-data/raw/polymarket_rtds_chainlink/price_update/date=2026-06-02/hour=04/events.jsonl`
- `/home/spoon/polymarket-data/raw/polymarket_rtds_chainlink/price_update/date=2026-06-02/hour=05/events.jsonl`

Older Python/Parquet collection output is also still present under `raw/`.

## Current Data Inventory On Spoon

Total data directory size:

```text
556M /home/spoon/polymarket-data
```

Top-level files:

```text
/home/spoon/polymarket-data/db/polymarket.duckdb      152317952 bytes
/home/spoon/polymarket-data/db/polymarket.duckdb.wal  594 bytes
/home/spoon/polymarket-data/live/status.json          135966 bytes
```

Raw data groups:

| Raw group | Files | Bytes |
|---|---:|---:|
| `coinbase_advanced_ws/ticker` | 4596 | 25053243 |
| `polymarket_clob/orderbook_snapshot` | 3136 | 52465870 |
| `polymarket_clob_market_ws/best_bid_ask` | 2 | 45713374 |
| `polymarket_market_ws/orderbook_snapshot` | 54 | 298378 |
| `polymarket_market_ws/top_of_book` | 609 | 2456165 |
| `polymarket_markets/crypto_5m_events_snapshot` | 1 | 7602 |
| `polymarket_markets/crypto_5m_markets_snapshot` | 1234 | 7865229 |
| `polymarket_markets/crypto_updown_markets_snapshot` | 74 | 496599 |
| `polymarket_markets/markets_snapshot` | 1 | 8261 |
| `polymarket_rtds_chainlink/price_update` | 4057 | 17974505 |
| `polymarket_rtds_crypto/price_update` | 857 | 3558515 |
| `polymarket_state_manager/state_snapshot` | 2 | 248764995 |

## Normalized DuckDB State

DuckDB path:

```text
/home/spoon/polymarket-data/db/polymarket.duckdb
```

Table counts:

| Table | Rows |
|---|---:|
| `core.contract_rules` | 466 |
| `core.contracts` | 916 |
| `core.orderbook_snapshots` | 78936 |
| `core.price_ticks` | 487952 |
| `features.asof_state_inputs` | 0 |
| `features.decision_snapshots` | 0 |
| `ops.ingest_checkpoints` | 0 |
| `ops.ingest_files` | 14608 |
| `ops.retention_manifests` | 14523 |
| `validation.contract_labels` | 0 |
| `validation.decision_labels` | 0 |

Normalized price rows by source:

| Source | Symbol | Rows | Min event_ts | Max event_ts |
|---|---|---:|---|---|
| `coinbase_advanced_ws` | `BTC-USD` | 170508 | 2026-06-01 01:34:10 UTC | 2026-06-02 01:32:42 UTC |
| `coinbase_advanced_ws` | `ETH-USD` | 152075 | 2026-06-01 01:34:10 UTC | 2026-06-02 01:32:42 UTC |
| `polymarket_rtds_chainlink` | `BTC/USD` | 73752 | 2026-06-01 01:33:22 UTC | 2026-06-02 01:32:43 UTC |
| `polymarket_rtds_chainlink` | `ETH/USD` | 34832 | 2026-06-01 01:33:22 UTC | 2026-06-02 01:32:43 UTC |
| `polymarket_rtds_crypto` | `BTC/USDT` | 44325 | 2026-06-01 15:12:32 UTC | 2026-06-02 01:32:44 UTC |
| `polymarket_rtds_crypto` | `ETH/USDT` | 12460 | 2026-06-01 15:12:32 UTC | 2026-06-02 00:49:41 UTC |

Normalized order-book rows stop around `2026-06-02 00:46 UTC`, while live Rust status/raw data is current around `2026-06-02 05:33-05:40 UTC`.

That means the normalized database is stale relative to the live collector.

## Current Holes Before Probability

1. Rust raw journals are now being written, but they are not yet normalized into `core.price_ticks`, `core.orderbook_snapshots`, and `features.asof_state_inputs`.
2. `features.asof_state_inputs` has zero rows.
3. `features.decision_snapshots` has zero rows.
4. DuckDB normalized rows are stale by several hours compared with the active Rust collector.
5. The deployed runtime is tracking current and next 5m windows; `next_next` was absent throughout this sample.
6. No `p_finish`, `p_no_touch`, executable edge, Monte Carlo output, or XGBoost output should be added until the Rust raw-to-normalized replay bridge is fixed.
7. The status schema does not expose first-class latency marks; latency is derived from timestamps.

## Fresh-Slate Wipe Plan

Destructive target:

```text
/home/spoon/polymarket-data/raw/*
/home/spoon/polymarket-data/db/*
/home/spoon/polymarket-data/live/*
/home/spoon/polymarket-data/logs/*
```

Preserve or recreate:

```text
/home/spoon/polymarket-data/raw/.polymarket_archive_root
```

Safe sequence:

1. Stop the collector container.
2. Delete all collected data under `raw`, `db`, `live`, and `logs`.
3. Recreate the four directories and the raw archive sentinel.
4. Redeploy/restart the collector.
5. Verify fresh status, fresh raw websocket journals, and an empty/new database.

Do not delete repo code, deploy scripts, Docker image definitions, or source files.
