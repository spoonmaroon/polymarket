# Robust Architecture Plan

Date: 2026-05-28

Project: multi-venue short-dated crypto binary pricing and execution research
system.

Working name: `polymarket`, but the system must not be Polymarket-only.

## Executive Summary

This system prices short-dated BTC/ETH/SOL binary prediction contracts by
estimating the probability that the underlying finishes on the desired side and
the probability that the price does not cross back against the trade before
resolution.

The core thesis:

> The edge is not generic crypto direction prediction. The edge is fast,
> disciplined pricing of remaining path risk when the venue's executable price
> still implies too much reversal risk.

The architecture should be production-shaped from the start:

- multi-venue adapters;
- settlement-source price tracking;
- real-time volatility and structure filters;
- compiled C++ `probability_core`;
- identical paper/live decision output;
- append-only research ledger;
- model calibration reports;
- execution disabled until explicit gates pass.

This is not a throwaway prototype. Paper mode is a policy state, not a different
architecture.

## Design Goals

### Goal 1: Price The Contract, Not The Coin

The system does not ask, "Will BTC go up?"

It asks:

- What is the contract's threshold?
- What source resolves it?
- How much time remains?
- How much can the underlying normally move in that time?
- How likely is a terminal win?
- How likely is a path reversal before resolution?
- Is the executable market price wrong after spread, fee, latency, and slippage?

Why this exists:

Prediction-market crypto binaries are digital options. A general BTC forecast is
too broad. The tradable object is narrow: one outcome, one settlement rule, one
expiry window, one venue order book.

How it works:

The system normalizes every supported venue market into one internal contract
shape, then sends that contract plus real-time price/vol/order-book state into
the compiled probability core.

### Goal 2: Keep Venue Differences Out Of The Math

Polymarket, Jupiter, Kalshi-through-Jupiter, and future DeFi venues will expose
different APIs, fee models, fill logic, market metadata, and settlement rules.

Why this exists:

If the probability engine reads raw Polymarket fields directly, every new venue
forces a rewrite. The math should operate on normalized concepts:

- market;
- outcome;
- threshold;
- expiry;
- bid/ask/depth;
- fee schedule;
- latency state;
- settlement rule.

How it works:

Each venue adapter converts raw API data into internal structs. The compiled
core receives only those structs. The core does not know whether the market came
from Polymarket, Jupiter, or another source.

### Goal 3: Build For Fast Decisions Without Pretending C++ Solves Network Lag

Speed matters, but the system is not trying to beat colocated market makers in
the final 100 milliseconds.

Why this exists:

The local calculation loop can be fast, but most latency comes from:

- WebSocket delivery;
- settlement/oracle feed delay;
- order book update delay;
- venue matching;
- Solana or Polygon signing/settlement;
- cancel/reprice round trips;
- queue position.

How it works:

Use C++ for the local hot loop:

- rolling volatility updates;
- path probability;
- no-touch probability;
- structure filter checks;
- fair-value and edge scoring;
- risk checks;
- order-intent decisions.

Use Python for:

- adapters;
- orchestration;
- storage;
- reports;
- dashboards;
- calibration jobs.

### Goal 4: Make Paper Trading Structurally Identical To Live Trading

Paper trading should not be a separate toy path.

Why this exists:

If paper mode uses different decision objects than live mode, the system will
lie. The whole point is to know whether the live architecture would have made a
good decision.

How it works:

The compiled core always emits an `OrderIntent`.

Then a policy router sends it to either:

- `PaperExecutionAdapter`; or
- `LiveExecutionAdapter`.

Both adapters consume the same intent schema. Live mode remains disabled until
legal, calibration, risk, and kill-switch gates pass.

### Goal 5: Treat ETF Options Flow As A Context Lane

BTC ETF options orderflow may help with BTC binary predictions, but it must not
be allowed to contaminate the core model before it proves value.

Why this exists:

Academic evidence supports options-flow information in general, and bitcoin
options contain volatility and some directional information. But BTC ETF options
are newer, and the direct link to 5m/15m BTC prediction contracts must be
tested.

How it works:

Borrow the GEX collection pattern:

- watchlist controls high-volume data;
- chain snapshots define the universe;
- top-N OI contract picker selects contracts;
- collector daemon normalizes events;
- quality flags label derived vs direct orderflow;
- reports test whether the lane improves calibration.

The core binary model can ignore these features until reports show they add
out-of-sample value.

## System Boundary

### In Scope

- BTC, ETH, SOL 5-minute and 15-minute binary markets.
- Polymarket-style CLOB market data.
- Jupiter Prediction API market data and order-intent pathway.
- Other venues only through adapters.
- Settlement-source price tracking.
- Crypto spot/candle/order-book data from official APIs.
- Multi-horizon realized volatility.
- Support/resistance structure filter.
- ETF options context for BTC-linked ETFs.
- Paper trading.
- Calibration and model reports.
- Compiled decision core.
- Read-only / paper-only / supervised-live policy gates.

### Out Of Scope Until Explicitly Added

- Autonomous live trading.
- Final-second latency wars.
- Undocumented scraping as a primary data source.
- Using ETF options flow as a direct trigger before proof.
- Wallet/private-key handling in early research mode.
- Anything that bypasses venue geographic, legal, or account restrictions.

## Architecture Overview

The system has six major planes:

1. **Source Plane**: venues, settlement feeds, crypto exchanges, ETF options.
2. **Adapter Plane**: converts raw external data into internal event schemas.
3. **State Plane**: live ring buffers, normalized market state, cold storage.
4. **Decision Plane**: compiled probability and risk core.
5. **Execution Plane**: paper/live adapters behind policy gates.
6. **Research Plane**: calibration, reports, ablations, dashboards.

## Core Data Flow

### Step 1: Discover Candidate Markets

The venue adapters search for live crypto binary markets:

- asset is BTC, ETH, or SOL;
- duration is 5m or 15m;
- market is open/tradable;
- resolution rule is machine-readable;
- order book is available;
- fee schedule is known or conservatively estimated.

Why this exists:

The engine cannot safely price a market if it does not understand the market's
contract terms.

How it works:

The `MarketRegistry` stores normalized market records:

```text
MarketRecord
- venue
- market_id
- asset
- horizon_seconds
- side_set
- threshold_price
- settlement_source
- open_time
- close_time
- resolve_time
- fee_schedule_id
- market_status
- legal_mode
- quality_flags
```

Change knobs:

- supported assets;
- supported durations;
- minimum volume;
- maximum spread;
- allowed venues;
- legal mode per venue.

### Step 2: Track Settlement-Source Price

Every contract must map to a canonical price source.

Examples:

- Chainlink stream;
- venue-provided settlement oracle;
- Coinbase spot;
- Binance spot;
- Jupiter/venue-specific oracle;
- custom composite only if the market rule allows it.

Why this exists:

For binary resolution, "close enough spot price" is not enough. The contract
settles from a specific rule. A profitable-looking signal can be fake if the
engine uses the wrong price source.

How it works:

`SettlementPriceAdapter` emits:

```text
SettlementTick
- asset
- source
- observed_at
- exchange_ts
- price
- sequence
- latency_ms
- quality_flags
```

The engine can also ingest supporting spot feeds, but only the settlement-source
tick is allowed to anchor final probability.

Change knobs:

- primary settlement source;
- fallback source;
- max allowed source staleness;
- max allowed divergence between settlement source and exchange spot.

### Step 3: Track Venue Order Books

For every candidate market, capture executable price, not just displayed
probability.

Why this exists:

The model makes money only if fair value beats executable bid/ask after costs.
Midpoint edge is not real edge.

How it works:

`OrderBookAdapter` emits:

```text
OrderBookSnapshot
- venue
- market_id
- outcome
- observed_at
- venue_ts
- best_bid
- best_ask
- bid_size
- ask_size
- depth_levels
- spread
- source_latency_ms
- quality_flags
```

Change knobs:

- minimum depth;
- maximum spread;
- whether to use best ask only or depth-weighted executable price;
- stale book timeout;
- per-venue fee/rebate model.

### Step 4: Maintain Rolling Feature State

The system maintains real-time features for each asset and market.

Core features:

- distance from threshold;
- seconds left;
- realized volatility at multiple horizons;
- volatility trend;
- support/resistance proximity;
- order book spread/depth;
- settlement source staleness;
- venue latency;
- ETF options context where available.

Why this exists:

The compiled core needs a compact, current, typed feature state. It should not
query databases or parse raw JSON on the hot path.

How it works:

Python adapters update in-memory ring buffers. Every market tick produces a
`DecisionInput` struct for the compiled core.

Change knobs:

- feature windows;
- buffer sizes;
- update cadence;
- support/resistance calculation mode;
- ETF context enabled/disabled.

### Step 5: Run Compiled Probability Core

The C++ `probability_core` computes:

- `p_finish`;
- `p_no_touch`;
- `z_path`;
- fair value;
- edge after costs;
- blocked reason;
- signal score;
- risk decision;
- order intent.

Why this exists:

This is the local hot loop. It must be deterministic, fast, testable, and
shared by paper and live execution.

How it works:

The core receives typed structs. It returns a typed decision. It does not
perform network IO. It does not read secrets. It does not write storage.

Change knobs:

- probability model formula;
- realized-vol estimator;
- no-touch approximation;
- edge buffer;
- risk limits;
- support/resistance block thresholds;
- maker/taker mode policy.

### Step 6: Route To Paper Or Live Execution

The `ExecutionPolicyRouter` decides where an order intent goes.

Modes:

- `read_only`: log decisions, do not simulate fills;
- `paper`: simulate fills using observed executable prices;
- `supervised_live`: allow manually approved live orders;
- `live`: not enabled until much later and only with explicit user approval.

Why this exists:

Safety and legal access cannot be an afterthought. The architecture should make
it impossible to accidentally trade from research mode.

How it works:

Every intent must pass:

- legal venue gate;
- account mode gate;
- kill-switch gate;
- max-notional gate;
- max-loss gate;
- stale-data gate;
- calibration gate;
- user approval gate for supervised live mode.

Change knobs:

- max notional per market;
- max daily loss;
- max open exposure;
- allowed venue modes;
- whether live trading is compiled in at all.

### Step 7: Log Everything

Every observed state and decision becomes an append-only event.

Why this exists:

Calibration needs truth. If a signal is skipped, blocked, or rejected, it must
still be logged. Otherwise the research reports will be biased toward acted-on
cases.

How it works:

The event ledger stores:

- market observations;
- price ticks;
- order book snapshots;
- feature frames;
- decisions;
- blocked decisions;
- order intents;
- paper fills;
- live fills if ever enabled;
- outcomes.

Change knobs:

- raw event retention;
- snapshot cadence;
- compression;
- hot-store retention;
- report dataset filters.

## Component Design

### 1. Venue Adapter Layer

Responsibility:

Convert venue-specific data into normalized internal schemas.

Why it exists:

Venue APIs are not stable, and they disagree on naming, fees, settlement,
position objects, order flow, and account model.

How it works:

Each adapter implements:

```text
VenueAdapter
- discover_markets(filters) -> list[MarketRecord]
- get_market(market_id) -> MarketRecord
- stream_order_book(market_id) -> OrderBookSnapshot stream
- get_fee_schedule(market_id) -> FeeSchedule
- submit_order_intent(intent) -> VenueOrderResult
- cancel_order(order_id) -> CancelResult
- get_fills(market_id) -> FillReport list
- get_positions() -> Position list
```

Initial adapters:

- `PolymarketAdapter`
- `JupiterPredictionAdapter`
- `NullVenueAdapter` for tests

Design notes:

- Market data methods can run in read-only mode.
- Order submission methods must hard-fail unless mode allows them.
- Jupiter may aggregate Polymarket/Kalshi liquidity, so it needs a field for
  `provider`.
- Polymarket and Jupiter order lifecycles differ; normalize only what the
  internal system actually needs.

### 2. Market Registry

Responsibility:

Maintain the active universe of candidate markets.

Why it exists:

The decision engine should not scan external APIs every tick. Discovery and
decisioning are separate concerns.

How it works:

The registry refreshes on a configurable cadence, stores current market
metadata, and emits changes:

- market added;
- market updated;
- market halted;
- market closed;
- market resolved.

Important fields:

- market id;
- venue;
- provider;
- asset;
- threshold;
- horizon;
- close/resolve time;
- fee schedule;
- settlement rule;
- legal mode;
- market quality flags.

Failure modes:

- venue returns malformed market metadata;
- market lacks machine-readable settlement rule;
- market is open but order book is disabled;
- venue reports stale close time.

Handling:

- mark market `not_tradable`;
- keep it in logs;
- exclude from signal generation.

### 3. Settlement Price Layer

Responsibility:

Track the price that matters for resolution.

Why it exists:

The biggest model bug would be pricing against Binance while the market settles
against Chainlink or another source.

How it works:

The layer maintains:

- primary settlement-source feed;
- supporting exchange feeds;
- divergence checks;
- stale checks;
- latency estimates.

If settlement-source price is stale, the engine can still log decisions but must
block tradable order intents.

### 4. Crypto Market Data Layer

Responsibility:

Provide spot ticks, trades, candles, VWAP, session high/low, and
support/resistance inputs.

Why it exists:

The probability model needs current price and volatility. The structure filter
needs candles and levels. Venue settlement feeds may not provide enough history.

How it works:

Adapters ingest official public feeds such as Coinbase/Binance-style APIs and
normalize them into:

```text
PriceTick
Candle
TradePrint
BookDepth
```

This layer is supporting data unless the settlement rule explicitly says it is
the settlement source.

### 5. Volatility Engine

Responsibility:

Maintain realized-volatility state over multiple windows.

Why it exists:

`distance / expected_remaining_move` is the backbone of the strategy. A single
vol estimate is too fragile.

How it works:

For each asset and settlement source:

- keep rolling log returns;
- compute short-window and longer-window realized volatility;
- compute range expansion/contraction;
- flag jumps;
- expose volatility slope/trend.

Suggested initial windows:

- 10 seconds;
- 30 seconds;
- 120 seconds;
- 300 seconds;
- one full contract horizon where available.

The exact windows are configuration, not hard-coded dogma.

Outputs:

```text
VolState
- sigma_10s
- sigma_30s
- sigma_120s
- sigma_300s
- sigma_contract
- vol_slope
- jump_flag
- range_compression_flag
- quality_flags
```

### 6. Structure Filter

Responsibility:

Block signals near levels where simple path probability is unreliable.

Why it exists:

Support/resistance can cause stalls, rejection wicks, failed breaks, and
liquidity magnets. Near those levels, a smooth no-touch model can look safer
than reality.

How it works:

The filter calculates levels from 5m and 15m charts:

- recent swing highs/lows;
- session high/low;
- VWAP and VWAP bands;
- repeated rejection levels;
- round-number levels;
- optional order-book liquidity walls if available.

It returns:

```text
StructureState
- nearest_support
- nearest_resistance
- distance_to_support_bps
- distance_to_resistance_bps
- threshold_near_level
- direction_requires_break
- accepted_breakout
- blocked
- blocked_reason
```

Trade behavior:

- blocked signals are logged;
- blocked signals do not become tradable order intents;
- reports compare blocked-vs-unblocked outcomes.

### 7. ETF Options Context Adapter

Responsibility:

Attach BTC ETF options context to BTC binary windows.

Why it exists:

Options flow may reveal informed demand, volatility demand, skew shifts, and
dealer positioning around BTC exposure.

How it works:

Borrow from GEX:

- watchlist: `IBIT`, `FBTC`, `ARKB`, `BITB`, then expand only if liquid;
- chain collector stores snapshots;
- contract picker selects top-N by open interest across nearest expirations;
- orderflow collector normalizes prints or derived print-like events;
- feature builder maps ETF features to BTC windows.

Outputs:

```text
OptionsContextFrame
- linked_crypto_asset
- etf_symbol
- observed_at
- atm_iv
- iv_change
- skew
- call_put_volume_ratio
- option_to_underlying_volume_ratio
- top_oi_strikes
- gex_like_proxy
- flow_imbalance
- source_latency_class
- quality_flags
```

Policy:

- context-only until reports prove value;
- never a direct trigger by default;
- all data quality limitations must be visible.

### 8. Compiled Probability Core

Responsibility:

Own the hot decision calculation.

Default implementation target:

- C++20 shared library;
- Python binding through `pybind11` or equivalent;
- deterministic tests for all math branches.

Why C++ here:

The user wants speed designed in from the start. This is the part where C++
actually helps: local calculation, risk checks, and order-intent generation.

Why not full C++:

Venue adapters, reports, storage, and dashboards are IO-heavy and change often.
Python is faster to develop and easier to inspect there.

Core input:

```text
DecisionInput
- market_id
- venue
- asset
- side
- horizon_seconds
- current_price
- threshold_price
- seconds_left
- vol_state
- structure_state
- order_book_state
- fee_schedule
- options_context_summary
- latency_state
- risk_limits
```

Core output:

```text
DecisionOutput
- market_id
- observed_at
- p_finish
- p_no_touch
- z_path
- fair_yes
- fair_no
- edge_after_costs
- signal_score
- blocked
- blocked_reason
- risk_decision
- order_intent
- cancel_or_reprice_action
- diagnostics
```

No network IO. No file IO. No secrets. No venue API calls.

### 9. Execution Policy Router

Responsibility:

Decide whether a decision can become an action.

Why it exists:

The compiled core should be allowed to say "this is attractive." A separate
policy layer should say "you are allowed to act."

How it works:

Policy gates:

- venue legal mode;
- account mode;
- read-only flag;
- kill switch;
- market quality;
- stale price;
- stale order book;
- max spread;
- max notional;
- max open exposure;
- max daily loss;
- calibration minimum;
- user approval if supervised.

Outputs:

- `ROUTE_READ_ONLY`
- `ROUTE_PAPER`
- `ROUTE_SUPERVISED_LIVE`
- `ROUTE_REJECTED`

### 10. Paper Execution Adapter

Responsibility:

Simulate fills from the same `OrderIntent` live trading would use.

Why it exists:

The paper ledger must answer, "Would this have worked at the executable price?"
Not "did midpoint move in our favor?"

How it works:

Paper fills use:

- observed best bid/ask;
- available depth;
- configured queue model;
- stale-book penalty;
- fee model;
- cancel/reprice events.

The paper adapter records:

- intended price;
- executable price;
- simulated fill;
- partial fill;
- missed fill;
- max adverse excursion;
- final outcome.

### 11. Live Execution Adapter

Responsibility:

Submit and cancel orders only when explicitly enabled.

Why it exists:

The architecture should support live execution without a rewrite, but live
access must be impossible by accident.

How it works:

Each venue adapter has a live execution implementation, but config defaults to
disabled:

```text
execution_mode = read_only
allow_live_orders = false
max_order_notional = 0
```

To enable supervised live mode, the operator must set multiple config gates and
confirm in runtime state. This is annoying on purpose.

### 12. Storage And Research Layer

Responsibility:

Persist enough truth to prove or disprove the edge.

Why it exists:

If the only thing stored is acted-on trades, reports will be biased. The system
must log non-signals and blocked signals too.

Recommended storage:

- **Raw event log**: append-only compressed JSONL or Parquet by date/source.
- **Normalized cold store**: partitioned Parquet for market observations,
  feature frames, decisions, fills, and outcomes.
- **Hot state**: in-memory ring buffers for live decisions.
- **Query layer**: DuckDB over Parquet for reports.
- **Optional hot time-series store**: QuestDB if dashboard latency or event
  volume demands it, borrowing the GEX pattern.

Why not start with only a database:

Raw event logs are easier to audit and replay. DuckDB over Parquet is simple for
research. A hot database can be added where it actually improves live
observability.

### 13. Report Engine

Responsibility:

Tell whether the system has edge.

Why it exists:

The whole project lives or dies on calibration and executable edge.

Required reports:

- `p_finish` calibration curve;
- `p_no_touch` calibration curve;
- Brier score and log loss versus venue executable price;
- edge-after-costs distribution;
- fill simulation quality;
- false positives near support/resistance;
- latency distribution;
- venue comparison;
- 5m vs 15m model comparison;
- asset comparison;
- ablation: core vs core+structure vs core+ETF context.

Acceptance rule:

No live mode until reports show persistent edge after spread, fees, slippage,
staleness, and realistic fill assumptions.

## Data Schemas

### MarketObservation

```text
market_id
venue
provider
asset
horizon_seconds
side
threshold_price
current_settlement_price
seconds_left
best_bid
best_ask
bid_size
ask_size
spread
observed_at
source_latency_ms
quality_flags
```

### FeatureFrame

```text
market_id
observed_at
distance_from_threshold
expected_remaining_move
z_path
vol_state
structure_state
options_context_summary
latency_state
```

### DecisionRecord

```text
market_id
observed_at
p_finish
p_no_touch
fair_value
edge_after_costs
blocked
blocked_reason
signal_score
risk_decision
order_intent_id
diagnostics
```

### OrderIntent

```text
intent_id
market_id
venue
side
is_buy
order_type
limit_price
max_notional
max_contracts
expires_at
reason_code
risk_snapshot
```

### PaperFill

```text
intent_id
market_id
observed_at
fill_status
simulated_fill_price
simulated_contracts
fee_estimate
slippage_estimate
miss_reason
```

### OutcomeRecord

```text
market_id
resolved_at
settlement_price
threshold_price
winning_side
max_adverse_excursion
max_favorable_excursion
path_crossed_danger_line
```

## Probability Model

### Core Quantities

For an UP contract:

```text
distance = ln(current_price / threshold_price)
expected_remaining_move = sigma_remaining * sqrt(seconds_left)
z_path = distance / expected_remaining_move
```

For a DOWN contract, sign the distance so positive means "currently on the
winning side."

### Outputs

`p_finish`:

Probability the final settlement price ends on the desired side.

`p_no_touch`:

Probability the path does not cross back through the danger line before expiry.

Why both:

`p_finish` can be high while reversal risk is still unacceptable. `p_no_touch`
is closer to the user's actual intuition: "when is this unlikely to go against
me?"

### Model Evolution Without Rewrites

The compiled core should support multiple model families behind one interface:

- Brownian/no-drift approximation;
- drift-adjusted approximation;
- jump-adjusted approximation;
- empirical calibration correction;
- venue-specific probability distortion correction.

The decision API stays stable while internals improve.

## Latency Design

This system should be fast, but honest about where latency lives.

### Local Hot Path Budget

Target:

- normalize incoming tick: under 1 ms;
- update vol/structure state: under 1 ms per asset;
- run `probability_core`: under 100 microseconds per market batch target after
  optimization;
- route policy: under 1 ms;
- write decision to async ledger queue: non-blocking.

These are engineering targets, not promises before measurement.

### Network/External Latency Budget

Track:

- venue book delay;
- settlement-source tick delay;
- exchange spot delay;
- order submission round trip;
- cancel round trip;
- fill confirmation delay.

If these are unstable, the system must widen edge buffers or block trading.

## Configuration Design

Use explicit config files, not hidden constants.

Suggested files:

```text
config/venues.yaml
config/assets.yaml
config/risk.yaml
config/models.yaml
config/storage.yaml
config/options_context.yaml
```

Example risk config:

```yaml
execution_mode: paper
allow_live_orders: false
max_order_notional_usd: 0
max_daily_loss_usd: 0
max_open_markets: 0
max_book_staleness_ms: 500
max_settlement_staleness_ms: 500
min_edge_after_costs: 0.03
```

Why this exists:

Dangerous behavior should require deliberate config changes. Defaults should be
boring and safe.

## Process Layout

Recommended runtime processes:

```text
market_discovery_daemon
settlement_price_daemon
venue_book_daemon
crypto_market_data_daemon
options_context_daemon
decision_engine
paper_execution_engine
report_worker
dashboard_api
```

Why split processes:

- A venue WebSocket crash should not kill reports.
- A report query should not block the hot decision loop.
- Options context can fail without stopping core binary pricing.
- Later deployment can scale pieces independently.

How they communicate:

- internal async queues inside one process for early local runs;
- append-only event log for replay;
- optional message bus only if process boundaries need it;
- never direct raw API calls from the compiled core.

## Build Order

This is not "versions." This is construction order for the final architecture.

### Construction Slice 1: Contracts And Schemas

Build the normalized data contracts first.

Why:

Everything else depends on stable interfaces.

Output:

- Python dataclasses or Pydantic models;
- C++ structs matching the FFI boundary;
- schema tests;
- sample fixtures for Polymarket, Jupiter, and null test venue.

### Construction Slice 2: Read-Only Market And Price Ingestion

Build market discovery, settlement price tracking, and book snapshots in
read-only mode.

Why:

No model is useful until the system can observe the real markets correctly.

Output:

- active market registry;
- settlement ticks;
- order book snapshots;
- raw and normalized logs.

### Construction Slice 3: Compiled Probability Core

Build C++ `probability_core` early.

Why:

This is the architecture the system is designed around. Paper mode should use
the real core from the start.

Output:

- C++ library;
- Python binding;
- deterministic math tests;
- benchmark harness.

### Construction Slice 4: Structure And Volatility Features

Build multi-horizon realized volatility and support/resistance blocking.

Why:

These are core to the user's actual strategy: wait for volatility contraction
and avoid dangerous levels.

Output:

- rolling volatility state;
- structure state;
- blocked-signal logging.

### Construction Slice 5: Paper Execution And Ledger

Build paper fills from real executable bid/ask.

Why:

This determines whether there is edge after costs.

Output:

- order intents;
- simulated fills;
- missed fills;
- outcome linking.

### Construction Slice 6: Reports

Build calibration and ablation reports.

Why:

This is the proof layer. Without it, the system is just a fast opinion machine.

Output:

- calibration reports;
- edge reports;
- latency reports;
- support/resistance ablations;
- ETF context ablations.

### Construction Slice 7: ETF Options Context

Build the GEX-style BTC ETF options context adapter.

Why:

It may improve BTC-specific predictions, but it should be measured separately.

Output:

- chain snapshots;
- selected contracts;
- options context frames;
- reports proving whether it helps.

### Construction Slice 8: Supervised Live Shell

Build the live shell only after the policy gates and paper ledger exist.

Why:

The shell should consume the same order intents as paper mode, but remain
disabled unless explicitly approved.

Output:

- live adapter interfaces;
- kill switch;
- dry-run submit/cancel tests;
- supervised approval workflow.

## What You Can Change Later

This architecture is intentionally editable. The parts most likely to change:

- C++ vs Rust for the compiled core;
- exact realized-volatility windows;
- support/resistance method;
- venue priority;
- whether Jupiter is first-class or only aggregation;
- whether QuestDB is used as hot store;
- ETF options symbols;
- calibration thresholds;
- maker vs taker policy;
- minimum edge buffer;
- paper fill model.

The parts that should not change casually:

- venue adapters must normalize into stable contracts;
- settlement-source price must anchor pricing;
- paper and live must use the same decision output;
- every signal and blocked signal must be logged;
- live execution must stay behind explicit gates;
- executable bid/ask beats midpoint;
- ETF options context must prove value before it drives decisions.

## Open Decisions For Enoch

1. **Compiled core language**: C++20 default, or Rust?
2. **First live venue focus**: Polymarket, Jupiter, or both read-only first?
3. **Initial support/resistance method**: hybrid simple filter, or stricter
   candle-only filter?
4. **Storage hot path**: in-memory + Parquet/DuckDB first, or add QuestDB from
   the start?
5. **Execution style for research**: taker-only paper fills first, maker quote
   simulation first, or both side-by-side?
6. **ETF options priority**: build after core calibration, or in parallel as
   context-only data?

## Risk Register

| Risk | Why It Matters | Mitigation |
| --- | --- | --- |
| Wrong settlement source | Model can be directionally right but settle wrong | Settlement-source adapter and divergence flags |
| Midpoint illusion | Apparent edge disappears at bid/ask | Use executable price only |
| Stale order book | Fast market eats old quotes | Staleness gate and latency telemetry |
| Final-second chaos | Distribution can jump near expiry | Avoid final seconds until proven |
| Support/resistance traps | Path model underestimates rejection/wick risk | Structure filter and blocked-signal reports |
| ETF options false signal | Correlation may not hold at 5m/15m horizon | Context lane and ablation reports |
| Venue legal restrictions | Access differs by jurisdiction | Compliance mode per venue |
| Overfitting thresholds | Strategy only works in sample | Calibration splits and out-of-sample reports |
| Market-maker adverse selection | Fills happen when wrong | Cancel/reprice policy and fill-quality reports |

## Success Criteria

The architecture is working if:

- it can replay raw observations into identical decisions;
- `p_finish` and `p_no_touch` are calibrated by bucket;
- edge exists against executable bid/ask, not midpoint;
- support/resistance blocking improves false-positive rate;
- ETF options context either improves reports or is rejected cleanly;
- local decision latency is measured and small relative to venue latency;
- live mode cannot activate accidentally;
- every action can be traced back to data, model inputs, and policy gates.

## References

See `docs/research-worthiness-2026-05-28.md` for academic and market-structure
sources supporting this design.

