# Polymarket BTC Binary Methodology Decisions

Date: 2026-05-29

This note captures the current state of the BTC binary path-probability idea, the document work already done, and the decisions that should guide the next edits. It exists so future work does not restart from a half-remembered conversation.

## Current Project Shape

The project is a standalone BTC binary prediction-market research system, not a GEX strategy port. The first target is short-dated BTC UP/DOWN markets, especially 5-minute and 15-minute contracts.

The current research paper lives in:

- `/Users/goon/Downloads/Polymarket idea.docx`
- `/Users/goon/polymarket/reports/generated/BTC_Binary_Path_Probability_Incomplete_Research_Paper.docx`

The project repo is:

- `/Users/goon/polymarket`

The repo already has a barebones scaffold with Python/FastAPI, C++ probability-core skeleton, React/Vite UI, docs, reports, config, and secrets hygiene.

## Core Concept

The system should not predict whether BTC is generally bullish or bearish. It should price a specific short-dated binary contract at a specific moment.

The core question is:

```text
Given the current settlement-source BTC price,
the contract threshold,
the time remaining,
and the current market state,
is the executable contract price mispriced after costs and risk?
```

The important distinction is:

```text
p_finish = terminal win probability
p_no_touch = path survival probability
z_path = normalized distance from the danger line
```

A trade can have high `p_finish` but low `p_no_touch`. That means the final settlement may still be favorable, but the path is unstable enough that the system should wait, block, or demand more edge.

## Current Section 4 Decision

Section 4 should define the core outputs and explain that Monte Carlo is the intended primary estimator for `p_finish` and `p_no_touch`. Closed-form formulas are retained only as sanity checks, debugging tools, and emergency fallbacks. Section 4 should not explain the full backtest, full volatility-surface construction, ETF options usage, XGBoost, or the whole execution methodology.

The cleaner Section 4 structure should be:

```text
4. Core Model Outputs and Monte Carlo Calculations

4.1 Shared Variables
4.2 Why Monte Carlo Is the Primary Estimator
4.3 p_finish: Meaning and Monte Carlo Calculation
4.4 p_no_touch: Meaning and Monte Carlo Calculation
4.5 z_path: Distance Normalization
4.6 Speed: Cached Monte Carlo and Conditional Refresh
4.7 Closed-Form Baselines for Sanity Checks
4.8 Core Outputs Passed to the Decision Layer
```

Section 4 should pair each idea with the calculation that makes it concrete. The Monte Carlo calculations should be primary. Explanatory/comparison material should be prose and bullets, not tables everywhere.

The user liked the Black-Scholes-style presentation where formula blocks have variable definitions nearby. Use that style for formulas, but do not turn every paragraph into a table.

## Variables for Section 4

Use these before formulas:

```text
K = venue-defined threshold or reference price

S_t = current settlement-source BTC price at decision time t

S_T = final settlement-source BTC price at expiry T

tau = seconds remaining until expiry

side = UP or DOWN contract direction

S_u = BTC settlement-source path between current time t and expiry T

N = number of simulated paths

sigma_tau = expected remaining BTC movement over tau

mu_tau = expected side-adjusted drift over tau; zero in the first version unless validated

P_exec = executable contract price, usually ask for entry and bid for exit

Phi(.) = standard normal cumulative distribution function
```

Important: `S_t`, `S_u`, and `S_T` should refer to the settlement-source price or closest validated proxy, not a random BTC chart price.

## p_finish Definition

`p_finish` is the probability that the contract finishes on the winning side of the threshold at expiry.

For an UP contract:

```text
winning finish event = S_T > K
```

For a DOWN contract:

```text
winning finish event = S_T < K
```

So:

```text
p_finish = P(contract finishes on winning side of K)
```

Monte Carlo calculation:

```text
UP: win_i = I(S_T^(i) > K)
DOWN: win_i = I(S_T^(i) < K)
p_finish_MC = (1 / N) * sum(win_i)
```

Closed-form sanity-check baseline:

```text
x_UP = ln(S_t / K)
x_DOWN = ln(K / S_t)
p_finish = Phi((x_side + mu_tau) / sigma_tau)
```

If `mu_tau = 0`, then:

```text
p_finish = Phi(z_path)
```

If `p_finish = 0.84`, the raw fair value of a one-dollar binary payoff is about `$0.84` before spread, fees, slippage, latency, model uncertainty, and fill risk.

`p_finish` is not a complete trade decision. It does not tell us whether the path before expiry is stable, whether the market is liquid enough to enter, or whether the price is too close to support/resistance.

## p_no_touch Definition

`p_no_touch` is the probability that BTC does not cross back through the danger line before expiry.

For an UP contract that is already above the threshold:

```text
p_no_touch_UP = P(min(S_u for t <= u <= T) > K)
```

For a DOWN contract that is already below the threshold:

```text
p_no_touch_DOWN = P(max(S_u for t <= u <= T) < K)
```

Decision interpretation:

```text
high p_finish + high p_no_touch = cleaner setup

high p_finish + low p_no_touch = unstable setup; wait, block, or demand more edge
```

Monte Carlo calculation:

```text
UP: survive_i = I(min(S_u^(i)) > K)
DOWN: survive_i = I(max(S_u^(i)) < K)
p_no_touch_MC = (1 / N) * sum(survive_i)
```

Closed-form driftless continuous-path sanity-check baseline:

```text
z_path = x_side / sigma_tau
p_no_touch = max(0, 2 * Phi(z_path) - 1)
```

This is only a baseline. The real system should later estimate `p_no_touch` with replayed paths or Monte Carlo because continuous Brownian assumptions miss discrete ticks, jumps, final-window wicks, and exchange-feed gaps.

## z_path Definition

`z_path` measures how far the current BTC price is from the threshold after adjusting for expected remaining movement.

For an UP contract:

```text
d_UP = ln(S_t / K)
```

For a DOWN contract:

```text
d_DOWN = ln(K / S_t)
```

Then:

```text
z_path = d_side / sigma_tau
```

Use log distance because BTC movement is naturally proportional and log returns handle compounding cleanly. Raw dollar distance is intuitive but weak; percentage distance is better; log distance scaled by expected remaining movement is the useful version.

Interpretation:

```text
z_path near 0 = price is close to the danger line

z_path around 1 = current cushion is about one expected remaining move

z_path around 2 = current cushion is about two expected remaining moves
```

`z_path` is not the final probability. It is a standardized distance measure used by the probability engine, the path-risk model, and the decision gates.

## Outputs Not Yet Accepted as Core Section 4 Outputs

These are useful diagnostics, but they were introduced too early and should not be treated as official core outputs yet:

```text
MAE = max adverse excursion
wick_risk = final-window adverse move risk
model_uncertainty = reliability warning
```

They can appear later in risk/methodology sections if the user agrees, but Section 4 should stay focused on:

```text
p_finish
p_no_touch
z_path
raw_fair_value
edge_before_costs
mc_uncertainty
```

## Methodology Direction

The likely main method is:

```text
As-Of Walk-Forward Empirical Monte Carlo with cached live probability grids
```

But the method choice should be explained later, after Section 4 defines the outputs and first-pass calculations.

The user wants the paper structure to be:

```text
Section 4 = What are we calculating, and how does Monte Carlo estimate it?
Section 5 = What market-state inputs feed those calculations?
Later sections = How do we estimate it, backtest it, avoid overfit, and make decisions?
```

This is better than putting all methodology into Section 4 because Section 4 otherwise becomes jumbled.

## Historical Data and Overfit Rules

Historical data must be used carefully.

Historical Polymarket BTC contracts are needed to backtest the idea:

```text
contract threshold K
bid/ask and executable price
expiry
market state
resolution result
```

Historical BTC data is used to reconstruct what the engine would have seen at each old contract timestamp:

```text
current S_t
recent BTC returns up to t
realized volatility up to t
BTC path behavior up to t
support/resistance context up to t if available
```

The critical rule:

```text
At replay time t, the model may only use information timestamped <= t.
```

Do not use:

```text
future BTC candles after t
future volatility after t
final settlement S_T as input
future Polymarket price movement
future contract outcome
future ETF IV movement
end-of-day option chain snapshots unless they were truly available at t
```

Future data can be used only as labels after the decision has been made.

## ETF Options / GEX Context

ETF options context is useful but should not be required for the first historical backtest unless true timestamped historical option-chain data exists.

If historical ETF chain snapshots are not available, the first clean backtest should exclude ETF options features.

ETF/GEX options context should become a prospective enhancement:

```text
Phase 1 = historical Polymarket + historical BTC only

Phase 2 = start collecting live ETF option-chain data now

Phase 3 = after enough as-of ETF data exists, test core engine vs core + ETF context
```

This avoids fake backtests that accidentally use future chain data.

## p_finish Method Discussion So Far

The current lognormal `p_finish` calculator is not the best final method. It is a good first baseline because it is transparent, fast, and falsifiable.

The previously discussed baseline:

```text
x = ln(S_t / K)

p_finish_UP = Phi((x + mu_tau) / sigma_tau)
```

Where:

```text
S_t = current settlement-source BTC price
K = contract threshold
tau = time left
sigma_tau = expected remaining log-price volatility
mu_tau = expected drift over the remaining window, probably zero at first
Phi = normal CDF
```

But this assumes the remaining distribution is basically normal. For 5m/15m BTC, that is too neat because crypto has jumps, wicks, volatility clustering, exchange dislocations, and regime shifts.

Better long-run p_finish candidates discussed:

```text
closed-form lognormal baseline
empirical bucketed probability
Monte Carlo p_finish
HAR-RV / GARCH volatility adjustment
market-implied benchmark
XGBoost calibrated probability
```

However, do not dump all of this into Section 4. Section 4 should define `p_finish`; later methodology sections should compare estimation methods.

## Next Paper Edits

Before editing the DOCX again:

1. Agree on the exact Section 4 text.
2. Keep Section 4 focused on outputs only.
3. Move methodology details into later sections.
4. Explain historical replay and overfit control separately.
5. Keep ETF options/GEX context as later enhancement unless as-of historical option data exists.
6. Render and visually inspect the DOCX after edits.

## Current User Preference

The user wants more questions before edits. Do not guess the methodology or document structure too aggressively.

Use a one-question-at-a-time process:

```text
ask
wait
adjust
then edit only after approval
```

## 2026-05-30 Update: Sections 6-14 Direction

The user approved moving forward with the section rewrite and specifically asked to prioritize XGBoost. The current method contract is:

```text
Monte Carlo = primary pricing and path-risk estimator
XGBoost = early challenger, blocker, and calibration layer
```

XGBoost should be introduced earlier than before, but it should not be the first authority for live trade creation. The reason is that XGBoost needs clean replay labels, chronological validation, and probability calibration before its output can be trusted as a direct trading signal.

The paper structure after Section 5 should now be:

```text
6. As-Of Monte Carlo Methodology
7. Historical Replay Dataset and Labels
8. XGBoost Challenger and Calibration Layer
9. Historical Polymarket Backtest Design
10. ETF Options and GEX Context
11. Decision Gates and Execution Logic
12. Validation and Falsification
13. Open Questions and Next Work
14. References and Data Documentation
```

The Monte Carlo section should explain the live/replay method as:

```text
As-Of Walk-Forward Empirical Monte Carlo with cached live probability grids
```

Default implementation choices:

```text
path model = empirical BTC path resampling, later stress overlays
time step = 1 second when reliable, 5 seconds as fallback
path count = 5,000-10,000 near trade, cached lookup otherwise
refresh = conditional on seconds_left, z_path, vol regime, quote staleness, or near-entry states
```

The replay dataset should exist partly to support XGBoost. Each replay row should contain only information available at decision time `t`, plus labels added after expiry.

Initial XGBoost features:

```text
p_finish_MC
p_no_touch_MC
z_path
mc_uncertainty
seconds_left
realized volatility windows
volatility trend/regime
recent wick frequency
support/resistance distance
prediction-market spread
depth
quote age
source quality
```

Initial XGBoost targets:

```text
false_positive_risk
profitable_after_costs_probability
calibration_adjustment
```

Decision logic:

```text
Monte Carlo proposes the candidate fair probability.
Structure gates can block the trade.
XGBoost can block the trade or demand more edge.
XGBoost cannot create new live trades until it wins out-of-sample and is calibrated.
```

ETF options and GEX-derived data should remain a later context layer unless true timestamped historical ETF option-chain snapshots exist. If that history is missing, the first clean replay should be historical Polymarket plus historical BTC only.

## 2026-05-30 Update: Live Shadow Backtester and Speed Requirement

OHLC data is not equivalent to tick or ticker data. OHLC can help estimate volatility and broad regime, but it does not preserve intraperiod path order. This is especially dangerous for `p_no_touch`, because path survival depends on whether BTC crossed the danger line before expiry.

The preferred validation path is therefore:

```text
historical replay = rough initial falsification
live shadow backtester = main clean dataset builder
real money = only after validation
```

The live shadow backtester should watch real contracts without trading:

```text
1. Read live BTC tick or 1-second data.
2. Read live Polymarket contract and order book data.
3. Compute p_finish_MC, p_no_touch_MC, z_path, spread, depth, and quote age.
4. Let the decision engine output trade / wait / block / demand_more_edge.
5. Store the exact as-of row.
6. After expiry, append the real outcome labels.
```

Free or mostly free live feeds should be treated as data sources, not complete solutions:

```text
Polymarket Market WebSocket = contract order book, price changes, trades
Polymarket RTDS crypto prices = venue crypto price feed / settlement proxy context
Binance / Coinbase / Kraken WebSockets = independent BTC spot validation
Freqtrade dry-run = reference design for live simulated trading
Hummingbot paper trade = reference design for paper market-making behavior
```

The paper should emphasize that the project needs a custom live shadow logger because generic crypto backtesters do not understand the binary-contract state: threshold `K`, expiry, bid/ask, quote age, depth, settlement source, `p_finish`, `p_no_touch`, and later path-survival labels.

Speed is also now a core architecture requirement. Many contracts converge quickly, so the live engine cannot wait for a fresh Monte Carlo run on every tick. The system should split into:

```text
slow research path = Monte Carlo grid construction, calibration, XGBoost training, reports
fast live path = cached probabilities, interpolation, simple gates, immediate decision
```

The intended live hot path is:

```text
state update -> z_path -> cached probability lookup -> edge check -> gate check -> async log
```

Possible language split:

```text
C++ hot loop = state -> z_path -> cached probability lookup -> decision
Python research layer = data collection, Monte Carlo grids, XGBoost, backtests, reports, UI
```

The strategic idea is not merely to have a slow smarter model. The edge, if it exists, is more likely to come from knowing a fair value before the market fully adjusts while still refusing trades when the path, quote, or data quality is unstable.

## 2026-05-30 Update: Monte Carlo Conditioning Variables and ETF Options Role

Monte Carlo does not use "features" in the same way XGBoost does. For the paper and implementation, call them conditioning variables or state variables. They select, scale, and simulate comparable BTC paths rather than training weights.

Core Monte Carlo conditioning variables:

```text
side
seconds_left
horizon
threshold K
S_t
d_side
z_path
sigma_tau
short/medium/long realized-vol windows
vol_regime
vol_trend
recent wick frequency
recent danger-line crosses
max adverse move bucket
source_quality_flag
data_granularity
feed_disagreement
stale_price_flag
```

Execution variables such as spread, depth, quote age, and executable price should usually remain decision gates rather than path-generation variables. ETF options variables should remain optional context until they pass ablation testing.

ETF options role to include in the paper:

```text
ETF options data will not be used as a primary predictor in the first version.
Instead, it will be used as an as-of volatility, skew, and risk-appetite context layer.
Its purpose is to adjust sigma_tau, p_no_touch, model uncertainty, and required edge.
The value of this layer will be tested through ablation: core BTC engine versus core BTC engine plus ETF options context.
```

ETF options candidate variables:

```text
ETF_IV_stress
ETF_skew_stress
ETF_flow_flag
IBIT_ATM_IV
IBIT_IV_change_1m / 5m / 15m
put_skew
call_skew
risk_reversal
put_call_volume_ratio
put_call_premium_ratio
delta_weighted_call_flow
delta_weighted_put_flow
vega_weighted_flow
large_trade_flag
open_interest_change
```

Do not add a final noise methodology to the paper until research is reviewed with the user. The current next step is to research effective market-microstructure noise mitigation methods and then choose which ones fit this short-dated BTC binary system.

## 2026-05-30 Update: Practical-First Noise Method

The selected paper direction is practical-first noise handling before using heavier high-frequency estimators. The first implementation should use robust live state construction and explicit buffers, then test heavier estimators only after live shadow data exists.

Guiding rule:

```text
filter bad data noise
preserve real path risk
price execution noise as a buffer
```

First implementation:

```text
S_t_raw = latest settlement-source/proxy price
S_t_filtered = robust median or robust mid over last 1-3 seconds
sigma_tau = max(realized_sigma_tau, volatility_floor)
required_edge = base_edge + spread_buffer + latency_buffer + noise_buffer + mc_uncertainty_buffer
```

Noise controls:

```text
bad tick / feed jump -> source agreement checks, stale-feed checks, robust median
bid/ask bounce -> robust mid or short rolling median
low-vol false calm -> volatility floor
high-vol regime shift -> refresh MC buckets, raise mc_uncertainty, demand more edge
thin comparable path bucket -> bucket_sample_size and mc_uncertainty gate
latency/slippage/fill risk -> execution buffers inside required_edge
```

Heavier estimators to test later:

```text
realized kernels
pre-averaging
two-scale realized volatility
```

Promotion rule:

```text
A heavier estimator should only replace the practical first version if it improves out-of-sample calibration, p_no_touch accuracy, and executable EV after costs.
```
