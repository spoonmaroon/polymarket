# Fill-Holes System Report

Date: 2026-06-02

Scope: local implementation on branch `codex/rust-raw-normalizer`, Spoon
deployment, raw normalization, normalized health, current `DecisionState`
snapshots, and latency status marks.

## Verdict

Sections 1-2 are now the active fixed target for read-only live collection and
as-of replay after deployment verification. Sections 3-4 remain intentionally
blocked from probability implementation until replay tests prove sampled
`DecisionState` rows are reconstructable from raw journals.

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
   - `uv run polymarket-engine build-current-decision-states --duckdb-path ... --status-path ...`

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

6. Added first-class Rust state-manager latency marks:
   - `chainlink_observed_age_ms`
   - `chainlink_event_to_observed_ms`
   - `orderbook_observed_age_ms`
   - `orderbook_event_to_observed_ms`

7. Added the `DecisionState` durability bridge:
   - current Rust status produces side-level `ContractSpec` rows;
   - normalized Chainlink/CLOB rows build exact as-of `DecisionState` rows;
   - `features.asof_state_inputs` is the pre-probability live decision boundary.

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

Durability decision:

```text
Persist exact DecisionState snapshots before probability decisions.
Keep append-only Chainlink/CLOB raw journals as the replay/audit source.
Do not require every raw event to be normalized before a live decision,
but require replay tests proving raw journals can reconstruct sampled states.
```

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
| 1. Contract rules and settlement source | Fixed for current live 5m Rust state | Rust status can derive side-level live `ContractSpec` rows after fresh slate. |
| 2. Data and as-of state | Fixed for current live 5m Rust state after deploy/run | Raw Rust Chainlink/CLOB normalize into DuckDB, normalized health is written, and current `DecisionState` snapshots are persisted. |
| 3. Core probability outputs | Not started | Correctly blocked until normalized replay rows and `DecisionState` snapshots are current. |
| 4. Monte Carlo path generation | Not started | Method is documented; implementation should wait for replay-safe live state. |

## Remaining Holes

1. Add replay tests that rebuild sampled `DecisionState` rows from raw journals
   and compare them to persisted `features.asof_state_inputs`.
2. Let the collector run long enough after a fresh slate to accumulate enough
   Chainlink ticks for non-missing volatility on every current state.
3. Only after the above, start probability outputs.

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
