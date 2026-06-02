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
- Python environment: `uv`
- Backend scaffold: FastAPI
- C++ scaffold: `probability_core`
- UI scaffold: React/Vite
- Execution mode: paper/read-only by design

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
Polymarket Rust SDK to keep BTC/ETH 5m current, next, and next-next contract
windows warm, subscribe to CLOB WebSocket top-of-book updates, collect
Chainlink RTDS BTC/USD and ETH/USD reference ticks, track WebSocket status, and
write an atomic health/status report.

The active deployment is intentionally 5m-only for now. 15m remains part of
the research plan, but it is not part of the current always-on Rust collector.

Run a finite smoke:

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

Verify:

```bash
python3 scripts/verify_state_manager_report.py reports/live_probe/state_manager.json
```

## Read First

- [PLAN.md](docs/PLAN.md) - complete merged architecture, research, build,
  setup, UI, and risk plan.
- [Part Two Live Collectors](docs/PART_TWO_LIVE_COLLECTORS.md) - read-only
  collector command and live-source rules.
- [SETUP.md](SETUP.md) - short local setup commands.
- [External review proposition](docs/EXTERNAL_REVIEW_PROPOSITION.md) - concise
  explanation of the idea, data sources, computed values, decision tree, and
  flaws we want reviewed.
