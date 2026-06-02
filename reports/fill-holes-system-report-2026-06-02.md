# Fill-Holes System Report

Date: 2026-06-02

Scope: local implementation on branch `codex/rust-raw-normalizer`, read-only
Spoon inspection, and no Spoon data deletion.

## Verdict

Sections 1-4 are still not fully "done" in the sense needed for probability
work, but the largest data-path hole found in the 2026-06-02 reports is now
filled locally.

The active architecture is:

```text
Polymarket Gamma REST
  -> Rust state manager contract discovery
Polymarket CLOB REST
  -> seed/backup order-book snapshots
Polymarket CLOB WebSocket
  -> hot top-of-book events
Polymarket RTDS Chainlink WebSocket
  -> BTC/USD and ETH/USD settlement/reference ticks
Rust raw journals + state snapshots
  -> DuckDB normalizer
DuckDB core tables
  -> as-of replay and future DecisionState/probability outputs
```

REST is discovery and backup. WebSockets are the hot live path. DuckDB is the
replay/research layer. The retired Python collector should stay stopped.

## What Was Fixed

1. Added a Rust raw-event normalizer:
   - `src/polymarket_engine/ingestion/rust_event_normalizer.py`
   - Converts `polymarket_rtds_chainlink/price_update` JSONL into
     `core.price_ticks`.
   - Converts `polymarket_clob_market_ws/best_bid_ask` JSONL into
     `core.orderbook_snapshots`.
   - Can normalize state-manager snapshot files explicitly for audit/recovery.
   - Records `ops.ingest_files` and retention manifests using raw file sha256.

2. Added CLI entrypoints:
   - `uv run polymarket-engine normalize-rust-events --raw-root ... --duckdb-path ...`
   - `uv run polymarket-engine write-normalized-health --duckdb-path ... --out ...`

3. Added a normalized DuckDB health status writer:
   - `src/polymarket_engine/health/normalized_status.py`
   - Emits `polymarket-normalized-health-v1`.
   - Reports normalized table row counts and latest timestamps.

4. Updated docs:
   - `README.md`
   - `docs/PART_TWO_LIVE_COLLECTORS.md`
   - `docs/SPOON_DEPLOYMENT.md`
   - `tests/docs/test_active_runtime_docs.py`

5. Created a reusable report skill:
   - `/Users/goon/.codex/skills/polymarket-system-report/SKILL.md`

## Important Design Choice

The normalizer defaults to direct WebSocket journals only.

That means:

```text
default = Chainlink raw events + CLOB raw events
optional = state-manager snapshots with --include-state-snapshots
```

Reason: state snapshots repeat the latest known price/book state every second.
They are useful for audit and recovery, but direct WebSocket journals preserve
cleaner event lineage for replay.

## Spoon Five-Minute Live Sample

Source: `/home/spoon/polymarket-data/live/status.json`

Duration: 300.829 seconds. Samples: 300. Read errors: 0. Health flags: 0.

| Metric | P50 ms | P95 ms | Max ms |
|---|---:|---:|---:|
| Status file age | 583.555 | 1084.278 | 1524.234 |
| Chainlink observed age | 1099.178 | 1885.610 | 2538.802 |
| Order-book observed age | 696.917 | 1449.939 | 6124.889 |
| Chainlink event-to-observed lag | 1259.156 | 1936.854 | 2231.183 |
| CLOB event-to-observed lag | 1885.706 | 10658.914 | 12098.652 |

Interpretation:

- Chainlink was healthy in the sample.
- CLOB was usually fresh enough for monitoring, but lag spikes above 10 seconds
  must become future decision gates or edge haircuts.
- `next_next` was empty in the sample because Spoon was running
  `--prewarm-windows 2`, not 3.

## Current Spoon Data Inventory

Total `/home/spoon/polymarket-data`: `556M`.

Raw groups from the read-only inspection:

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

DuckDB counts from the same inspection:

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

## Sections 1-4 Status After This Fix

| Section | Status | Hole |
|---|---|---|
| 1. Contract rules and settlement source | Mostly implemented | Need fresh-slate live contract normalization after reset/deploy. |
| 2. Data and as-of state | Improved | Raw Rust Chainlink/CLOB can now be normalized into DuckDB locally; Spoon still needs coordinated deploy/run. |
| 3. Core probability outputs | Not started | Correctly blocked until normalized replay rows and `DecisionState` snapshots are current. |
| 4. Monte Carlo path generation | Not started | Method is documented; implementation should wait for replay-safe live state. |

## Remaining Holes

1. Deploy this branch to Spoon, then run:
   ```bash
   uv run polymarket-engine normalize-rust-events \
     --raw-root /home/spoon/polymarket-data/raw \
     --duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb
   uv run polymarket-engine write-normalized-health \
     --duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb \
     --out /home/spoon/polymarket-data/live/normalized_health.json
   ```
2. Build the next bridge from normalized rows to current `DecisionState`
   snapshots in DuckDB.
3. Decide whether every Chainlink/CLOB event must be persisted before each live
   decision, or whether each live decision can persist an exact `DecisionState`
   snapshot first and rely on raw journals for replay.
4. Add first-class latency marks to the status schema instead of deriving all
   latency from event/observed timestamps.
5. Only after the above, start probability outputs.

## Fresh-Slate Data Wipe

No Spoon data was deleted.

The destructive target remains:

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

This still requires explicit confirmation, because it is destructive. The
confirmation phrase should be: `yes, wipe Spoon data`.

## Verification

Local verification completed:

```text
uv run ruff check .        -> pass
uv run mypy src tests      -> pass
uv run pytest -q           -> 239 passed, 1 upstream warning
cd rust && cargo test --workspace -> 54 passed
```
