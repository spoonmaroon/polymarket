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

The old Python live collector is retired. `polymarket-engine collect`, the
legacy Docker entrypoint, and the legacy systemd unit now fail closed so the
old framework cannot be restarted by accident. The read-only monitor remains
available for inspecting existing local data.

In a second terminal:

```bash
uv run polymarket-engine monitor --refresh 1 --limit 8
```

## Rust Live Probe

The Rust live probe is the active read-only runtime test. It uses the official
Polymarket Rust SDK to discover current BTC/ETH 5m markets, fetch CLOB order
books, normalize them, pull Chainlink BTC/USD, pull Kraken XBT/USD as a proxy,
calculate source disagreement, and write a latency report.

Run:

```bash
cd rust
cargo run -p polymarket-live-probe -- \
  --assets BTC,ETH \
  --interval 5m \
  --windows 1 \
  --timeout-seconds 25 \
  --out ../reports/live_probe/latest.json
```

Verify:

```bash
uv run python scripts/verify_rust_probe_output.py \
  reports/live_probe/latest.json \
  --require-orderbooks \
  --require-btc-prices \
  --require-btc-disagreement \
  --max-btc-disagreement-bps 100
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
