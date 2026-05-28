# Polymarket Crypto Binary Strategy

This project is for designing and testing a remaining-path probability engine
for short-dated BTC/ETH/SOL-style up/down crypto binaries across prediction
venues.

## Core Idea

The goal is not to predict crypto spot prices in general. The goal is to price
the remaining path risk of short-dated crypto binary contracts better than the
market at specific moments.

Project documents:

- `SETUP.md` is the barebones local setup guide for GitHub, Python, C++, UI,
  and secrets.
- `docs/robust-architecture-plan-2026-05-28.md` is the detailed, editable
  architecture plan with component-by-component reasoning, data contracts,
  latency design, execution gates, build order, risk register, and success
  criteria.
- `docs/architecture-visualization.md` contains editable Mermaid diagrams for
  the system map, hot decision loop, compiled-core boundary, venue adapter
  normalization, research loop, and execution safety gates.
- `docs/research-worthiness-2026-05-28.md` reviews academic and working-paper
  evidence for the core ideas and lists what would falsify the project.
- `docs/architecture-inquiry.md` preserves the initial requirements and design
  questions so the reasoning trail stays visible.

This should be venue-agnostic. Polymarket is one venue, but the architecture
should also support Jupiter Prediction Markets and other DeFi or regulated
prediction venues when their APIs, legal status, and data quality make sense.

For each BTC/ETH/SOL 5-minute or 15-minute up/down market, estimate fair
probability from:

- current oracle or settlement-source price
- start or resolution threshold price
- seconds left until market resolution
- realized volatility over multiple rolling windows
- volatility trend, especially whether volatility is decreasing and staying low
- distance from the resolution line
- executable bid/ask and book depth for each supported venue
- taker fees, maker rebates, slippage, and queue risk

## Venue Scope

Initial candidate venues:

- Polymarket-style CLOB markets
- Jupiter Prediction Markets on Solana
- other prediction-market APIs only after their market structure, fees, access
  rules, settlement source, and execution mechanics are understood

The architecture should normalize venue-specific fields into one internal model:

- `Venue`
- `Event`
- `Market`
- `Outcome`
- `OrderBook`
- `FeeSchedule`
- `SettlementRule`
- `Position`
- `Fill`

Venue adapters should hide API differences. The probability engine should not
care whether a market came from Polymarket, Jupiter, or another source.

## Market Data Sources

The project should prefer official, stable, low-cost data sources before paid
feeds. Data quality matters more than grabbing every free endpoint.

Candidate source classes:

- prediction venue APIs for market discovery, pricing, order books, and
  settlement metadata
- Jupiter Prediction API for Jupiter-hosted or aggregated prediction markets
- public crypto exchange WebSockets for spot ticks, trades, candles, and book
  state
- settlement/oracle sources that match the actual market resolution rule
- free or low-cost historical candles for volatility and support/resistance
- optional ETF options data for cross-market context

The source-selection rule:

1. settlement-source price beats exchange spot when pricing a binary contract
2. executable venue bid/ask beats displayed probability
3. official APIs beat scraping
4. delayed data is acceptable for research labels, but not for live entry logic

## Cross-Market Feature Lane

BTC ETF options data may contain useful context for BTC binary predictions.
This should be treated as a separate feature lane, not part of the core
remaining-path model at first.

Candidate BTC-linked underlyings:

- spot BTC ETFs such as `IBIT`, `FBTC`, `ARKB`, `BITB`, and similar liquid ETFs
- futures-linked BTC products only if liquidity and option structure justify it

Candidate features:

- ETF option implied volatility level and change
- call/put volume imbalance
- premium bought/sold if available from a legitimate source
- open-interest changes by expiry and strike
- gamma or delta exposure proxies around current BTC-equivalent levels
- unusual option activity flags
- skew changes and volatility risk premium proxies

Use case:

- Do not let ETF options flow directly trigger trades in the initial approved
  signal policy.
- Log it beside each crypto binary window.
- Test whether it improves calibration of `p_finish`, `p_no_touch`, or blocked
  signal classification.
- Promote it only if out-of-sample reports show it adds predictive value.

Constraint: real-time U.S. options order flow is usually licensed data. If we do
not have a legitimate source, this lane starts with delayed or end-of-day data
for research only.

## Borrowed GEX Pattern For ETF Options

Enoch already has a working architecture pattern in the GEX project for ETF
option-chain and orderflow collection. The Polymarket project should borrow the
pattern, not couple directly to the GEX runtime.

Reusable pieces from the GEX design:

- watchlist-driven option universe selection
- per-symbol collection cadence
- top-N option contract picker by open interest across nearest expirations
- separate collector daemon
- normalized event schema
- hot event table for recent orderflow
- durable Parquet-style research storage when needed
- API/report layer reading normalized data instead of raw broker payloads
- persistence gate so only selected symbols write high-volume orderflow data
- heartbeat/health rows for collector observability

Important GEX-specific lesson:

- The live Schwab orderflow collector may derive synthetic print events from
  level-one option/equity deltas (`LAST_PRICE`, `LAST_SIZE`, exchange), rather
  than receiving perfect raw time-and-sales prints. That is good enough for
  context and correlation testing, but it should not be treated as full OPRA
  tape truth.

For this project, the ETF options lane should become an `OptionsContextAdapter`
with a clean output contract:

- `underlying_symbol`
- `linked_crypto_asset`
- `timestamp`
- `chain_snapshot_features`
- `flow_features`
- `iv_features`
- `skew_features`
- `gex_like_features`
- `data_source`
- `latency_class`
- `quality_flags`

The binary probability engine can consume these features only after calibration
reports prove they improve prediction quality.

## Strategy Shape

The first strategy should focus on contracts where price has already moved to
one side and the remaining reversal risk is shrinking.

Target setup:

- price is safely on the desired side of the resolution line
- realized volatility is falling
- remaining time is short enough for the distribution to collapse
- the order book still prices too much reversal risk
- spread and depth allow execution without consuming the edge
- the underlying is not sitting near a meaningful 5-minute or 15-minute support
  or resistance level

The main probability outputs:

- `p_finish`: probability the final settlement price ends on the desired side
- `p_no_touch`: probability the price does not cross back through the danger
  line before expiry
- `z_path`: distance from line divided by expected remaining move
- `edge`: fair probability minus executable market price after costs

## First Modeling Decision

The system should model both 5-minute and 15-minute binaries, but they should
not share one undifferentiated model.

Each horizon needs separate:

- volatility windows
- probability calibration reports
- entry thresholds
- support/resistance exclusion settings
- latency and execution assumptions
- paper-trading performance summaries

The 5-minute model will be more latency-sensitive and more exposed to final
minute noise. The 15-minute model should have more stable volatility estimates
and is the better initial place to validate the probability engine.

## Support / Resistance Exclusion

The engine should avoid trades when the underlying spot or oracle price is near
meaningful 5-minute or 15-minute chart support/resistance.

Reason: near support/resistance, the simple remaining-path distribution is less
trustworthy. Price can stall, reject, wick through, or mean-revert for
structure-driven reasons even when volatility appears to be decreasing.

The architecture should include a `structure_filter` that can mark a market as
blocked when:

- current price is within a configurable distance of recent support/resistance
- the resolution line is near support/resistance
- the expected trade direction would require breaking a level that has not yet
  been accepted by price
- the market is close to large round-number levels that repeatedly act as
  magnets or rejection points

Signals near blocked levels should still be logged for research, but not marked
tradable.

## Target Architecture Direction

This should be designed as the real architecture from the start. No throwaway
prototype path. Live execution can stay disabled by policy, but the code
boundaries should already match the eventual production system.

Core components:

- venue adapters for market discovery, pricing, order books, and order intent
  routing
- settlement-source price tracker, ideally matching the actual oracle source
- crypto market-data adapters for ticks, trades, candles, and derived
  support/resistance
- compiled `probability_core` for rolling volatility, path probability,
  structure filters, fair value, and signal scoring
- execution simulator that consumes the same order-intent objects live trading
  would use
- venue-normalized book scanner
- ETF options context adapter borrowed from the GEX architecture pattern
- append-only signal, non-signal, blocked-signal, and fill ledger
- calibration and model-report engine showing whether predicted probabilities
  match outcomes
- compliance and kill-switch gate that can force any venue into read-only or
  paper-only mode

## Performance / Language Direction

Speed matters for this system, but the architecture should separate latency
domains instead of rewriting everything in C++.

Most first-order latency will come from:

- venue API/WebSocket latency
- oracle/spot feed latency
- order book update latency
- wallet/signing/transaction latency for DeFi venues
- cancel/reprice round trips
- queue position and fill uncertainty

C++ does not fix those. It only helps the parts we control locally.

Recommended language split:

- Python for research, paper-trading, reports, adapters, and orchestration
- Polars/DuckDB/NumPy for vectorized research and calibration
- C++ or Rust for the hot calculation and execution-decision path from the
  start
- a stable FFI boundary so the probability engine can be called from Python
  without rewriting the whole project

The compiled core should be built as a first-class module, not postponed:

- rolling volatility updates
- `p_finish`
- `p_no_touch`
- barrier/no-touch approximation
- support/resistance distance checks
- fair-value and edge calculations
- per-market signal scoring
- risk checks
- cancel/reprice decisions
- order-intent generation

The live venue adapter can remain disabled until calibration and legal gates
pass, but the simulator should use the same compiled decision output that live
execution would use. That avoids rebuilding the system when moving from paper to
supervised live trading.

Design rule: optimize the hot loop, not the whole app.

## Current Design Questions

These are the design knobs Enoch can still change after the first architecture
draft:

1. Should the system target 5-minute contracts, 15-minute contracts, or
   both? **Decision: both, with separate models and reports.**
2. Which venues should be supported first: Polymarket only, Jupiter only, or a
   venue-adapter skeleton with both as read-only data sources?
3. Should the first strategy take mispriced offers, post maker quotes, or only
   paper-trade both paths?
4. What is the intended first capital range after paper trading proves useful?
5. Which source should be treated as canonical for crypto settlement pricing?
6. Should this be built as a CLI daemon first, a notebook/research tool first,
   or a small local dashboard first?
7. What is the acceptable latency target for the first build?
8. Should the first design avoid live order execution completely?
9. How should support/resistance be calculated in the initial signal policy?
10. Should ETF options data start as delayed research-only context, or should we
    prioritize finding a live licensed feed? **Update: use the existing GEX
    architecture pattern as the first source path where possible.**
11. Should the hot path be implemented in C++ or Rust? **Decision shape:
    compiled `probability_core` from the beginning, called from Python through
    a stable FFI boundary.**
