# Hot Decision Replay Proof

Date: 2026-06-02

Scope: prove that recent Rust hot `DecisionState` JSONL rows can be replayed
from normalized DuckDB Chainlink and CLOB rows at the same `asof_ts`.

## Verdict

The replay proof passes after fixing Python replay freshness ages to use
observed timestamps, matching the Rust hot path and collector health checks.

## What Failed First

The first synthetic proof failed because Python replay computed:

```text
source_age_ms = asof_ts - settlement_event_ts
book_age_ms = asof_ts - book_event_ts
```

Rust hot decisions compute those ages from observed timestamps:

```text
source_age_ms = asof_ts - settlement_observed_ts
book_age_ms = asof_ts - book_observed_ts
```

That mismatch caused replay rows to disagree with hot Rust state even when the
selected price and book rows were correct.

## Fix

Updated `src/polymarket_engine/features/state_builder.py` so replay freshness
uses observed timestamps for `source_age_ms`, `book_age_ms`, and `quote_age_ms`.
The separate event-to-observed lag fields remain unchanged.

## Local Synthetic Proof

Command:

```bash
uv run pytest -q tests/features/test_hot_decision_replay_verifier.py tests/features/test_state_builder.py
```

Result:

```text
12 passed
```

The synthetic replay test writes Rust-shaped raw Chainlink and CLOB JSONL,
normalizes those raw rows into DuckDB, and compares a hot decision snapshot
against replay at the same `asof_ts`.

## Verifier Command

The proof is now available as a reusable CLI command:

```bash
uv run polymarket-engine verify-hot-decision-replay \
  --raw-root /home/spoon/polymarket-data/raw \
  --duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb \
  --limit 40 \
  --scan-limit 1000 \
  --report-out /home/spoon/polymarket-data/live/hot_decision_replay_report.json
```

The command scans recent Rust hot decision JSONL rows, filters out rows whose
inferred Chainlink/order-book observed timestamps are newer than the normalized
DuckDB watermarks, replays the eligible rows from DuckDB, and writes a compact
JSON report with `rows_scanned`, `rows_checked`,
`rows_skipped_not_replay_ready`, `mismatch_count`, and detailed mismatches.

Local verification:

```bash
uv run pytest -q tests/features/test_hot_decision_replay_verifier.py tests/features/test_state_builder.py tests/test_cli.py
uv run ruff check .
uv run mypy src tests
uv run pytest -q
```

Result:

```text
27 passed
All checks passed!
Success: no issues found in 81 source files
267 passed, 1 warning
```

## Spoon Snapshot Proof

Method:

1. Read Spoon collector and normalizer status.
2. Copy `/home/spoon/polymarket-data/db/polymarket.duckdb` to a local temp dir.
3. Read recent hot decision rows from
   `/home/spoon/polymarket-data/raw/polymarket_decision_state/hot_state`.
4. Filter hot rows to rows older than the copied DuckDB price/orderbook
   watermarks.
5. Compare 40 recent eligible hot rows against local replay from copied DuckDB.

Result:

```json
{
  "rows_checked": 40,
  "ok": true,
  "mismatch_count": 0
}
```

Important note: copying only the main DuckDB file while active writes are in
flight can produce a stale local copy. The proof must either run directly on
Spoon or filter by the copied DB watermarks.

## Remaining Gate

This proves the current raw-to-normalized replay path for sampled hot decision
inputs. It does not start probability, Monte Carlo, XGBoost, paper trading, or
execution. Those remain blocked until this verifier is deployed or run routinely
on Spoon after normalizer cycles.
