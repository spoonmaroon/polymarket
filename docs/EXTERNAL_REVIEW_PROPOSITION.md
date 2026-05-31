Initial scope:

- BTC only;
- 5-minute and 15-minute up/down binaries;
- Polymarket first;
- Jupiter Prediction API only as a secondary/read-only venue if access and data
  quality make sense;
- live trading disabled until calibration, risk, and legal/access gates pass.

The central question:

> Can we estimate the remaining path probability of a BTC binary contract better
> than the executable venue price after fees, spread, slippage, stale-data risk,
> and fill uncertainty?

We are not asking whether BTC will generally go up or down. We are asking
whether a specific binary contract is mispriced at a specific moment.

## 2. Core Contract Framing

A BTC up/down binary is treated as a digital payoff:

```text
payout = 1 if BTC settles on the winning side of the threshold
payout = 0 otherwise
```

For a BTC UP contract:

```text
win if settlement_price > threshold_price
```

For a BTC DOWN contract:

```text
win if settlement_price < threshold_price
```

This gives the basic fair-value relationship:

```text
fair_price ~= probability_of_winning
```

If the model estimates the probability of winning at `0.84`, then the first
fair value is approximately `$0.84` before costs and buffers.

## 3. Main Values The Engine Computes

### `p_finish`

Definition:

```text
p_finish = probability that BTC finishes on the winning side at expiry
```

Example:

```text
Market: BTC UP if BTC > 100,000
Current BTC: 100,080
Time left: 90 seconds
p_finish: 0.84
```

Interpretation:

```text
The model estimates an 84% chance BTC settles above 100,000.
```

Use:

```text
fair_price_raw = p_finish
```

### `p_no_touch`

Definition:

```text
p_no_touch = probability BTC does not cross back through the danger line before expiry
```

For a BTC UP contract, the danger line is usually the threshold below the
current price. For a BTC DOWN contract, it is usually the threshold above the
current price.

Example:

```text
Market: BTC UP if BTC > 100,000
Current BTC: 100,080
Time left: 90 seconds
p_finish: 0.84
p_no_touch: 0.58
```

Interpretation:

BTC may still be likely to finish above the threshold, but the path is unstable.
The price has a meaningful chance of crossing back through the danger line
before expiry.

Use:

```text
if p_no_touch < required_path_survival:
    block trade
```

This is the core of the strategy: only trade when both terminal probability and
path survival are strong.

### `z_path`

Definition:

```text
z_path = distance_from_threshold / expected_remaining_move
```

For BTC UP:

```text
distance_from_threshold = ln(current_price / threshold_price)
```

For BTC DOWN:

```text
distance_from_threshold = ln(threshold_price / current_price)
```

Interpretation:

`z_path` measures how far the current price is from the danger line relative to
the amount BTC is expected to move before expiry.

Use:

```text
if z_path < minimum_required_distance:
    block trade
```

### `edge_after_costs`

Definition:

```text
edge_after_costs =
    p_finish
    - executable_price
    - fees
    - slippage_buffer
    - latency_buffer
    - model_uncertainty_buffer
```

Use:

```text
if edge_after_costs < minimum_edge:
    no trade
```

## 4. Data Sources

### Prediction-Market Data

Primary source: Polymarket.

Polymarket provides:

- real-time market WebSocket for order book snapshots;
- price-level changes;
- last trade price events;
- best bid/ask updates;
- market creation and resolution events;
- REST endpoints for order books, prices, spreads, midpoints, and price
  history.

Official docs:

- Polymarket WebSocket overview:
  `https://docs.polymarket.com/market-data/websocket/overview`
- Polymarket orderbook docs:
  `https://docs.polymarket.com/trading/orderbook`
- Polymarket API overview:
  `https://docs.polymarket.com/api-reference`

How we use it:

```text
market metadata -> know threshold, expiry, outcome tokens, status
order book -> executable bid/ask, depth, spread
last trade / price change -> recent venue price movement
price history -> retrospective price-level backtesting where available
resolution events -> outcome labels
```

Important limitation:

Historical price data is useful, but full historical order-book replay may need
forward capture from WebSocket data. We should collect our own book snapshots
and deltas from day one.

### Settlement / Reference BTC Price

Primary target: the price source used by the actual contract resolution.

Possible sources:

- Polymarket RTDS crypto price stream;
- Chainlink-like price stream if the contract uses it;
- venue-defined price-to-beat / oracle source;
- independent exchange feeds as redundancy.

Official doc:

- Polymarket RTDS:
  `https://docs.polymarket.com/market-data/websocket/rtds`

How we use it:

```text
settlement price -> current price S
threshold -> K
time to expiry -> T
distance from threshold -> z_path
path simulation input -> p_finish / p_no_touch
```

Rule:

The settlement/reference source beats generic exchange spot when pricing the
contract.

### Free BTC Spot / Trade / Order-Flow Data

Initial free sources:

- Binance Spot WebSocket;
- Coinbase Advanced Trade WebSocket;
- Kraken WebSocket v2;
- optionally Hyperliquid for BTC perp flow.

Official docs:

- Binance Spot WebSocket streams:
  `https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams`
- Coinbase Advanced Trade WebSocket:
  `https://help.coinbase.com/en/developer-platform/websocket-feeds/advanced-trade`
- Kraken WebSocket v2 ticker:
  `https://docs.kraken.com/api/docs/websocket-v2/ticker/`

How we use it:

```text
trades -> realized volume, aggressor flow when available
level 2 / depth -> spread, imbalance, liquidity stress
tick/ticker -> redundant BTC price
candles -> realized volatility windows
```

Derived features:

```text
return_1s
return_5s
rv_10s
rv_30s
rv_120s
rv_300s
volume_5s
volume_30s
taker_buy_volume
taker_sell_volume
orderbook_imbalance
spread_bps
depth_near_mid
```

### BTC ETF Options Context

Candidate underlyings:

- `IBIT`;
- `FBTC`;
- `BITB`;
- `ARKB`;
- other BTC-linked ETF options only if liquidity justifies them.

Data source:

- Enoch's existing GEX-style options collection architecture where available;
- possible external options data provider if needed and legally/licensing-wise
  acceptable.

Examples of options data providers to evaluate:

- Polygon/Massive options trades, quotes, aggregates, snapshots, and analytics;
- Cboe/Trade Alert-style options-flow APIs;
- broker-provided options chains if available.

Reference docs:

- Polygon options trades:
  `https://polygon.io/docs/rest/options/trades-quotes/trades`
- Polygon options WebSocket overview:
  `https://polygon.io/docs/websocket/options/overview`

How we use ETF options data:

```text
ETF implied volatility -> BTC volatility regime
ETF skew changes -> downside/upside stress context
ETF call/put volume imbalance -> risk-on/risk-off context
ETF open interest changes -> positioning context
ETF large-flow flags -> event/risk warning
```

Important rule:

ETF options data is not a direct buy/sell trigger at the beginning. It is a
context and risk-adjustment lane.

Example use:

```text
if IBIT IV is rising sharply:
    increase expected_remaining_move
    lower p_no_touch
    widen latency/model buffer
    possibly block trade

if ETF put skew expands sharply:
    add downside stress flag
    reduce confidence in BTC UP contracts
```

## 5. Feature Construction

Each candidate BTC binary gets a feature frame:

```text
market_id
venue
side
threshold_price
expiry_timestamp
seconds_to_expiry
current_settlement_price
distance_from_threshold
best_bid
best_ask
spread
depth
fees
source_latency_ms
orderbook_latency_ms
rv_10s
rv_30s
rv_120s
rv_300s
volatility_slope
volume_5s
volume_30s
orderflow_imbalance
support_distance
resistance_distance
etf_iv_change
etf_skew_change
etf_flow_flag
```

This frame becomes the input to the decision engine.

## 6. Model Stack

### 6.1 Digital Option Payoff

Purpose:

Define the contract payoff.

Output:

```text
fair_price_raw = p_finish
```

Decision use:

The model can only consider a trade if the fair price exceeds the executable
price after costs.

### 6.2 Realized-Volatility / Path-Risk Surface

Purpose:

Estimate the expected remaining BTC move over the contract's remaining life.

Input:

```text
asset = BTC
horizon = 5m or 15m
seconds_to_expiry
recent realized volatility windows
volatility regime
```

Output:

```text
expected_remaining_move
volatility_regime
volatility_confidence
```

Decision use:

The expected move determines `z_path`, `p_finish`, and `p_no_touch`.

### 6.3 Remaining Path Probability

Purpose:

Estimate both terminal win probability and path survival probability.

Inputs:

```text
current price
threshold
seconds to expiry
expected remaining move
volatility trend
```

Outputs:

```text
p_finish
p_no_touch
z_path
touch_probability
```

Decision use:

```text
if p_finish < min_p_finish:
    no trade

if p_no_touch < min_p_no_touch:
    block trade

if z_path < min_z_path:
    block trade
```

### 6.4 Binary Greeks

Purpose:

Measure fragility near the threshold.

Inputs:

```text
current price
threshold
volatility estimate
time left
```

Outputs:

```text
delta = sensitivity to BTC movement
gamma = threshold instability
vega = sensitivity to volatility estimate
theta = convergence/time-decay effect
```

Decision use:

```text
if gamma too high:
    block trade as threshold_gamma_risk

if vega too high and volatility estimate is unstable:
    block trade as vol_model_fragile
```

### 6.5 Monte Carlo

Purpose:

Validate and stress-test the path model.

Inputs:

```text
current price
threshold
seconds to expiry
volatility model
drift assumption
jump/stress assumptions
```

Outputs:

```text
mc_p_finish
mc_p_no_touch
max_adverse_excursion_distribution
expected_value_distribution
```

Decision use:

Live hot path can use a faster approximation. Monte Carlo is used for:

- backtesting;
- calibration;
- model validation;
- sanity checks against the closed-form/fast path.

Optional live rule:

```text
if fast_path and Monte Carlo disagree too much:
    block trade as model_disagreement
```

### 6.6 GARCH / Volatility Forecast Challenger

Purpose:

Estimate whether volatility is likely to expand or persist.

Outputs:

```text
forecast_vol
vol_expansion_warning
vol_persistence_state
```

Decision use:

```text
if GARCH forecast_vol > realized_vol_surface by too much:
    increase expected_remaining_move
    reduce p_no_touch
    widen model_uncertainty_buffer
```

GARCH is a challenger and warning system, not the main decision maker.

### 6.7 Support / Resistance Filter

Purpose:

Block trades where structure makes path probability unreliable.

Outputs:

```text
near_support
near_resistance
threshold_on_structure
unaccepted_breakout
```

Decision use:

```text
if buying UP and near resistance:
    block trade

if buying DOWN and near support:
    block trade

if threshold sits on major structure:
    block trade
```

### 6.8 XGBoost Shadow Model

Purpose:

Learn when the deterministic probability engine is wrong.

Initial role:

Shadow model only. It should not control decisions until enough labeled BTC
data exists.

Inputs:

```text
p_finish
p_no_touch
z_path
seconds_to_expiry
distance_from_threshold
rv_10s
rv_30s
rv_120s
rv_300s
volatility_slope
spread
depth
orderflow_imbalance
support_distance
resistance_distance
etf_iv_change
etf_skew_change
etf_flow_flag
time_of_day
```

Targets:

```text
did_finish_win
did_touch_danger_line
was_profitable_after_costs
was_false_positive
```

Decision use after validation:

```text
if xgboost_false_positive_probability > max_allowed:
    block trade
```

Reason for not using it directly at the beginning:

It will overfit without enough clean BTC binary labels. It should earn control
by outperforming the deterministic engine out of sample.

## 7. Deterministic Decision Policy Tree

No model directly trades.

Models produce evidence. The decision tree converts evidence into action.

### Gate 1: Market Eligibility

```text
if venue disabled:
    BLOCKED: venue_disabled

if asset != BTC:
    BLOCKED: unsupported_asset_initial_scope

if horizon not in [5m, 15m]:
    BLOCKED: unsupported_horizon

if settlement source unknown:
    BLOCKED: unknown_settlement_source

if market not open/tradeable:
    BLOCKED: market_not_tradeable
```

### Gate 2: Data Quality

```text
if settlement_price stale:
    BLOCKED: stale_settlement_price

if orderbook stale:
    BLOCKED: stale_orderbook

if exchange/reference prices disagree beyond tolerance:
    BLOCKED: price_source_disagreement

if not enough recent ticks:
    BLOCKED: insufficient_recent_data
```

### Gate 3: Path Probability

```text
if current price is on the wrong side of threshold:
    BLOCKED: wrong_side_of_threshold

if z_path < min_z_path:
    BLOCKED: not_enough_distance

if p_finish < min_p_finish:
    NO_TRADE: low_finish_probability

if p_no_touch < min_p_no_touch:
    BLOCKED: weak_path_survival
```

### Gate 4: Fragility

```text
if gamma > max_gamma:
    BLOCKED: threshold_gamma_risk

if vega high and volatility confidence low:
    BLOCKED: volatility_model_fragile

if GARCH warns of volatility expansion:
    reduce confidence or BLOCKED: volatility_expansion_warning
```

### Gate 5: ETF Options Context

```text
if ETF IV expansion is severe:
    widen model buffer or BLOCKED: etf_vol_expansion

if ETF skew moves sharply against trade direction:
    reduce confidence or BLOCKED: etf_skew_warning

if ETF flow flag indicates event-like stress:
    BLOCKED: etf_flow_stress
```

### Gate 6: Market Microstructure

```text
if spread > max_spread:
    BLOCKED: spread_too_wide

if ask/bid depth < desired size:
    BLOCKED: insufficient_depth

if orderbook is moving against us:
    BLOCKED: adverse_book_movement

if fill probability is too low:
    NO_TRADE: weak_fill_quality
```

### Gate 7: Structure

```text
if buying UP and current price is near resistance:
    BLOCKED: near_resistance

if buying DOWN and current price is near support:
    BLOCKED: near_support

if threshold sits on major structure:
    BLOCKED: threshold_on_structure
```

### Gate 8: Edge

```text
edge_after_costs =
    p_finish
    - executable_price
    - fees
    - slippage_buffer
    - latency_buffer
    - model_uncertainty_buffer

if edge_after_costs < min_edge:
    NO_TRADE: insufficient_edge
```

### Gate 9: Risk / Sizing

```text
if max loss after trade > daily limit:
    BLOCKED: daily_loss_limit

if correlated BTC exposure too high:
    BLOCKED: exposure_limit

if VaR after trade > max_var:
    BLOCKED: var_limit
```

Sizing:

```text
base_size = configured_max_order

size_multiplier =
    edge_quality
    * p_no_touch_quality
    * liquidity_quality
    * volatility_stability
    * portfolio_room

order_size = base_size * size_multiplier
```

### Gate 10: Execution Mode

```text
if execution_mode == read_only:
    LOG_ONLY

if execution_mode == paper:
    PAPER_TRADE

if execution_mode == supervised_live:
    REQUIRE_MANUAL_APPROVAL

if allow_live_execution == false:
    live orders impossible
```

## 8. Example Candidate Decision

Market:

```text
BTC UP if BTC > 100,000
Current settlement price: 100,080
Time left: 90 seconds
Ask: 0.78
```

Model output:

```text
p_finish = 0.86
p_no_touch = 0.81
z_path = 1.75
edge_after_costs = 0.05
gamma = acceptable
ETF context = no stress warning
spread/depth = acceptable
structure = not blocked
risk = within limits
```

Decision:

```text
PAPER_TRADE
reason: positive_edge_path_survival
```

Bad candidate:

```text
p_finish = 0.86
p_no_touch = 0.58
gamma = high
near resistance = true
```

Decision:

```text
BLOCKED
reason: weak_path_survival + threshold_gamma_risk + near_resistance
```

## 9. What We Want The Reviewer To Critique

Please evaluate:

1. Is the digital-option / remaining-path framing valid for short-dated BTC
   binary contracts?
2. Is `p_finish` plus `p_no_touch` the right split, or are we double-counting
   risk?
3. Are the volatility inputs sufficient for 5m/15m BTC binaries?
4. Is ETF options context useful at this horizon, or likely too slow/noisy?
5. Are we using ETF options correctly as a risk modifier instead of a direct
   signal?
6. Are the decision gates in the right order?
7. Which gates are likely redundant, overfit, or missing?
8. Is XGBoost appropriate as a shadow model from the beginning?
9. What labels should we collect to test whether XGBoost adds value?
10. Are the proposed backtests enough to avoid midpoint-edge illusion?
11. Are there microstructure flaws in using Polymarket WebSocket orderbook data?
12. What latency assumptions are unrealistic?
13. What would make this strategy unfundable or untradeable even if model
    calibration looks good?
14. What is the simplest falsification test we should run first?

## 10. Current Bias / Recommendation

Recommended first implementation:

```text
BTC only
Polymarket market-data first
read-only + paper mode only
collect own orderbook history from day one
compute p_finish / p_no_touch using realized-vol path model
use ETF options only as context/risk modifier
run XGBoost in shadow mode
require every decision to pass deterministic policy gates
```

What would make the project worth continuing:

```text
model calibration beats executable venue prices
edge survives spread/fees/slippage/latency buffers
p_no_touch is reliable near intended entries
blocked trades are worse than accepted trades
ETF context improves out-of-sample calibration or risk blocking
XGBoost improves false-positive filtering out of sample
```

What would kill the idea:

```text
edge exists only against midpoint
edge disappears after costs
p_no_touch is unstable near expiry
ETF context adds no measurable value
XGBoost only overfits
Polymarket liquidity/latency makes fills unrealistic
```
