# Polymarket Crypto Binary Strategy

Multi-venue crypto binary pricing, paper-trading, and research system.

The canonical project plan is [PLAN.md](PLAN.md).

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

## Read First

- [PLAN.md](PLAN.md) - complete merged architecture, research, build, setup, UI,
  and risk plan.
- [SETUP.md](SETUP.md) - short local setup commands.

## Source Notes

These are supporting notes preserved for traceability:

- [architecture inquiry](docs/architecture-inquiry.md)
- [architecture visualization](docs/architecture-visualization.md)
- [research worthiness memo](docs/research-worthiness-2026-05-28.md)
- [robust architecture plan](docs/robust-architecture-plan-2026-05-28.md)

If a source note conflicts with [PLAN.md](PLAN.md), use `PLAN.md`.
