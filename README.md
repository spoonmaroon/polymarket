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

## Live Read-Only Collection

The current live runner tracks BTC/ETH UP/DOWN for the current and next
5-minute and 15-minute windows. It writes durable raw data, normalized DuckDB
tables, and an atomic terminal-monitor status file with normalized-table and
source-freshness health.

```bash
uv run polymarket-engine collect --assets BTC,ETH --intervals 5m,15m --forever --windows-to-track 2 --snapshot-interval 1 --market-refresh-interval 30
```

In a second terminal:

```bash
uv run polymarket-engine monitor --refresh 1 --limit 8
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
