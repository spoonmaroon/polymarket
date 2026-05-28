# Architecture Inquiry

Date: 2026-05-28

## Purpose

Gather requirements for a detailed architecture before building the
Polymarket crypto binary probability engine.

## Working Thesis

The edge is not generic directional prediction. The edge is fast, disciplined
pricing of remaining path probability during short-dated BTC/ETH/SOL binary
markets, especially when volatility is decreasing and the contract has not yet
fully converged to its true remaining probability.

The system should not be Polymarket-only. It should be designed around a
venue-adapter layer so Polymarket, Jupiter Prediction Markets, and other DeFi or
regulated prediction venues can be added without rewriting the probability
engine.

## Candidate Build Shape

A local system with production-shaped boundaries and execution disabled by
policy until approved. It should:

1. discover live BTC/ETH/SOL 5m or 15m up/down markets across supported venues,
2. stream settlement-source price data,
3. stream or poll venue order book and pricing data,
4. calculate `p_finish`, `p_no_touch`, and expected remaining move,
5. check whether the underlying is near 5-minute or 15-minute
   support/resistance,
6. optionally attach BTC ETF options-flow context,
7. compare fair value to executable bid/ask after fees and slippage,
8. log every signal, non-signal, blocked signal, and outcome for calibration.

## Venue Notes

### Polymarket

Polymarket remains a useful first reference because it has crypto binary markets
and public market-data surfaces. Trading access must stay behind a compliance
gate.

### Jupiter Prediction Markets

Jupiter Prediction Markets should be treated as a first-class candidate venue.
Current Jupiter developer docs describe a beta Prediction API at
`https://api.jup.ag/prediction/v1`, with events, markets, orders, positions,
history, and provider filters. Jupiter documentation also says the API is beta
and geographically restricted in some jurisdictions, so the first implementation
should support read-only research mode before any trading path.

### Other Venues

Any new venue must answer these before becoming tradable:

- What is the settlement source?
- What is the exact resolution rule?
- Is pricing CLOB-based, AMM-based, quote-based, or an aggregator over another
  venue?
- Are bid/ask/depth and fills observable with enough latency?
- What are the fees, rebates, and payout rules?
- Is access legal for the operator's jurisdiction?

## Data Source Notes

### Crypto Spot / Candles

Free official APIs should be enough for the first support/resistance,
volatility, and spot-tracking layers. Coinbase and Binance both publish public
market-data WebSocket or REST surfaces. The architecture should wrap these in
exchange adapters and compare them against the actual settlement source.

### Settlement / Oracle Price

For binary pricing, the settlement-source price is the anchor. Exchange spot
feeds are supporting data unless the market's resolution rule explicitly uses
that exchange.

### Prediction Venue Data

Prediction venue APIs provide market discovery and executable pricing. The
engine must store executable bid/ask/depth, not just displayed probability.

### BTC ETF Options

BTC ETF options and order flow may correlate with short-dated BTC binary
outcomes, but this is a hypothesis to test. It should be a separate feature
lane named `etf_options_context`.

Initial version:

- borrow the existing GEX collector architecture where possible
- collect chain/flow features for BTC-linked ETFs when legally available
- normalize features by ETF liquidity and BTC exposure
- attach features to each BTC binary observation window
- report whether the features improve calibration or reduce false positives

Do not use ETF options flow as a live trading trigger until the data source,
latency, and predictive value are proven.

The useful GEX architecture pattern is:

1. watchlist config controls which symbols collect high-volume data,
2. chain snapshots define the contract universe,
3. a pure contract picker selects top-N contracts by open interest across the
   nearest expirations,
4. a separate collector daemon handles broker/streaming connectivity,
5. orderflow events normalize into a stable schema,
6. hot storage supports recent reads,
7. durable storage supports research and calibration,
8. API/report code consumes normalized events, not broker-specific payloads.

Important nuance from the GEX implementation: broker streams may not provide a
perfect trade tape. The Schwab path can derive print-like events from
level-one option/equity deltas such as `LAST_PRICE`, `LAST_SIZE`, and exchange.
For the Polymarket project, that is acceptable as contextual signal data, but
all ETF options features must carry quality and latency flags.

Candidate first BTC ETF option symbols:

- `IBIT`
- `FBTC`
- `ARKB`
- `BITB`
- other liquid spot BTC ETFs only after checking option liquidity

## Architecture Areas To Design

- data sources
- venue adapters
- market discovery across venues
- oracle/spot price ingestion
- order book ingestion
- CLOB vs AMM/aggregator normalization
- volatility model
- remaining-path probability model
- 5-minute and 15-minute model separation
- support/resistance structure filter
- cross-market BTC ETF options context
- GEX-style options context adapter
- signal rules
- paper-trading ledger
- calibration and backtest reports
- live-trading safety boundaries, if ever enabled
- hot-path performance boundaries
- C++/Rust probability core boundary
- deployment shape
- secret management
- monitoring

## First Decision Needed

Pick the initial contract horizon:

- both, but separate models and reports

Decision: model both 5-minute and 15-minute contracts, but keep separate
calibration reports, volatility windows, thresholds, and performance summaries.

## Second Decision Needed

Pick the first venue strategy:

- Polymarket first, with a venue adapter shaped for Jupiter later
- Jupiter first, with Polymarket as a reference data source
- Multi-venue skeleton from day one, with both venues initially read-only

## Third Decision Needed

Define the first support/resistance method:

- recent swing highs/lows from 5-minute and 15-minute candles
- VWAP bands plus session high/low
- round-number and psychological levels
- order-book/liquidity-wall-derived levels
- a hybrid filter, starting simple and logging each reason separately

## Fourth Decision Needed

Define the ETF options lane priority:

- borrow the GEX-style Schwab/options architecture first, in research mode
- live licensed options feed search first
- defer ETF options until the core binary probability model is calibrated

Current lean: borrow the GEX-style architecture as the first source path, but
keep it as research/context until calibration proves it adds value.

## Fifth Decision Needed

Define the performance and language split:

- C++ probability/execution-decision core from the start, Python orchestration
  around it
- Rust probability/execution-decision core from the start, Python orchestration
  around it
- full C++/Rust execution engine from the start

Decision shape:

- Do not build the full system in C++.
- Build the hot path as compiled code from the beginning.
- Keep Python for adapters, paper trading, reports, and orchestration.
- Force paper and live modes to consume the same compiled decision output.
- Keep live execution blocked behind explicit compliance, calibration, and
  kill-switch gates.
- Avoid rewriting everything in C++; optimize the hot loop and execution
  decision boundary.

Candidate `probability_core` API:

```text
Input:
- market_id
- horizon_seconds
- side
- current_price
- threshold_price
- seconds_left
- rolling_vol_state
- structure_filter_state
- executable_bid
- executable_ask
- fee_schedule
- risk_limits
- venue_latency_state

Output:
- p_finish
- p_no_touch
- z_path
- fair_value
- edge_after_costs
- blocked_reason
- signal_score
- order_intent
- cancel_or_reprice_action
- risk_decision
```

The open language choice is C++ vs Rust. C++ is natural for numerical kernels
and existing quant-library reuse. Rust is attractive if the compiled core owns
more live-state and safety-critical execution logic.
