# Ensemble And Execution Gates Design

## Decision

The next probability and decision layer stays read-only/paper by default and includes a typed `supervised_live` interface that cannot place orders. The supervised-live path is a future contract for approvals, kill switches, and audit payloads; this spec does not add credentials, signing, private-key loading, or real order submission.

## Goal

Unify four Monte Carlo generators, execution-aware edge checks, crowding checks, support/resistance gates, validation gates, skip reasons, and paper/supervised execution output into one replay-safe decision contract.

## Scope

In scope:

- Four-generator ensemble probability with generator-level `p_finish`, `p_no_touch`, diagnostics, weights, and dispersion.
- Read-only and paper decision outputs that explain `TRADE_CANDIDATE`, `DEMAND_MORE_EDGE`, `WAIT`, `BLOCK`, `PAPER_TRADE`, and `REQUIRE_MANUAL_APPROVAL`.
- Execution feasibility from executable entry price, target-size VWAP, spread, quote age, depth, latency, fill quality, and exit liquidity.
- Exit-risk modeling for cases where the trade is wrong, the path deteriorates, or book liquidity disappears before expiry.
- Crowding and microstructure checks based on order-flow/depth imbalance, spread/depth deterioration, top-of-book churn, and one-sided liquidity.
- Support/resistance gates as deterministic blockers or edge add-ons, not alpha.
- Validation gates and skip reasons with explicit machine-readable components.

Out of scope:

- Live order placement.
- Exchange signing, private-key access, funded-account setup, or credential storage.
- Making the TUI or API block on Monte Carlo generation.
- Letting a supervised-live output bypass paper/read-only gates.

## Research Basis

- Gneiting and Raftery, "Strictly Proper Scoring Rules, Prediction, and Estimation" (2007): probability generators should be scored with proper scoring rules instead of hit rate alone. Source: https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf
- Yao, Vehtari, Simpson, and Gelman, "Using stacking to average Bayesian predictive distributions" (2018): predictive mixtures can be optimized by proper-scoring utility when candidate models are imperfect. Source: https://arxiv.org/abs/1704.02030
- Almgren and Chriss, "Optimal Execution of Portfolio Transactions" (2000): execution decisions should price the tradeoff between cost, liquidity, and risk over the holding horizon. Source: https://docslib.org/doc/1384720/optimal-execution-of-portfolio-transactions
- Cont, Kukanov, and Stoikov, "The Price Impact of Order Book Events" (2014): order-flow imbalance and market depth are useful short-horizon microstructure inputs. Source: https://arxiv.org/abs/1011.6402
- Guéant, Lehalle, and Fernandez-Tapia, "Optimal Portfolio Liquidation with Limit Orders" (2012): limit-order liquidation couples price risk with non-execution risk. Source: https://arxiv.org/abs/1106.3279
- Avellaneda and Stoikov, "High-frequency trading in a limit order book" (2008): inventory and order-arrival risk matter when quoting or waiting for fills in a limit order book. Source: https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf
- Lo, Mamaysky, and Wang, "Foundations of Technical Analysis" (2000): chart structure should be converted into systematic, testable rules before it is trusted. Source: https://www.mit.edu/~wangj/pap/LoMamayskyWang00.pdf
- Polymarket CLOB docs: GTC/GTD are limit order types and FOK/FAK are immediate marketable types, so paper execution must distinguish resting-entry assumptions from immediate liquidity-taking assumptions. Source: https://docs.polymarket.com/trading/orders/create

## Existing Local Inputs

- Hot probability inputs: `data/live/probability_inputs.json`, written by the normalizer.
- Probability runtime inputs: `ProbabilityRuntimeInput` and `ProbabilityInput`.
- Current probability output: `ProbabilityOutput` with `p_finish`, `p_no_touch`, `z_path`, `model_version`, and diagnostics.
- Current DuckDB feature tables: `features.asof_state_inputs`, `features.probability_outputs`, and validation labels.
- Current TUI probability table: contract, `p_finish`, `p_no_touch`, `z_path`, `sigma_tau`, age/flags.

## Ensemble Contract

The production ensemble uses four main generator families:

1. `empirical_conditional`: as-of-safe path fragments matched by asset, horizon, seconds-left bucket, `z_path` bucket, vol regime, wick regime, and source-quality state.
2. `block_bootstrap`: recent return blocks that preserve local clustering and wick behavior.
3. `filtered_historical`: longer historical fragments filtered by volatility, threshold distance, source quality, and horizon.
4. `stress_overlay`: adverse paths that widen uncertainty and reduce confidence. Stress may never improve fair value.

The existing seeded lognormal generator remains a control/fallback. It can be emitted as diagnostics and used when the four-generator set is unavailable, but it is not the primary v1 ensemble once the path-fragment generators are implemented.

Each generator produces:

```text
generator_id
p_finish_g
p_no_touch_g
path_count
effective_path_count
seed
asof_ts
runtime_ms
sparse
diagnostics
```

The ensemble produces:

```text
p_finish = sum(w_g * p_finish_g_effective)
p_no_touch = sum(w_g * p_no_touch_g_effective)
u_gen_finish = weighted standard deviation of p_finish_g_effective
u_gen_touch = weighted standard deviation of p_no_touch_g_effective
u_gen = max(u_gen_finish, u_gen_touch)
mc_dispersion = max absolute generator deviation from weighted center
uncertainty_buffer = base_model_buffer + generator_disagreement_buffer + sparse_scope_penalty + calibration_penalty
path_diagnosis
```

`p_finish` is the fair-value anchor. `p_no_touch`, `z_path`, `u_gen`, support/resistance state, and execution conditions are gates or required-edge components.

## Weighting Rule

Seed weights:

```text
empirical_conditional = 0.40
block_bootstrap = 0.25
filtered_historical = 0.25
stress_overlay = 0.10
```

Dynamic weights are allowed only after chronological validation exists for the specific scope. The scope is:

```text
asset
horizon_seconds
seconds_left_bucket
z_path_bucket
vol_regime
vol_trend
wick_regime
source_quality_state
```

Weight learning must use labels strictly before the decision `asof_ts`. The first dynamic rule is:

```text
loss_finish_g = log_loss(p_finish_g, did_finish_win)
loss_touch_g = log_loss(p_no_touch_g, did_no_touch)
loss_joint_g = 0.70 * loss_finish_g + 0.30 * loss_touch_g
raw_weight_g = exp(-eta * decayed_mean_loss_g)
weight_g = raw_weight_g / sum(raw_weight_g)
```

If a scope has fewer than `100` labeled rows, use seed weights, mark `sparse_scope=true`, and force decision strength no stronger than `WAIT`.

## Execution Contract

Execution is modeled before any decision hint is promoted.

Entry fields:

```text
entry_mode = READ_ONLY | PAPER_FAK | PAPER_FOK | PAPER_GTC | SUPERVISED_LIVE_REQUEST
target_size
entry_price_limit
entry_vwap
entry_slippage
entry_depth_available
entry_fill_quality
entry_quote_age_ms
```

Exit fields:

```text
exit_vwap
exit_slippage
exit_depth_available
exit_quote_age_ms
exit_expected_loss_if_wrong
exit_time_buffer_seconds
can_exit_before_expiry
```

Required edge includes:

```text
base_edge
entry_slippage_buffer
exit_slippage_buffer
latency_buffer
source_quality_buffer
uncertainty_buffer
path_risk_buffer
crowding_buffer
support_resistance_buffer
```

A candidate is blocked if exit liquidity is missing. A trade that looks good on entry but cannot plausibly exit before expiry is not a valid paper candidate.

## Crowding Contract

Crowding is a defensive gate. It raises required edge or blocks.

Inputs:

```text
top_book_imbalance
depth_imbalance
order_flow_imbalance
spread_widening_bps
depth_decay_ratio
top_level_churn
one_sided_liquidity
adverse_book_movement
```

Outputs:

```text
crowding_state = OK | WATCH | CROWDED | TOXIC
crowding_buffer
crowding_reasons
```

Hard blocks:

```text
TOXIC order flow
depth collapse at target size
spread blowout
book moving against entry direction
```

## Support And Resistance Contract

Support/resistance is a gate and uncertainty input, not a directional alpha source.

Inputs:

```text
settlement_price
threshold
recent local extrema
touch_count
level_age_seconds
level_distance_bps
breakout_confirmation
```

Outputs:

```text
near_support
near_resistance
threshold_on_structure
unaccepted_breakout
support_resistance_buffer
support_resistance_reasons
```

Rules:

```text
BUY UP near resistance -> BLOCK near_resistance
BUY DOWN near support -> BLOCK near_support
threshold on major structure -> BLOCK threshold_on_structure
unaccepted breakout near threshold -> WAIT unaccepted_breakout
```

## Decision Output Contract

The decision output is read-only JSON and is persisted. It contains:

```text
decision_id
state_id
contract_id
asof_ts
execution_mode
decision_hint
paper_action
supervised_live_action
p_finish
p_no_touch
edge_after_costs
required_edge
edge_components
skip_reasons
block_reasons
wait_reasons
demand_more_edge_reasons
generator_summary
execution_summary
crowding_summary
structure_summary
validation_summary
```

Decision hints:

```text
DISABLED
BLOCK
WAIT
DEMAND_MORE_EDGE
TRADE_CANDIDATE
PAPER_TRADE
REQUIRE_MANUAL_APPROVAL
```

Supervised live is represented only by:

```text
supervised_live_action = REQUIRE_MANUAL_APPROVAL | DISABLED
live_order_intent = null
credentials_required = false
signing_enabled = false
```

Any code path that creates a live order payload must fail tests unless an explicit future task changes this contract.

## Skip Reasons

Skip reasons are stable machine-readable codes:

```text
probability_disabled
quality_blocked
stale_settlement_price
stale_orderbook
source_disagreement
wrong_side_of_threshold
not_enough_distance
low_finish_probability
weak_path_survival
generator_disagreement
sparse_generator_scope
insufficient_edge
spread_too_wide
insufficient_entry_depth
insufficient_exit_depth
exit_not_available
adverse_book_movement
crowded_order_flow
near_resistance
near_support
threshold_on_structure
unaccepted_breakout
paper_mode_disabled
supervised_live_disabled
manual_approval_required
```

## Validation Gates

Validation is chronological:

- Generator scoring uses only labeled decisions before the evaluated decision.
- Dynamic weight rows carry `trained_through_ts`.
- Replay rejects any weight row whose `trained_through_ts > asof_ts`.
- Paper execution labels compare against order-book replay at the decision time and after the decision.
- Support/resistance labels are evaluated by future path behavior, but the level inputs are as-of only.

Promotion gates:

```text
generator calibration improves log loss or Brier score out of sample
paper EV remains positive after entry and exit costs
skip reasons explain every non-candidate
supervised-live interface emits no signed order and no credential request
```

## API And TUI Surface

`/api/runtime/probabilities` should continue to expose backward-compatible fields:

```text
contract
p_finish
p_no_touch
z_path
sigma_tau
age_ms
flags
```

It may add optional fields:

```text
decision_hint
edge_after_costs
required_edge
u_gen
mc_dispersion
path_diagnosis
skip_reasons
generator_weights
execution
crowding
structure
```

The TUI should display compact columns first:

```text
Contract | p_finish | p_no_touch | Edge | Req | Hint | Reasons
```

Detailed generator/execution summaries stay JSON/API first.

## Safety Invariants

- No real-money execution.
- No private-key, API-key, or funder-address fields.
- No signing library dependency for this plan.
- All data used for a decision must be timestamped at or before `asof_ts`.
- Stress overlay cannot improve the ensemble probability.
- Sparse generator buckets cannot produce `TRADE_CANDIDATE`.
- Missing exit liquidity blocks paper candidates.
- Live action remains `REQUIRE_MANUAL_APPROVAL` at most.

