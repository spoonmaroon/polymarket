Paste this into docs/observations.md as the combined ML/BART roadmap:

# Hybrid Monte Carlo, ML Calibration, and BART Research Plan
## Core Recommendation
Do not abandon Monte Carlo yet.
The project should move toward a hybrid architecture:
```text
Monte Carlo = base probability engine
ML = correction / calibration / meta-decision layer
BART = uncertainty-aware research/calibration benchmark

The goal is not:

Monte Carlo vs ML

The goal is:

Monte Carlo + ML calibration + uncertainty-aware decision rules

Monte Carlo is still valuable because short-dated BTC/ETH binary contracts are path-dependent in practice, even though the final payoff is binary. The current framework already separates:

p_finish
p_no_touch
z_path
sigma_tau
executable edge

That structure should stay.

Raw Monte Carlo becomes weak when its simulated paths fail to reflect live market conditions such as:

order-book pressure
liquidity changes
threshold congestion
wick risk
volatility regime shifts
final-window chaos
source-quality issues
execution costs

So the correct direction is to keep Monte Carlo as the path simulator, then train ML models to learn when Monte Carlo is too optimistic, too pessimistic, or too uncertain.

⸻

1. Current Diagnosis: Probability Calibration Problem

The model issue sounds mostly like a probability calibration problem, not simply underfitting or overfitting.

Example:

If the model predicts 80% across 500 similar states:
If only 65% win, the model is overconfident.
If 90% win, the model is underconfident.

The system should evaluate calibration by bucket, not only overall.

Important calibration buckets:

TTE bucket
z_path bucket
distance from threshold
volatility regime
UP vs DOWN
asset: BTC vs ETH
spread/depth bucket
order-book imbalance bucket
final 30-60 second window
high-congestion threshold states
source-quality state

This matters because the model can look calibrated overall but fail in dangerous slices. For example, it may be fine when BTC is far from the threshold, but overconfident when BTC is hovering near K with 90 seconds left.

Recommended calibration metrics:

Brier score
log loss
calibration curve
reliability diagram
expected calibration error
bucket-level realized win rate
bucket sample count

⸻

2. Biggest Monte Carlo Suspicion: Simulated Paths Are Too Narrow

When the model is too confident while TTE is still meaningful, the first suspicion is sigma_tau.

If sigma_tau is too small:

simulated paths are too narrow
p_finish becomes too confident
wick/reversal risk is underestimated

If sigma_tau is too large:

the model becomes too conservative
probabilities become too close to 50%
good trades may be skipped

Short-dated BTC/ETH contracts can be affected by:

sudden wicks
final-window chaos
liquidation moves
news/event shocks
threshold pinning
microstructure pressure near K
source disagreement
thin order-book depth

Example failure:

Monte Carlo says:
p_finish = 0.91
Comparable historical bucket wins:
76%

This does not automatically mean Monte Carlo should be replaced. It may mean:

path generator is too calm
volatility floor is too low
final-window risk is under-modeled
fat tails are missing
sparse bucket uncertainty is ignored
order-book pressure is missing

⸻

3. Target Architecture

The next serious architecture should be:

As-of market state
    ↓
Monte Carlo path engine
    ↓
p_finish_MC
p_no_touch_MC
z_path
sigma_tau
MC generator dispersion
    ↓
ML calibration / meta-model
    ↓
p_finish_final
probability uncertainty estimate
    ↓
Executable edge calculation
    ↓
Trade / wait / block / demand more edge

The ML model should not directly say:

BUY / DO NOT BUY

Instead, it should estimate:

calibrated probability of final win/loss

Then the decision layer should remain disciplined:

edge = p_finish_final - executable_price - costs - uncertainty_buffer - path_risk_buffer

This keeps the system from becoming a black-box trading bot.

⸻

4. ML Target

The first supervised ML target should be:

y = 1 if contract resolves as win
y = 0 if contract resolves as loss

The model should learn:

p_finish_final = f(
    p_finish_MC,
    p_no_touch_MC,
    z_path,
    sigma_tau,
    TTE,
    distance_to_threshold,
    realized_volatility,
    order_book_features,
    source_quality_features,
    threshold_congestion_features
)

The goal is to learn when Monte Carlo is systematically wrong.

Example:

Monte Carlo says: 88%
ML-calibrated model says: 73%

Interpretation:

The raw simulation is likely missing risk in this state.

Another example:

Monte Carlo says: 58%
ML-calibrated model says: 68%

Interpretation:

The order book or market structure may contain useful information that the Monte Carlo path simulation does not see.

⸻

5. Model Roadmap

The correct model order should be:

Phase 0: Runtime stability and clean data logging
Phase 1: Raw Monte Carlo calibration reports
Phase 2: Logistic regression calibrator
Phase 3: XGBoost / LightGBM calibrator
Phase 4: BART uncertainty-aware calibrator
Phase 5: Neural networks later, only if justified

Do not start with neural networks.

The data is noisy, regimes change, labels are sparse, and look-ahead risk is high. Neural networks can be explored later only after the replay-safe dataset is large and reliable.

⸻

6. Phase 0: Runtime Stability Comes First

Before full ML development, the system must stabilize:

TUI freeze
API_BLOCKED / decode errors
degraded runtime after restart
offload starts too soon after restart
sigma instability
K threshold mutation
service communication mismatch
missing automatic bug-report pipeline

ML should not be trusted until the system can prove:

as-of replay is correct
K is stable
sigma is valid
order-book state is fresh
settlement-source state is fresh
labels are correct
skip/block reasons are logged
probability outputs are versioned

If runtime stability is weak, ML will learn from corrupted states.

⸻

7. Phase 1: Dataset First

Before training models, log every as-of decision state and final label.

Required fields:

state_id
contract_id
market_slug
asset
side
asof_ts
expiry_ts
TTE
K
K_source
rule_hash
current_price
distance_to_threshold
z_path
sigma_tau
sigma_valid
sigma_age_ms
short_realized_vol
medium_realized_vol
long_realized_vol
volatility_regime
p_finish_MC
p_no_touch_MC
MC_generator_dispersion
spread
best_bid
best_ask
midpoint
target_size_ask_vwap
target_size_bid_vwap
visible_depth
orderbook_imbalance
quote_age_ms
source_age_ms
source_disagreement
threshold_cross_count
near_threshold_congestion
recent_wick_size
event_window_flag
probability_model_version
feature_version
runtime_phase
offload_allowed
skip_or_block_reason
final_label
resolved_outcome
settlement_price_at_expiry

The dataset must be replay-safe:

Only use information available at or before the decision timestamp.
Future price movement, settlement, and future market quotes are labels only.

⸻

8. Phase 2: Logistic Regression Calibrator

First model:

MC_Calibrator_LogReg_v1

Why logistic regression first:

simple
interpretable
fast
harder to overfit
good calibration baseline
shows whether p_finish_MC has real signal

Example model:

logit(p_final) =
    a
    + b1 * logit(p_finish_MC)
    + b2 * TTE
    + b3 * z_path
    + b4 * p_no_touch_MC
    + b5 * spread
    + b6 * order_book_imbalance
    + b7 * volatility_regime

Inputs:

logit(p_finish_MC)
p_no_touch_MC
MC generator dispersion
TTE
z_path
sigma_tau
distance_to_threshold
spread
orderbook_imbalance
volatility_regime

Output:

p_finish_calibrated

Purpose:

measure raw MC overconfidence
detect systematic bias
create simple benchmark
establish calibration pipeline

⸻

9. Phase 3: XGBoost / LightGBM Calibrator

Second model:

MC_Calibrator_GBDT_v1

Use:

XGBoost
LightGBM

Why:

excellent for tabular data
handles nonlinear interactions
fast enough for research and later runtime use
easier to validate than neural networks
strong benchmark before BART

This model can learn interactions like:

High p_finish_MC is trustworthy only when:
    spread is low
    ask depth is strong
    z_path is high
    p_no_touch is stable
    TTE is inside a favorable bucket
    sigma is valid
    threshold congestion is low

Inputs:

p_finish_MC
p_no_touch_MC
MC generator dispersion
TTE
side
asset
z_path
raw distance from threshold
sigma_tau
short realized volatility
medium realized volatility
volatility trend
threshold crossing count
near-threshold congestion
recent wick size
bid-ask spread
best bid
best ask
target-size VWAP
order-book imbalance
visible depth near executable price
quote age
last trade direction
source disagreement flag
event/news flag

Output:

p_finish_final

After training, probability outputs should be calibrated using:

sigmoid calibration
isotonic calibration
walk-forward calibration

⸻

10. Phase 4: BART Uncertainty-Aware Calibrator

BART stands for:

Bayesian Additive Regression Trees

BART should be explored after logistic regression and XGBoost/LightGBM, not before.

Model name:

MC_Calibrator_BART_v1

Why BART is worth exploring

BART is useful because it can estimate not just a probability, but uncertainty around that probability.

The key question for this project is not only:

What is the probability?

It is also:

How confident should the system be in that probability?

That matters because short-dated binary contracts can look attractive by mean probability but still be unsafe when the model is uncertain.

Example:

p_finish_MC = 0.86

A normal ML model might return:

p_finish_final = 0.76

A BART-style model may return:

posterior mean probability = 0.76
lower uncertainty bound = 0.61
upper uncertainty bound = 0.88

That uncertainty interval is useful for deciding whether to:

trade
wait
block
reduce size
demand more edge

⸻

BART’s role in this system

BART should not replace Monte Carlo.

BART should be used as:

offline uncertainty-aware calibration benchmark

Its purpose:

learn when Monte Carlo is uncertain
estimate probability uncertainty
increase uncertainty buffer when model confidence is weak
identify dangerous sparse buckets
compare against XGBoost/LightGBM

BART should initially run in:

research notebooks
offline training scripts
walk-forward validation reports

Do not put BART into the live TUI/runtime loop until it proves useful out of sample.

⸻

BART inputs

Use the same feature family as the GBDT model:

p_finish_MC
p_no_touch_MC
MC generator dispersion
TTE
asset
side
z_path
raw distance from threshold
sigma_tau
short realized volatility
medium realized volatility
volatility trend
threshold crossing count
near-threshold congestion
recent wick size
bid-ask spread
best bid
best ask
target-size VWAP
order-book imbalance
visible depth near executable price
quote age
last trade direction
source disagreement flag
event/news flag
runtime_phase
skip_or_block_reason

Output:

posterior distribution of p_finish_final

Useful outputs:

p_mean
p_median
p_10
p_25
p_75
p_90
posterior_width
uncertainty_score

⸻

BART-based decision logic

Do not trade from BART mean probability alone.

Use conservative probability:

p_conservative = p_10

or:

p_conservative = p_mean - uncertainty_penalty

Example:

p_mean = 0.76
p_10 = 0.68
executable_price = 0.61
costs_and_buffers = 0.03
conservative_edge = 0.68 - 0.61 - 0.03
conservative_edge = 0.04

This may be acceptable.

But if:

p_mean = 0.76
p_10 = 0.55
executable_price = 0.61
costs_and_buffers = 0.03
conservative_edge = 0.55 - 0.61 - 0.03
conservative_edge = -0.09

Then the trade should be blocked even though the mean probability looks good.

This is the main reason to explore BART.

⸻

How BART connects to the existing edge formula

Current decision logic:

edge = p_finish_final - executable_price - costs - uncertainty_buffer - path_risk_buffer

With BART:

edge_mean = p_mean - executable_price - costs - path_risk_buffer
edge_conservative = p_lower_bound - executable_price - costs - path_risk_buffer
uncertainty_buffer = f(posterior_width)

If BART posterior uncertainty is high:

increase required edge
reduce size
wait
block

This fits the existing framework because uncertainty is already part of the executable-edge calculation.

⸻

11. BART Research Questions

BART should answer these research questions:

1. Does BART improve calibration over logistic regression and GBDT?
2. Does BART reduce overconfident wrong predictions?
3. Does posterior uncertainty identify dangerous market states?
4. When BART uncertainty is high, are realized outcomes actually less stable?
5. Can BART uncertainty improve skip/block decisions?
6. Does BART help sparse buckets near threshold and final-window states?
7. Does BART produce useful uncertainty during high congestion and thin liquidity?
8. Does BART add value beyond MC generator dispersion?
9. Does BART help more for BTC, ETH, or both?
10. Is BART too slow for live use but useful for offline calibration?

⸻

12. Model Comparison Plan

Compare the following models on the same walk-forward splits:

Model A: Raw Monte Carlo
Model B: Logistic regression calibrator
Model C: XGBoost / LightGBM calibrator
Model D: BART calibrator

Evaluate with:

Brier score
log loss
calibration curve
expected calibration error
bucket-level win rate
false confidence rate
edge after executable price
profit after conservative fill assumptions
skip/block usefulness
uncertainty usefulness

Special BART metric:

uncertainty usefulness

Definition:

When BART says uncertainty is high, are those states actually dangerous, unstable, poorly calibrated, or lower EV?

If yes, BART is useful even if its raw prediction score is only similar to XGBoost/LightGBM.

⸻

13. Validation Setup

Because this is time-series trading data, do not randomly shuffle.

Use walk-forward validation:

Train: Week 1
Validate: Week 2
Train: Weeks 1-2
Validate: Week 3
Train: Weeks 1-3
Validate: Week 4

Also add a purge/embargo window because 5-minute contracts and nearby decision states can overlap.

Required validation rules:

no random shuffle
no future labels in features
no later Polymarket prices as decision features
no future settlement data as input
no leakage from overlapping windows
all features must be as-of decision timestamp

⸻

14. Separate Models for Separate Questions

Do not ask one model to solve everything.

Train separate models:

Model 1: p_finish model
Predicts final binary win/loss.
Model 2: p_no_touch / path-risk model
Predicts whether price crosses the danger line before expiry.
Model 3: execution model
Predicts whether displayed edge survives executable price, slippage, depth, and quote movement.
Model 4: failure / skip model
Predicts when the system should demand more edge or block.
Model 5: BART uncertainty model
Estimates uncertainty around p_finish_final and helps set uncertainty buffers.

This matters because a contract can have a good final win probability but still be a bad trade if:

path is unstable
order book is too thin
spread is too wide
quote is stale
source quality is poor
model uncertainty is high

⸻

15. Order-Book Data Caution

Order-book data is useful, but it must be handled carefully.

Useful features:

spread
best bid
best ask
midpoint
target-size ask VWAP
target-size bid VWAP
visible depth
depth imbalance
quote age
recent quote movement
last trade price
last trade direction
book replenishment after trades

But do not let the model blindly learn:

Polymarket price = probability

The goal is not to predict Polymarket price.

The goal is:

predict true settlement probability better than executable market price

First label:

final outcome: win or lose

Not:

future Polymarket price

Later, train execution/PnL models. First, get calibrated final probabilities.

⸻

16. Practical Implementation Order

Implementation status: the active build plan is `docs/superpowers/plans/2026-06-15-backtest-replay-xgboost-calibration.md`. The first shipped scope is replay-safe dataset export, offline backtest, logistic calibration, and XGBoost calibration. BART remains an offline benchmark after the simpler models have walk-forward evidence.

Step 1: Keep Monte Carlo

Do not replace the current Monte Carlo engine.

Keep producing:

p_finish_MC
p_no_touch_MC
z_path
sigma_tau
MC generator dispersion

Step 2: Log every as-of decision state and final label

No ML without clean, replay-safe logging.

Step 3: Build calibration reports

Slice by:

TTE
z_path
distance from threshold
volatility regime
asset
side
order-book state
spread/depth
threshold congestion
final 30-60 second window

Step 4: Fix obvious Monte Carlo issues

Fix:

volatility floor
final-window bucket
fat-tail/wick stress
sparse bucket penalty
generator dispersion buffer
sigma instability
K mutation
invalid/stale inputs

Step 5: Train logistic regression calibrator

Model:

MC_Calibrator_LogReg_v1

Step 6: Train XGBoost/LightGBM calibrator

Model:

MC_Calibrator_GBDT_v1

Step 7: Calibrate GBDT probabilities

Use:

sigmoid calibration
isotonic calibration
walk-forward calibration

Step 8: Explore BART offline

Model:

MC_Calibrator_BART_v1

Use it to estimate:

p_mean
p_lower_bound
posterior_width
uncertainty_score

Step 9: Add uncertainty-aware edge

Use:

conservative_edge = p_lower_bound - executable_price - costs - path_risk_buffer

or:

edge = p_mean - executable_price - costs - path_risk_buffer - uncertainty_buffer

Step 10: Only paper-trade when calibrated probability beats executable price

Require:

runtime_phase == READY
data fresh
K stable
sigma valid
probability calibrated
executable price valid
edge positive after costs and buffers

⸻

17. Final Recommended Model Stack

The best model stack for the project is:

Monte Carlo base engine
    ↓
Logistic regression calibrator
    ↓
XGBoost / LightGBM calibrated classifier
    ↓
BART uncertainty-aware research model
    ↓
Executable edge and risk-gated decision layer

The first serious version should be:

p_finish_MC
    ↓
XGBoost / LightGBM meta-model
    ↓
calibrated p_finish_final
    ↓
edge vs executable Polymarket price

The more advanced uncertainty-aware version should be:

p_finish_MC
p_no_touch_MC
z_path
sigma_tau
order-book features
market-structure features
    ↓
BART offline calibrator
    ↓
p_mean + p_lower_bound + posterior_width
    ↓
uncertainty-adjusted edge
    ↓
trade / wait / block / demand more edge

⸻

18. Key Rule

Do not use BART because it sounds advanced.

Use BART only if it answers this question better than the simpler models:

When should the system distrust its own probability?

If BART helps identify false confidence, sparse buckets, unstable threshold states, or dangerous high-uncertainty regimes, then it is valuable.

If it does not improve calibration or uncertainty-aware decision-making out of sample, keep it as a research note and use XGBoost/LightGBM for the main calibrator.

⸻

19. Questions for Enoch

Before implementing the BART plan, answer these:

1. Do you want BART to be purely offline research first, or eventually part of the live probability stack?
2. Should BART uncertainty directly increase the required edge, or should it only create a warning/block signal?
3. What conservative bound should be used for decision-making: p10, p25, or mean minus uncertainty penalty?
4. Should BART train on BTC only first, or BTC and ETH together with asset as a feature?
5. How many clean contracts or decision states should be collected before running BART experiments?
6. Should the first ML target be final win/loss, p_no_touch/path crossing, or executable edge?
7. Should BART be compared against MC generator dispersion to see whether it adds new uncertainty information?
8. Should BART use Polymarket order-book features, or should the first version use only MC/path/volatility features?
9. Should high BART uncertainty block all trades, or just reduce size/demand more edge?
10. Should the project prioritize fastest deployable calibrator first, or best uncertainty research first?

⸻

20. Suggested Codex Instruction

You are working on the Polymarket BTC/ETH probability engine.
Create a research and modeling roadmap for a hybrid Monte Carlo + ML calibration system. Do not replace Monte Carlo. Treat Monte Carlo as the base probability engine and ML as a calibration/meta-model layer.
The immediate implementation order should be:
1. Keep Monte Carlo outputs: p_finish_MC, p_no_touch_MC, z_path, sigma_tau, MC dispersion.
2. Build replay-safe logging of every as-of decision state and final label.
3. Build calibration reports by TTE, z_path, distance, volatility regime, asset, side, order-book state, and threshold congestion.
4. Implement MC_Calibrator_LogReg_v1 as the first baseline model.
5. Implement MC_Calibrator_GBDT_v1 using XGBoost or LightGBM as the stronger tabular model.
6. Calibrate probabilities with walk-forward sigmoid or isotonic calibration.
7. Add MC_Calibrator_BART_v1 as an offline uncertainty-aware research model.
8. Use BART posterior uncertainty to estimate p_mean, p_lower_bound, posterior_width, and uncertainty_score.
9. Feed uncertainty into the decision layer as higher required edge, reduced size, wait, or block.
10. Do not add neural networks until the replay-safe dataset is large, stable, and the GBDT baseline is beaten out of sample.
Do not enable live trading.
Do not let ML directly output buy/sell.
Do not use future Polymarket prices or settlement data as decision-time features.
Preserve the read-only research workflow.
