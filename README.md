# Polymarket Crypto Binary Strategy

Multi-venue crypto binary pricing, paper-trading, and research system.

The canonical project plan is [PLAN.md](docs/PLAN.md).

## What This Is

This project is for pricing short-dated BTC/ETH/SOL prediction-market binaries
using remaining-path probability, executable venue prices, volatility state,
support/resistance filters, and later BTC ETF options context.

The goal is not generic crypto direction prediction. The goal is to estimate
whether a specific 5-minute or 15-minute binary contract is mispriced after
fees, spread, slippage, data latency, and path-reversal risk.

## Current Status

- Private repo: `git@github.com:AnimeWeeb9000/polymarket.git`
- Canonical GitHub branch: `main`
- Python environment: `uv`
- API: FastAPI read-only runtime endpoints
- Live runtime: Rust SDK state-manager on THEPC
- Operator UI: read-only Rust cockpit TUI
- Browser UI: read-only Probability Runtime preview for latest probability rows
- Research scaffold: `probability_core` and replay tables
- Execution mode: paper/read-only by design

Start with [SETUP.md](SETUP.md). Normal development should merge back to
`main`; do not leave merged `codex/*` branches on GitHub.

THEPC deploys fetch `origin/main` over SSH from GitHub. The Windows/WSL user
must have a GitHub SSH key that can read
`git@github.com:AnimeWeeb9000/polymarket.git`. The deploy script refuses commits
that are not already present at `origin/main`.

## Part One Data Contract

The locked Part One data-source and database plan lives in
[PART_ONE_DATA_CONTRACT.md](docs/PART_ONE_DATA_CONTRACT.md).

## Retired Python Live Collection

The legacy Python collector is retired. `polymarket-engine collect`, the
legacy Docker entrypoint, and the legacy systemd unit now fail closed so the
old framework cannot be restarted by accident. The read-only monitor remains
available for inspecting existing local data.

In a second terminal:

```bash
uv run polymarket-engine monitor --refresh 1 --limit 8
```

## Rust State Manager

The Rust state-manager is the active read-only runtime. It uses the official
Polymarket Rust SDK to keep BTC/ETH 5m current and next contract
windows warm, subscribe to CLOB WebSocket top-of-book updates, collect
Chainlink RTDS BTC/USD and ETH/USD reference ticks, track WebSocket status, and
write an atomic health/status report plus append-only raw event journals.

The active deployment is intentionally 5m-only for now. 15m remains part of
the research plan, but it is not part of the current always-on Rust collector.

Run a finite smoke:

```bash
cd rust
cargo run -p polymarket-live-probe -- \
  --mode state-manager \
  --assets BTC,ETH \
  --interval 5m \
  --prewarm-windows 2 \
  --run-for-seconds 30 \
  --out ../reports/live_probe/state_manager.json
```

Verify:

```bash
python3 scripts/verify_state_manager_report.py reports/live_probe/state_manager.json
```

Normalize persisted Rust raw journals into DuckDB for replay/research:

```bash
uv run polymarket-engine normalize-rust-events \
  --raw-root data/raw \
  --duckdb-path data/db/polymarket.duckdb
```

Build current as-of `DecisionState` snapshots from normalized rows:

```bash
uv run polymarket-engine build-current-decision-states \
  --duckdb-path data/db/polymarket.duckdb \
  --status-path data/live/status.json
```

Write normalized DuckDB table health for operators after snapshot building:

```bash
uv run polymarket-engine write-normalized-health \
  --duckdb-path data/db/polymarket.duckdb \
  --out data/live/normalized_health.json
```

## Offline Backtest And Calibration

The backtest and ML calibration workflow is offline-only. It reads replay-safe
as-of rows from DuckDB, joins final outcomes only as labels, and writes research
artifacts under `data/research/`.

Example:

```bash
uv run polymarket-engine export-calibration-dataset \
  --duckdb-path data/db/polymarket.duckdb \
  --out data/research/calibration/asof_decision_states.jsonl \
  --limit 10000

uv run polymarket-engine calibration-report \
  --input data/research/calibration/asof_decision_states.jsonl \
  --out data/research/calibration/raw_mc_report.json \
  --probability-field p_finish_mc

uv run polymarket-engine run-backtest \
  --input data/research/calibration/asof_decision_states.jsonl \
  --out data/research/backtests/raw_mc.json \
  --probability-field p_finish_mc \
  --stake-usd 100 \
  --min-edge 0.02

uv run polymarket-engine train-calibrator \
  --input data/research/calibration/asof_decision_states.jsonl \
  --model-type logreg \
  --model-out data/research/models/logreg.json \
  --predictions-out data/research/calibration/logreg_predictions.jsonl

uv run polymarket-engine calibration-report \
  --input data/research/calibration/logreg_predictions.jsonl \
  --out data/research/calibration/logreg_report.json \
  --probability-field p_finish_final
```

Run XGBoost only after syncing research dependencies:

```bash
uv sync --group dev --group research
uv run polymarket-engine train-calibrator \
  --input data/research/calibration/asof_decision_states.jsonl \
  --model-type xgboost \
  --model-out data/research/models/xgboost.json \
  --predictions-out data/research/calibration/xgboost_predictions.jsonl
```

This workflow does not place trades. It is for replay, calibration, and
offline execution simulation.

## Read First

- [PLAN.md](docs/PLAN.md) - complete merged architecture, research, build,
  setup, UI, and risk plan.
- [Part Two Live Collectors](docs/PART_TWO_LIVE_COLLECTORS.md) - read-only
  collector command and live-source rules.
- [SETUP.md](SETUP.md) - short local setup commands.
- [External review proposition](docs/EXTERNAL_REVIEW_PROPOSITION.md) - concise
  explanation of the idea, data sources, computed values, decision tree, and
  flaws we want reviewed.
