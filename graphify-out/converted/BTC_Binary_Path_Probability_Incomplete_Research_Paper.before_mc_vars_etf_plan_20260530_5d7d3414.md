<!-- converted from BTC_Binary_Path_Probability_Incomplete_Research_Paper.before_mc_vars_etf_plan_20260530.docx -->

Polymarket Idea: A Remaining-Path Probability Framework for Short-Dated BTC Binary Markets

# Abstract
This draft proposes a research system for pricing short-dated BTC binary prediction-market contracts. The central claim is that the tradable object is not BTC direction itself, but the remaining path risk of a specific binary payoff. The system separates three things: the core probability outputs, the as-of methodology used to estimate them, and the decision gates that decide whether a quoted contract is tradable. The first implementation is limited to BTC 5-minute and 15-minute binaries in read-only and paper-trading mode.
# Introduction
# Prediction markets and short-dated binary contract markets have grown rapidly, especially on platforms such as Polymarket. A Wall Street Journal analysis of Polymarket account data reported that roughly 0.1% of accounts captured 67% of total profits, suggesting that profitability is highly concentrated rather than evenly distributed across participants. This concentration is important because it implies that these markets may not be fully efficient at every moment. If pricing errors exist, they are likely to appear in short windows where speed, data quality, volatility, liquidity, and execution discipline matter.
# This paper examines short-dated BTC binary contracts through an option-like pricing framework. A BTC UP/DOWN contract appears simple because the payoff is binary: it resolves to either one dollar or zero. However, the pricing problem is not simply whether BTC is bullish or bearish. Each contract is tied to a specific threshold, a short expiry window, an executable market price, and a venue-defined settlement source. A useful model must therefore price the exact remaining state of the contract, not the general direction of Bitcoin.
The central idea of this project is a remaining-path probability engine. Instead of only asking whether BTC will finish above or below the threshold at expiry, the model also asks whether the path to expiry is stable enough to justify entry. This distinction matters because a contract can have a high terminal win probability while still having poor path survival. In other words, BTC may be likely to finish on the correct side, but the remaining path may be volatile enough that the trade is still dangerous.

# 1. Research Question and Scope
The research question is whether a remaining-path probability engine can identify quickly expiring BTC binary contracts whose executable price is mispriced after spread, fees, slippage, latency, model uncertainty, and fill risk.
# 2. Instrument Definition and Settlement Source
The venue (Polymarket) defines the binary contract. The model does not choose the threshold K. It decides whether the current executable price is attractive for that venue-defined contract.

The settlement source is the official price feed or rule the venue uses to decide whether the binary pays out. It is the scoreboard for the contract. The model should not treat generic BTC spot, a chart price, or a random exchange last trade as truth unless the market rules name that source.



# 3. Data Required Before Modeling
The model needs two historical datasets with different jobs. Historical BTC data reconstructs what the engine would have seen at each timestamp. Historical Polymarket BTC contract data provides the contract object, executable price, and eventual label. These roles should not be mixed.
# 4. Core Model Outputs and Monte Carlo Calculations
Section 4 defines the quantities the engine calculates and states the preferred way to estimate them. The main estimator should be Monte Carlo, not the closed-form formulas. The formulas remain useful as sanity checks, debugging tools, and emergency fallbacks, but the actual research question is whether an as-of Monte Carlo engine can price short-dated BTC binaries better than the market after costs.
Monte Carlo fits this problem because the contract is path-sensitive in practice. A binary can finish in the right direction but still be a bad trade if the path is unstable, crosses the danger line, or requires sitting through adverse wicks. Monte Carlo lets the engine simulate many possible remaining paths and measure those events directly.
## 4.1 Shared Variables
- K = venue-defined threshold or reference price.
- S_t = current settlement-source BTC price at decision time t.
- S_T = final settlement-source BTC price at expiry T.
- tau = seconds remaining until expiry.
- side = UP or DOWN contract direction.
- S_u = simulated BTC settlement-source path between current time t and expiry T.
- N = number of simulated paths.
- sigma_tau = expected remaining BTC log-price movement over tau.
- P_exec = executable contract price, usually ask for entry and bid for exit.
- I(.) = indicator function equal to 1 when the condition is true and 0 otherwise.
Important: S_t, S_u, and S_T should refer to the settlement-source price or the closest validated proxy, not a random BTC chart price. If the settlement proxy is stale or materially different from major BTC feeds, the engine should block or widen the uncertainty buffer.
## 4.2 Why Monte Carlo Is the Primary Estimator
The clean closed-form formulas assume a smooth distribution. That is useful for intuition, but it is too neat for 5-minute and 15-minute BTC binaries. BTC can jump, wick, cluster volatility, and behave differently in the final seconds. Monte Carlo can represent those path features explicitly if the simulated shocks are built from as-of historical and live data.
- Monte Carlo estimates p_finish by counting how many simulated paths finish on the winning side of K.
- Monte Carlo estimates p_no_touch by counting how many simulated paths avoid crossing the danger line before expiry.
- Monte Carlo also produces useful diagnostics: max adverse excursion, final-window wick risk, and uncertainty bands.
- Closed-form formulas stay in the system only as sanity checks and fallback estimates.
## 4.3 p_finish: Meaning and Monte Carlo Calculation
p_finish is the terminal win probability. It asks whether the contract finishes on the winning side of K at expiry. For an UP contract, the finish event is S_T > K. For a DOWN contract, the finish event is S_T < K.
If p_finish_MC = 0.84, the raw pre-cost fair value of a one-dollar binary payoff is approximately $0.84. That is still not a trade decision; the engine must also consider p_no_touch, liquidity, spread, source quality, support/resistance, and model uncertainty.
## 4.4 p_no_touch: Meaning and Monte Carlo Calculation
p_no_touch is the path survival probability. It asks whether the simulated BTC path avoids crossing back through the danger line before expiry. This is the quantity that captures your original idea: not only whether the contract finishes correctly, but whether the remaining path is stable enough to justify entry.
A contract can have high p_finish_MC but low p_no_touch_MC. That means the final settlement may still be favorable, but the path is unstable enough that the decision layer should wait, block, or demand more edge before entry.
## 4.5 z_path: Distance Normalization
z_path is still useful even when Monte Carlo is the main estimator. It measures the current distance from the threshold in units of expected remaining movement. The simulation can use z_path for bucketing, cache lookup, interpolation, and risk cutoffs.
- z_path near 0 = price is close to the danger line.
- z_path around 1 = current cushion is about one expected remaining move.
- z_path around 2 = current cushion is about two expected remaining moves.
## 4.6 Speed: Cached Monte Carlo and Conditional Refresh
A full Monte Carlo run on every tick is unnecessary and may be too slow. The engine should separate fast live updates from heavier simulation refreshes.
This design keeps Monte Carlo as the primary estimator while still respecting the speed requirement. The engine should know the approximate answer quickly and reserve expensive simulation for moments where the answer actually changes or money is at risk.
## 4.7 Closed-Form Baselines for Sanity Checks
Closed-form formulas should remain in the research system, but only as comparison tools. They are useful for debugging and for catching Monte Carlo outputs that are obviously wrong.
## 4.8 Core Outputs Passed to the Decision Layer
The decision layer should not trade from p_finish alone. It should require enough edge after costs and should treat low p_no_touch_MC, weak z_path, stale data, thin order books, or nearby support/resistance as reasons to block or demand a larger edge.
# 5. Volatility, Path Distribution, and Market-State Inputs
Section 5 defines the input layer that feeds the calculations in Section 4. The engine should not use vague market opinions. Every input must either come from timestamped BTC data, timestamped prediction-market data, or a clearly marked later enhancement such as ETF options context.
The input layer has one rule: at decision time t, the engine may only use information that would have been available at or before t. Future candles, final settlement, later Polymarket prices, and end-of-day summaries are labels for evaluation, not inputs for the decision.
## 5.1 Purpose of the Input Layer
The input layer transforms raw market data into the quantities needed by the calculator. Its job is to answer four questions before the engine prices a contract:
- What is the best current estimate of the settlement-source BTC price?
- How much can BTC plausibly move before expiry?
- Is the path becoming calmer, more violent, or unreliable?
- Is the venue market executable enough for the probability estimate to matter?
## 5.2 Settlement-Source BTC Price Inputs
The settlement-source price is the price series the venue uses to resolve the contract, or the closest validated proxy if the official source is not streamed. This source defines S_t, S_u, and S_T. If the proxy is wrong, every probability can look precise while being pointed at the wrong target.

## 5.3 Realized Volatility Inputs
Realized volatility is built from BTC log returns available before the decision time. The first version should use multiple short windows instead of one magic lookback because 5-minute binaries are sensitive to sudden volatility changes.

## 5.4 Estimating sigma_tau
sigma_tau is the expected remaining log-price movement over the seconds left until expiry. It is the main scale input for p_finish, p_no_touch, and z_path.

The exact weights should be learned with walk-forward validation, not guessed from the full historical sample. Until that validation exists, the first implementation should keep the blend simple and conservative.
## 5.5 Volatility Regime and Trend
The engine should know whether volatility is expanding, falling, or flat. This matters because the same z_path can mean different things in a calming tape versus a tape that is starting to whip.
- Vol expanding: short-window volatility is meaningfully above medium/long windows; widen sigma_tau or block marginal entries.
- Vol falling: short-window volatility is below medium/long windows; entries may become cleaner, but still require p_no_touch and liquidity gates.
- Vol unstable: rapid alternation between calm and spikes; treat model uncertainty as high.
## 5.6 Historical Path Behavior for Simulation
Historical BTC data is not a look-ahead answer key. It is used to learn realistic path shapes that the live engine could sample later: jumps, wick behavior, volatility clustering, and final-window instability. At replay time t, only historical observations ending at or before t are allowed.
This path library is what lets empirical Monte Carlo become the main estimator instead of assuming every remaining path is smooth and normal. The engine can sample path fragments from similar as-of states and then measure terminal wins, danger-line touches, and uncertainty directly.
## 5.7 Support/Resistance and Danger-Zone Filters
Support and resistance should not be treated as mystical prediction. For this system, they are structure filters. If the threshold K is too close to a local support/resistance level, the price may chop around the danger line and make p_no_touch unreliable.
- Distance from K to recent local highs/lows.
- Distance from S_t to the threshold K.
- Whether K sits inside a recent congestion zone.
- Whether recent price has repeatedly crossed the same level.
The first version should use support/resistance as a blocker or extra-edge requirement, not as a direct probability boost.
## 5.8 Liquidity, Spread, and Order-Book Inputs
A probability estimate only matters if the trade can be executed near the observed price. The model should use executable bid/ask data, not midpoint fantasy.

## 5.9 Data-Quality Flags and Block Rules
Data-quality flags prevent false precision. If the model cannot trust the source, it should not pretend the probability is clean.
- Block when the settlement proxy is stale or missing.
- Block when exchange feeds materially disagree without a clear reason.
- Block or widen uncertainty when timestamps drift or data arrives out of order.
- Block when venue order-book data is stale or too thin to execute.
- Log every blocked state so the backtest can distinguish no-trade discipline from missing opportunities.
## 5.10 Inputs Deferred From the First Version
Some inputs may be valuable later, but they should not contaminate the first clean backtest unless timestamped historical data exists.

The first research pass should prove whether the core BTC path-probability idea works before adding richer predictors. That is the cleanest way to avoid building a complicated overfit machine with very nice charts and very suspicious results.
# 6. As-Of Monte Carlo Methodology
The proposed estimation method is As-Of Walk-Forward Empirical Monte Carlo with cached live probability grids. The phrase as-of is the important part: at every historical replay or live-shadow timestamp t, the engine behaves as if it is living at that moment and cannot see the future.
The default design is empirical resampling from historical BTC paths. This is preferred over a purely normal model because short-dated BTC contracts are sensitive to wicks, jumps, volatility clustering, exchange-feed gaps, and final-window instability.
## 6.1 Path Generation Defaults

## 6.2 Cached Grid and Refresh Rules
The live engine should not run a full Monte Carlo simulation on every tick. It should maintain cached probability grids and refresh them only when the state has changed enough to matter. This keeps Monte Carlo as the primary estimator without making every decision wait for a full simulation.

## 6.3 Fast Live Path and Speed Architecture
Many short-dated binary contracts converge very quickly. In those moments, speed is not a cosmetic engineering concern; it is part of the edge. The live system should therefore split into a slow research path and a fast decision path.

The live decision should be cheap: update S_t and seconds_left, compute z_path, look up p_finish_MC and p_no_touch_MC from the cached grid, compare against executable price, run gates, then log asynchronously. Full simulation is reserved for initialization, stale caches, regime changes, and close entry decisions where money is actually at risk.
The practical goal is not simply to have a smarter model. The goal is to know the fair value before the market fully adjusts, while still refusing trades where stale data, thin depth, or unstable paths make the quote untrustworthy.
## 6.4 Replay Procedure
- Load the historical prediction-market contract state as of t: K, side, expiry, bid/ask, quote age, and available depth.
- Load BTC market data timestamped at or before t: S_t, recent returns, realized volatility, volatility trend, source-quality flags, and support/resistance context.
- Build or retrieve the valid Monte Carlo probability bucket for the current state.
- Compute p_finish_MC, p_no_touch_MC, z_path, mc_uncertainty, and block reasons.
- Compare the outputs to the executable venue price and log the decision.
- After expiry, use the final outcome only for scoring and calibration, not as an input.
No future leakage: The model cannot use future BTC candles, final settlement, future Polymarket prices, future contract outcome, future volatility, or end-of-day summaries that were not available at replay time t.
# 7. Live Shadow Backtester, Data Sources, and Labels
A historical backtest is useful for early falsification, but the cleanest dataset for this project is a live shadow backtester. The live shadow system watches real contracts, computes decisions in real time, records the exact as-of state, but does not place trades. After each contract resolves, the system appends outcome labels. This avoids the main weakness of OHLC-based replay: the model no longer has to guess the intraperiod price path.
The goal is not to find a generic free backtesting website. The better design is to use free or mostly free live data feeds and build a project-specific shadow logger around the binary-contract problem.
## 7.1 Free Live Data Sources

## 7.2 Live Shadow Row Schema

## 7.3 Labels Added After Expiry

## 7.4 Overfit and Data-Quality Controls
- Use chronological walk-forward splits, never random train/test splits.
- Keep a final untouched holdout period that is not used for tuning.
- Store model version, feature version, decision rule version, and data_granularity with every replay row.
- If only OHLC data is available, mark the replay as lower confidence because OHLC does not preserve intraperiod path ordering.
- During live replay, never use unfinished candle high, low, or close values as inputs.
# 8. XGBoost Challenger and Calibration Layer
XGBoost should be prioritized early, but not as the first authority. The first authority remains Monte Carlo because it directly estimates terminal and path probabilities. XGBoost becomes an early challenger, false-positive blocker, and calibration layer once the replay dataset has enough clean labels.
## 8.1 Feature Set

## 8.2 First XGBoost Targets

## 8.3 Promotion Rule
XGBoost should first be allowed to block or demand more edge. It should not directly create trades until it proves out-of-sample improvement without deleting most good opportunities.
- Candidate rule: Monte Carlo finds edge first.
- XGBoost rule: if false_positive_risk is high, block or demand more edge.
- Promotion requirement: improve out-of-sample EV after costs, calibration, and drawdown without heavy opportunity deletion.
- Calibration requirement: predicted probabilities must be calibrated, not only ranked.
# 9. Historical Polymarket Backtest Design
Historical Polymarket contracts are the test objects. BTC history reconstructs the market state around those contracts. The backtest should evaluate whether the engine would have identified mispriced executable contracts without knowing the future.

The backtest should be judged on executable expected value, not attractive midpoint pricing. A signal only counts if it could have been traded at the observed bid/ask with enough depth and fresh quotes.
# 10. ETF Options and GEX Context
BTC ETF options data may contain useful information about volatility, skew, and risk appetite, but it should not be required for the first historical backtest unless true timestamped historical option-chain snapshots are available.
The first clean backtest should exclude ETF options if historical chain timing is missing. ETF/GEX context should then be collected prospectively and tested as an ablation: core engine versus core engine plus ETF options context.

As-of rule: In live mode, current option-chain data is allowed. In historical replay, option-chain data is allowed only if timestamped at or before the replay decision time.
# 11. Decision Gates and Execution Logic
The decision layer converts model outputs into trade, wait, block, or demand-more-edge decisions. This is where high p_finish_MC but low p_no_touch_MC becomes actionable instead of just interesting.

## 11.1 Mandatory Gates
- p_finish_MC must be high enough relative to executable price.
- p_no_touch_MC must be high enough to avoid unstable entries.
- z_path must show enough cushion from the danger line.
- mc_uncertainty must be below the configured threshold or require extra edge.
- Spread, depth, quote age, and source agreement must pass.
- Support/resistance blockers must pass.
- XGBoost false-positive risk must not exceed the blocker threshold once the model is promoted.
- Portfolio and daily-loss rules must pass before live money is considered.
# 12. Validation and Falsification
The research should continue only if the strategy produces positive out-of-sample expected value after executable pricing, costs, and realistic market constraints. High win rate alone is not proof.

- Stop if edge exists only against midpoint and disappears at executable bid/ask.
- Stop if performance fails on the final untouched holdout.
- Stop or redesign if XGBoost improves in-sample results but worsens out-of-sample EV or deletes most valid opportunities.
- Stop if the model cannot explain why Monte Carlo and closed-form baselines diverge in important buckets.
- Treat live shadow results as the main evidence once enough contracts have been recorded.
# 13. Open Questions and Next Work
- Which settlement-source proxy is closest to each venue rule when the official tick is not directly streamed?
- How much historical Polymarket BTC contract data can be reconstructed with true bid/ask timing and quote age?
- How much BTC tick or 1-second historical data is available with reliable timestamps?
- What free live data feeds are reliable enough for a continuous shadow logger?
- How quickly do short-dated BTC binary markets converge after the model detects edge?
- Does empirical Monte Carlo beat the lognormal sanity-check baseline out of sample?
- How many live shadow rows are needed before XGBoost can be trusted as a blocker?
- Which XGBoost target is most useful first: false_positive_risk, profitable_after_costs_probability, or calibration_adjustment?
- When should ETF options/GEX context be tested prospectively?
- Which decision gates are redundant, overfit, or too strict?
# 14. References and Data Documentation
- Polymarket Market WebSocket / market channel documentation: https://docs.polymarket.com/market-data/websocket/market-channel
- Polymarket RTDS crypto price documentation: https://docs.polymarket.com/market-data/websocket/rtds
- Polymarket order book documentation: https://docs.polymarket.com/trading/orderbook
- Binance Spot WebSocket streams: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams
- Coinbase Advanced Trade WebSocket feeds: https://help.coinbase.com/en/developer-platform/websocket-feeds/advanced-trade
- Kraken WebSocket v2 ticker: https://docs.kraken.com/api/docs/websocket-v2/ticker/
- Freqtrade dry-run / simulated trading documentation: https://docs.freqtrade.io/en/stable/configuration/
- Hummingbot paper trade documentation: https://hummingbot.org/client/global-configs/paper-trade/
- Bollerslev, T. (1986). Generalized Autoregressive Conditional Heteroskedasticity. Journal of Econometrics.
- Corsi, F. (2009). A Simple Approximate Long-Memory Model of Realized Volatility.
- Chen, T., and Guestrin, C. (2016). XGBoost: A Scalable Tree Boosting System.
- Naeini, M. P., Cooper, G. F., and Hauskrecht, M. (2015). Obtaining Well Calibrated Probabilities Using Bayesian Binning.
- Internal GEX reuse references: src/gex/green/orderflow_picker.py; docs/plans/green-option-chain-and-source-migration.md; docs/superpowers/specs/2026-05-17-orderflow-live-toggle-design.md.
- Internal GEX convention caveat: docs/decisions/gex-conventions.md; GEX signs are descriptive assumptions, not direct dealer-position truth.
| Status: This is a research and execution-design paper. It explains the current idea, proposed data sources, model outputs, decision methodology, and open risks. It is not a finished empirical result and does not recommend live trading. |
| --- |
| Scope Item | Decision |
| --- | --- |
| Initial asset | BTC only. |
| Initial contracts | 5-minute and 15-minute UP/DOWN binaries. |
| Initial venue focus | Polymarket BTC contracts first, assuming market rules and historical market data can be reconstructed honestly. |
| Initial mode | Read-only logging and paper trading. No live-money execution until calibration survives out-of-sample tests. |
| Core thesis | The model prices the contract state, not a general BTC forecast. |
| Binary payoff | Variable Key |
| --- | --- |
| BTC UP payoff = 1{S_T > K}; otherwise 0

BTC DOWN payoff = 1{S_T < K}; otherwise 0 | K = venue-defined threshold or reference price
S_t = current settlement-source BTC price
S_T = final settlement-source BTC price
P_exec = executable contract price |
| Stored Field | Why It Matters |
| --- | --- |
| settlement_source | Identifies the price feed or venue rule that resolves the contract. |
| threshold_price | Stores K exactly as the venue defines it. |
| current_settlement_price | Stores S_t for every decision snapshot. |
| settlement_timestamp | Defines the exact resolution time. |
| final_settlement_price | Stores S_T for outcome labels. |
| rule_text_or_rule_id | Lets the backtest verify that the model used the same rules the market used. |
| Decision rule: If the settlement source is unknown, stale, or materially disagreeing with validated proxies, the market is blocked or assigned a larger uncertainty buffer. | Decision rule: If the settlement source is unknown, stale, or materially disagreeing with validated proxies, the market is blocked or assigned a larger uncertainty buffer. |
| Data Source | Fields Needed | Purpose |
| --- | --- | --- |
| Historical Polymarket BTC contracts | market id, side, K, expiry, bid/ask, depth if available, trades, resolution | Backtest old contracts and compare model fair value against executable market prices. |
| Historical BTC price/path data | timestamp, price, source, OHLCV or ticks, missing-data flags | Reconstruct S_t, recent realized volatility, path behavior, and as-of market state. |
| Settlement-source or proxy data | official settlement feed if available; otherwise validated proxy snapshots | Prevent the model from using a BTC price that does not match the contract rule. |
| Live BTC polling | current S_t, recent returns, volatility, source disagreement | Used later in live/read-only mode to update the current state. |
| ETF options / GEX context | IBIT/FBTC IV, skew, volume, open interest, quote age when timestamped | Prospective enhancement. Exclude from first historical backtest unless true as-of history exists. |
| Anti-overfit principle: When replaying an old contract at time t, the model may only use data timestamped at or before t. Future BTC movement, final settlement, and future Polymarket prices are labels only. | Anti-overfit principle: When replaying an old contract at time t, the model may only use data timestamped at or before t. Future BTC movement, final settlement, and future Polymarket prices are labels only. | Anti-overfit principle: When replaying an old contract at time t, the model may only use data timestamped at or before t. Future BTC movement, final settlement, and future Polymarket prices are labels only. |
| Monte Carlo p_finish calculation | Meaning |
| --- | --- |
| For each simulated path i, generate S_T^(i). | Each path starts at S_t and evolves until expiry using the as-of path distribution. |
| UP: win_i = I(S_T^(i) > K)
DOWN: win_i = I(S_T^(i) < K) | Each path is scored as a terminal win or loss. |
| p_finish_MC = (1 / N) * sum(win_i) | The estimated terminal win probability is the fraction of simulated paths that finish in the money. |
| Monte Carlo p_no_touch calculation | Meaning |
| --- | --- |
| For each simulated path i, observe the full path S_u^(i) from t to T. | The calculation uses the entire simulated path, not only the final point. |
| UP: survive_i = I(min(S_u^(i)) > K)
DOWN: survive_i = I(max(S_u^(i)) < K) | A path survives only if it never crosses the danger line before expiry. |
| p_no_touch_MC = (1 / N) * sum(survive_i) | The estimated path-survival probability is the fraction of simulated paths that avoid touching the danger line. |
| z_path calculation | Meaning |
| --- | --- |
| d_UP = ln(S_t / K)
d_DOWN = ln(K / S_t) | d_side is the favorable log distance for the contract side. |
| z_path = d_side / sigma_tau | z_path is the current cushion relative to expected remaining movement. |
| Speed layer | Role |
| --- | --- |
| Fast per-tick update | Update S_t, seconds_left, x_side, z_path, spread, and source-quality flags. This is cheap and should run whenever live data updates. |
| Cached Monte Carlo grid | Maintain simulated probabilities by bucket: side, seconds_left bucket, z_path bucket, volatility regime, volatility trend, and wick regime. |
| Lookup / interpolation | Most live decisions use the cached grid and interpolate between nearby buckets instead of launching a fresh simulation. |
| Conditional full refresh | Run fresh Monte Carlo when a new contract appears, volatility regime changes, price moves to a new bucket, time bucket changes, cache becomes stale, or an entry decision is close. |
| Baseline formula | Use |
| --- | --- |
| p_finish_formula = Phi((x_side + mu_tau) / sigma_tau) | Fast terminal-probability sanity check under a normal/lognormal assumption. |
| p_no_touch_formula = max(0, 2 * Phi(z_path) - 1) | Rough driftless no-touch baseline; useful for intuition, not as the main estimator. |
| Compare MC vs formula | Large disagreement should be logged and explained by regime, jumps, path shape, or data-quality flags. |
| Output | How It Is Used |
| --- | --- |
| p_finish_MC | Primary terminal win-probability estimate. |
| p_no_touch_MC | Primary path-survival estimate and instability warning. |
| z_path | Normalized cushion used for bucketing, cache lookup, interpolation, and gates. |
| mc_uncertainty | Confidence interval or reliability score from simulation count, bucket quality, and data quality. |
| raw_fair_value = p_finish_decision * $1.00 | Pre-cost fair value of a one-dollar binary payoff. |
| edge_before_costs = raw_fair_value - P_exec | First edge check before fees, slippage, latency, and model uncertainty. |
| Input | Use |
| --- | --- |
| settlement_source_id | Identifies the official venue price feed or proxy. |
| S_t | Current settlement-source BTC price at decision time. |
| source_timestamp | Confirms the price is fresh enough to use. |
| source_dislocation | Difference between the settlement proxy and other major BTC feeds. |
| source_quality_flag | Blocks or widens uncertainty when the price source is stale, missing, or inconsistent. |
| Volatility input | Reason |
| --- | --- |
| r_i = ln(S_i / S_{i-1}) | High-frequency log returns from the settlement-source proxy. |
| short-window realized vol | Captures immediate movement and current noise. |
| medium-window realized vol | Stabilizes the estimate so one tick does not dominate. |
| longer intraday realized vol | Provides regime context for whether the current window is unusually calm or active. |
| recent wick frequency | Flags whether price has recently made sharp adverse moves. |
| First-pass sigma_tau construction | Meaning |
| --- | --- |
| sigma_tau,short = vol_per_second_short * sqrt(tau) | Immediate movement scale projected to the remaining horizon. |
| sigma_tau,medium = vol_per_second_medium * sqrt(tau) | More stable movement estimate for the same horizon. |
| sigma_tau = weighted_blend(short, medium, longer) * regime_multiplier | First version combines windows and adjusts for expanding or falling volatility. |
| Prediction-market input | Use |
| --- | --- |
| best_bid and best_ask | Defines executable entry and exit assumptions. |
| spread | Blocks contracts where edge disappears after crossing the spread. |
| available_depth | Confirms enough size exists at the quoted price. |
| quote_age | Blocks stale order-book states. |
| market_price_path | Used for later backtest scoring, but never as future information. |
| Deferred input | Reason to defer |
| --- | --- |
| BTC ETF options / GEX context | Potentially useful for volatility and risk appetite, but historical replay needs timestamped option-chain snapshots. |
| XGBoost features | Requires clean labels, no leakage, and probability calibration before it can influence decisions. |
| HMM regime labels | Useful later for calm/trend/chop/event regimes, but not needed for the first transparent calculator. |
| Deep learning models | Too data-hungry and opaque for the first validation pass. |
| Method Choice | Decision |
| --- | --- |
| Path model | Empirical resampling from historical BTC path fragments, with later stress overlays if needed. |
| Time step | Use 1-second steps when reliable data exists; use 5-second fallback only when 1-second data is unavailable. |
| Path count | Use 5,000-10,000 paths when near a trade; use cached lookup otherwise. |
| Shock scaling | Scale sampled path fragments to the current sigma_tau and volatility regime without using future data. |
| Output | p_finish_MC, p_no_touch_MC, z_path, mc_uncertainty, and optional path diagnostics. |
| Trigger | Action |
| --- | --- |
| New contract appears | Initialize or refresh the relevant side/horizon grid. |
| seconds_left bucket changes | Move to the nearest cached bucket or refresh if the bucket is missing. |
| z_path bucket changes | Interpolate between nearby buckets or refresh near the trade boundary. |
| volatility regime changes | Refresh because the path distribution has changed. |
| near-entry state | Run a fresh or higher-path-count estimate before allowing a real decision. |
| cache stale | Refresh when cached probabilities are older than the configured freshness limit. |
| Layer | Role |
| --- | --- |
| Slow research path | Monte Carlo grid construction, calibration, XGBoost training, backtests, reports, and method evaluation. |
| Fast live path | Low-latency state updates, cached probability lookup, interpolation, edge checks, gate checks, and async logging. |
| C++ hot loop | state -> z_path -> cached probability lookup -> decision. This is the part that may need C++ if Python becomes too slow. |
| Python research layer | data collection, Monte Carlo grid building, XGBoost, reports, backtests, and operator UI. |
| Source | Use in This Project |
| --- | --- |
| Polymarket Market WebSocket | Contract order book, price changes, trades, bid/ask, depth, and quote updates. |
| Polymarket RTDS crypto prices | Venue-provided real-time crypto price stream; useful for settlement-source proxy checks. |
| Binance / Coinbase / Kraken WebSockets | Independent BTC spot feeds used to validate price quality and detect exchange-feed disagreement. |
| Freqtrade dry-run | Reference design for live simulated trading, not a direct solution for Polymarket binary contracts. |
| Hummingbot paper trade | Reference design for market-making and paper trading behavior, not the core binary pricing engine. |
| Field Group | Fields |
| --- | --- |
| Contract state | timestamp, contract_id, venue, side, K, expiry, seconds_left, rules, settlement_source_id |
| Market quote | best_bid, best_ask, spread, available_depth, quote_age, executable_price |
| BTC state | S_t, BTC spot feeds, z_path, realized-vol windows, vol_trend, vol_regime, source_quality_flag |
| Monte Carlo outputs | p_finish_MC, p_no_touch_MC, mc_uncertainty, cache_bucket_id, path_count, refresh_reason |
| Decision state | decision, edge_before_costs, support/resistance flag, block_reason, demand_more_edge_reason |
| Label | Meaning |
| --- | --- |
| final_settlement_price | The settlement-source price used to resolve the contract or the closest validated proxy. |
| finish_win | Whether the contract actually resolved on the winning side of K. |
| danger_line_touch | Whether BTC crossed the danger line after the replay decision and before expiry. |
| profitable_after_costs | Whether the candidate trade would have positive realized value after spread, fees, slippage, and fill assumptions. |
| false_positive | The model showed attractive edge, but the realized trade failed after costs or path risk. |
| missed_winner | The model blocked or ignored a setup that would have been profitable; useful for studying over-filtering. |
| Feature Group | Examples |
| --- | --- |
| Monte Carlo outputs | p_finish_MC, p_no_touch_MC, mc_uncertainty, path_count, cache_bucket_id |
| Distance/time state | z_path, seconds_left, side, horizon, threshold distance |
| Volatility state | realized-vol windows, vol_trend, vol_regime, recent wick frequency |
| Market structure | support/resistance distance, congestion flag, recent danger-line crossings |
| Execution state | spread, depth, quote_age, executable_price, source_quality_flag |
| Data quality | data_granularity, feed disagreement, stale quote flag, missing-depth flag |
| Target | Use |
| --- | --- |
| false_positive_risk | Warns that a Monte Carlo edge historically failed in similar states. |
| profitable_after_costs_probability | Estimates whether the candidate survives execution costs, not just terminal direction. |
| calibration_adjustment | Adjusts or caps p_finish_MC when historical calibration shows overconfidence. |
| Backtest Rule | Decision |
| --- | --- |
| Valid trade price | Executable bid/ask with depth and quote-age checks; no midpoint-only edge. |
| Replay frequency | Evaluate at configured intervals or quote changes while respecting timestamp availability. |
| Core comparison | Monte Carlo only versus Monte Carlo plus structure filters versus Monte Carlo plus XGBoost blocker. |
| Split method | Chronological walk-forward validation with final untouched holdout. |
| Costs | Include spread crossing, fees, slippage assumptions, latency buffer, and model uncertainty buffer. |
| GEX Component | Reuse Plan |
| --- | --- |
| Schwab authentication and option-chain collection | Reuse the access pattern prospectively for IBIT first, then FBTC if useful. |
| Option-chain normalization | Adapt into an OptionsContextFrame with expiry, strike, right, bid, ask, mid, IV, Greeks, volume, open interest, and quote age. |
| Contract selection logic | Reuse bounded option-contract selection so the system watches relevant near-ATM/front-expiry ETF options instead of everything. |
| Parquet and DuckDB research tier | Reuse the storage pattern for durable feature history and backtests. |
| Decision | Meaning |
| --- | --- |
| Trade | p_finish_MC edge survives costs, p_no_touch_MC is strong, z_path cushion is sufficient, execution checks pass, and XGBoost false-positive risk is acceptable. |
| Wait | Setup is directionally interesting but time bucket, volatility, or p_no_touch_MC is not stable enough yet. |
| Block | Data quality, stale quote, thin depth, support/resistance danger zone, or XGBoost false-positive risk fails hard. |
| Demand more edge | Trade may be allowed only at a better price because uncertainty, spread, or path risk is elevated. |
| Metric | Purpose |
| --- | --- |
| EV after costs | Primary proof of edge after spread, fees, slippage, latency, and buffers. |
| Brier score and log loss | Measures probability quality for p_finish_MC and XGBoost calibrated probabilities. |
| Calibration curve | Checks whether predicted probabilities match realized frequencies. |
| p_no_touch calibration | Checks whether predicted path survival matches actual danger-line touches. |
| Drawdown and daily loss | Measures whether the strategy survives clusters of bad outcomes. |
| Trade count by bucket | Prevents fake edge from tiny samples. |
| Ablation results | Compares core MC, MC plus structure, MC plus XGBoost, live shadow data, and later MC plus ETF/GEX. |