<!-- converted from BTC_Binary_Path_Probability_Incomplete_Research_Paper.before_contract_state_visual_20260530.docx -->

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
# 5. Market-State Inputs and State Construction
Section 5 defines the inputs and state construction rules used before the probability engine runs. The goal is to transform raw BTC data, venue market data, and optional ETF options context into a clean as-of state. Every input must be timestamped at or before decision time t. Future candles, future settlement, later Polymarket prices, and end-of-day summaries are labels for evaluation, not inputs for the decision.
## 5.1 Settlement-Source Price Inputs
The settlement-source price is the price series the venue uses to resolve the contract, or the closest validated proxy if the official source is not streamed. This source defines S_t, S_u, and S_T. If the proxy is wrong, every probability can look precise while being pointed at the wrong target.

## 5.2 Live BTC and Tick Inputs
The live BTC layer supplies the short-horizon path information that OHLC candles cannot preserve. Tick or 1-second data is preferred because p_no_touch depends on whether BTC crossed the danger line before expiry, not only where the candle closed.

## 5.3 Realized Volatility and sigma_tau Inputs
Realized volatility is built from BTC log returns available before the decision time. The first version should use multiple short windows instead of one magic lookback because 5-minute binaries are sensitive to sudden volatility changes.

sigma_tau is the expected remaining log-price movement over the seconds left until expiry. It is the main scale input for p_finish, p_no_touch, and z_path.

## 5.4 Support/Resistance, Liquidity, and Order-Book Inputs
Support/resistance should not be treated as a mystical prediction. For this system, it is a structure blocker or extra-edge requirement. A contract near a heavily traded level can show attractive p_finish while still being vulnerable to chop around the threshold.
- Distance from S_t to nearest local highs/lows.
- Distance from S_t to the threshold K.
- Whether K sits inside a recent congestion zone.
- Whether recent price has repeatedly crossed the same level.

## 5.5 ETF Options Context Inputs
ETF options data will not be used as a primary predictor in the first version. Instead, it will be used as an as-of volatility, skew, and risk-appetite context layer. Its purpose is to adjust sigma_tau, p_no_touch, model uncertainty, and required edge. The value of this layer will be tested through ablation: core BTC engine versus core BTC engine plus ETF options context.

## 5.6 Data-Quality and Noise Controls
Noise handling belongs in state construction before it becomes a Monte Carlo problem. The paper should not finalize the full noise method until the microstructure-noise research pass is reviewed. The first design rule is simpler: filter bad data noise, preserve real path risk, and price execution noise as a buffer.

# 6. Monte Carlo Methodology
The proposed estimation method is As-Of Walk-Forward Empirical Monte Carlo with cached live probability grids. The phrase as-of is the important part: at every historical replay or live-shadow timestamp t, the engine behaves as if it is living at that moment and cannot see the future.
The default design is empirical resampling from historical BTC paths. This is preferred over a purely normal model because short-dated BTC contracts are sensitive to wicks, jumps, volatility clustering, exchange-feed gaps, and final-window instability.
## 6.1 Path Generation Defaults

## 6.2 Monte Carlo Conditioning Variables
Monte Carlo does not use features in the same way that XGBoost does. It uses conditioning variables to select, scale, and simulate comparable remaining BTC paths. These variables define the state that the simulated paths should resemble.

Core Monte Carlo conditioning should start with side, seconds_left, z_path, sigma_tau, volatility regime, volatility trend, recent wick frequency, source quality, and data granularity. Execution variables such as spread, depth, and quote age should usually remain decision gates rather than path-generation variables. ETF options variables should remain optional context until they pass ablation testing.
## 6.3 Practical Noise Handling Inside Monte Carlo
The first implementation should use a practical noise framework before adopting heavier high-frequency estimators. The reason is operational: short-dated BTC binaries need a robust live decision path, and the system must first prove that simple filters, volatility floors, and explicit buffers reduce false precision. Realized kernels, pre-averaging, and two-scale estimators should be kept as research challengers and ablation tests, not as the initial live dependency.
The guiding rule is: filter bad data noise, preserve real path risk, and price execution noise as a buffer. A bad tick should not move the model, but a real wick through the threshold should remain in the path library because that is exactly the risk p_no_touch is trying to measure.

The first decision rule should be conservative:
required_edge = base_edge + spread_buffer + latency_buffer + noise_buffer + mc_uncertainty_buffer
A trade is allowed only if model_edge is greater than required_edge and the data-quality gates pass. This prevents the engine from treating tiny theoretical edges as real tradable opportunities.
After enough live shadow data is collected, the practical estimator should be tested against heavier noise-robust alternatives. The paper should compare simple realized volatility plus filters against realized kernels, pre-averaging, and two-scale realized volatility. A heavier estimator should only replace the practical first version if it improves out-of-sample calibration, p_no_touch accuracy, and executable EV after costs.
## 6.4 Cached Grids and Refresh Rules
The live engine should not run a full Monte Carlo simulation on every tick. It should maintain cached probability grids and refresh them only when the state has changed enough to matter. This keeps Monte Carlo as the primary estimator without making every decision wait for a full simulation.

## 6.5 Fast Live Path and Speed Architecture
Many short-dated binary contracts converge very quickly. In those moments, speed is not a cosmetic engineering concern; it is part of the edge. The live system should therefore split into a slow research path and a fast decision path.

The live decision should be cheap: update S_t and seconds_left, compute z_path, look up p_finish_MC and p_no_touch_MC from the cached grid, compare against executable price, run gates, then log asynchronously. Full simulation is reserved for initialization, stale caches, regime changes, and close entry decisions where money is actually at risk.
The practical goal is not simply to have a smarter model. The goal is to know the fair value before the market fully adjusts, while still refusing trades where stale data, thin depth, or unstable paths make the quote untrustworthy.
## 6.6 Replay Procedure
- Load the historical prediction-market contract state as of t: K, side, expiry, bid/ask, quote age, and available depth.
- Load BTC market data timestamped at or before t: S_t, recent returns, realized volatility, volatility trend, source-quality flags, and support/resistance context.
- Build or retrieve the valid Monte Carlo probability bucket for the current state.
- Compute p_finish_MC, p_no_touch_MC, z_path, mc_uncertainty, and block reasons.
- Compare the outputs to the executable venue price and log the decision.
- After expiry, use the final outcome only for scoring and calibration, not as an input.
No future leakage: The model cannot use future BTC candles, final settlement, future Polymarket prices, future contract outcome, future volatility, or end-of-day summaries that were not available at replay time t.
# 7. Live Shadow Backtester and Data Collection
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
# 9. Backtest and Ablation Design
Historical Polymarket contracts are the test objects. BTC history reconstructs the market state around those contracts. The backtest should evaluate whether the engine would have identified mispriced executable contracts without knowing the future.


The backtest should be judged on executable expected value, not attractive midpoint pricing. A signal only counts if it could have been traded at the observed bid/ask with enough depth and fresh quotes.
# 10. ETF Options/GEX Implementation Plan
ETF options data will not be used as a primary predictor in the first version. Instead, it will be used as an as-of volatility, skew, and risk-appetite context layer. Its purpose is to adjust sigma_tau, p_no_touch, model uncertainty, and required edge. The value of this layer will be tested through ablation: core BTC engine versus core BTC engine plus ETF options context.
BTC ETF options data may contain useful information about volatility, skew, and risk appetite, but it should not be required for the first historical backtest unless true timestamped historical option-chain snapshots are available. If historical chain timing is missing, ETF/GEX context should be collected prospectively and tested after enough live observations exist.

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
- ETF options context must be fresh and liquid before it can adjust risk.
- Portfolio and daily-loss rules must pass before live money is considered.
# 12. Validation and Falsification
The research should continue only if the strategy produces positive out-of-sample expected value after executable pricing, costs, and realistic market constraints. High win rate alone is not proof.

- Stop if edge exists only against midpoint and disappears at executable bid/ask.
- Stop if performance fails on the final untouched holdout.
- Stop or redesign if XGBoost improves in-sample results but worsens out-of-sample EV or deletes most valid opportunities.
- Stop if ETF options context improves in-sample fit but fails ablation out of sample.
- Stop if the model cannot explain why Monte Carlo and closed-form baselines diverge in important buckets.
- Treat live shadow results as the main evidence once enough contracts have been recorded.
# 13. Open Questions and Next Work
- Which settlement-source proxy is closest to each venue rule when the official tick is not directly streamed?
- How much historical Polymarket BTC contract data can be reconstructed with true bid/ask timing and quote age?
- How much BTC tick or 1-second historical data is available with reliable timestamps?
- What free live data feeds are reliable enough for a continuous shadow logger?
- How quickly do short-dated BTC binary markets converge after the model detects edge?
- Which microstructure-noise method should be used first: practical filtering, realized kernels, pre-averaging, or two-scale realized volatility?
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
- Ait-Sahalia, Y., Mykland, P. A., and Zhang, L. (2005). How Often to Sample a Continuous-Time Process in the Presence of Market Microstructure Noise. Review of Financial Studies.
- Zhang, L., Mykland, P. A., and Ait-Sahalia, Y. (2005). A Tale of Two Time Scales: Determining Integrated Volatility with Noisy High-Frequency Data.
- Barndorff-Nielsen, O. E., Hansen, P. R., Lunde, A., and Shephard, N. (2008). Designing Realized Kernels to Measure the Ex Post Variation of Equity Prices in the Presence of Noise.
- Jacod, J., Li, Y., Mykland, P. A., Podolskij, M., and Vetter, M. (2009). Microstructure Noise in the Continuous Case: The Pre-Averaging Approach.
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
| Input | Use |
| --- | --- |
| BTC spot feeds | Binance, Coinbase, Kraken, Polymarket RTDS, or other validated feeds used for source agreement checks. |
| trade_or_quote_timestamp | Confirms the current price is available as of t. |
| robust_S_t | Short rolling median or robust price estimate used to avoid one bad tick defining the state. |
| feed_disagreement | Measures whether major BTC feeds disagree enough to block or demand more edge. |
| data_granularity | Records whether the state is tick, 1s, 5s, 1m OHLC, or another quality level. |
| Volatility Input | Reason |
| --- | --- |
| short-window realized vol | Captures immediate movement and current noise. |
| medium-window realized vol | Stabilizes the estimate so one tick does not dominate. |
| longer-window realized vol | Provides regime context for whether the current window is unusually calm or active. |
| recent wick frequency | Flags whether price has recently made sharp adverse moves. |
| First-pass sigma_tau | Meaning |
| --- | --- |
| sigma_tau,short = vol_per_second_short * sqrt(tau) | Immediate movement scale projected to the remaining horizon. |
| sigma_tau,medium = vol_per_second_medium * sqrt(tau) | More stable movement estimate for the same horizon. |
| sigma_tau = weighted_blend(short, medium, longer) * regime_multiplier | Final version combines windows and adjusts for expanding or falling volatility. |
| Prediction-market input | Use |
| --- | --- |
| best_bid and best_ask | Defines executable entry and exit assumptions. |
| spread | Blocks contracts where edge disappears after crossing the spread. |
| available_depth | Confirms enough size exists at the quoted price. |
| quote_age | Blocks stale order-book states. |
| market_price_path | Used for later backtest scoring, but never as future information. |
| ETF Options Input | Intended Use |
| --- | --- |
| IBIT_ATM_IV | Adds a market-implied volatility context check for BTC-linked risk. |
| IBIT_IV_change_1m / 5m / 15m | Flags sudden changes in ETF option-implied volatility. |
| put_skew / call_skew / risk_reversal | Measures downside or upside stress that may affect path survival. |
| put_call_volume_ratio / premium_ratio | Risk-appetite context; not a direct trade trigger. |
| delta_weighted_flow / vega_weighted_flow | Later flow features after enough timestamped data is collected. |
| quote_age / option_spread | Blocks stale or illiquid option context from influencing decisions. |
| Noise / Data Issue | First Treatment |
| --- | --- |
| Bad tick or feed jump | Use robust price construction and source agreement checks. |
| Bid/ask bounce | Prefer robust mid or short rolling median for state construction. |
| Low-volatility false calm | Apply a volatility floor before estimating p_no_touch. |
| High-volatility regime shift | Refresh Monte Carlo buckets and demand more edge. |
| Small path bucket | Raise mc_uncertainty or block until enough comparable paths exist. |
| Latency, slippage, and fill risk | Add explicit execution and noise buffers to required edge. |
| Method Choice | Decision |
| --- | --- |
| Path model | Empirical resampling from historical BTC path fragments, with later stress overlays if needed. |
| Time step | Use 1-second steps when reliable data exists; use 5-second fallback only when 1-second data is unavailable. |
| Path count | Use 5,000-10,000 paths when near a trade; use cached lookup otherwise. |
| Shock scaling | Scale sampled path fragments to the current sigma_tau and volatility regime without using future data. |
| Output | p_finish_MC, p_no_touch_MC, z_path, mc_uncertainty, and optional path diagnostics. |
| Variable Group | Variables | Purpose |
| --- | --- | --- |
| Contract state | side, seconds_left, horizon, threshold K | Defines the binary payoff, expiry window, and direction being priced. |
| Distance state | S_t, d_side, z_path | Measures how far the current settlement-source price is from the danger line. |
| Volatility state | sigma_tau, short/medium/long realized-vol windows, vol_regime, vol_trend | Scales the remaining path distribution and separates calm, expanding, and high-volatility states. |
| Path-shape state | recent wick frequency, recent danger-line crosses, max adverse move bucket | Helps sample paths that resemble the current short-horizon tape instead of assuming smooth movement. |
| Data-quality state | source_quality_flag, data_granularity, feed_disagreement, stale_price_flag | Prevents false precision when the settlement proxy, tick data, or venue feed is unreliable. |
| Optional later context | ETF_IV_stress, ETF_skew_stress, ETF_flow_flag | May adjust volatility, path-risk, and required edge after enough as-of ETF options data is collected. |
| Noise Layer | Problem | Practical First Treatment |
| --- | --- | --- |
| Bad tick / feed jump | A single corrupt print can distort S_t, z_path, and the simulated starting point. | Use source agreement checks, stale-feed checks, and a short robust median over the last 1-3 seconds. |
| Bid/ask bounce | Microstructure movement can look like real BTC motion when the system is too close to the threshold. | Use robust mid/median state construction and avoid using one raw tick as the only S_t input. |
| Low-volatility false calm | A quiet tape can make p_no_touch look too safe right before a sudden move. | Apply sigma_tau = max(realized_sigma_tau, volatility_floor). |
| High-volatility regime shift | Old cached paths understate risk when the tape suddenly changes. | Refresh Monte Carlo buckets, raise mc_uncertainty, and demand more edge. |
| Thin comparable-path bucket | Monte Carlo probabilities are unstable when too few similar historical/live states exist. | Expose bucket_sample_size and mc_uncertainty; block or demand more edge when confidence is weak. |
| Latency / slippage / fill uncertainty | The market may converge before the quote can be traded. | Add latency_buffer, slippage_buffer, and noise_buffer to required_edge. |
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
| ETF context | ETF_IV_stress, ETF_skew_stress, ETF_flow_flag after ablation support exists |
| Target | Use |
| --- | --- |
| false_positive_risk | Warns that a Monte Carlo edge historically failed in similar states. |
| profitable_after_costs_probability | Estimates whether the candidate survives execution costs, not just terminal direction. |
| calibration_adjustment | Adjusts or caps p_finish_MC when historical calibration shows overconfidence. |
| Backtest Rule | Decision |
| --- | --- |
| Valid trade price | Executable bid/ask with depth and quote-age checks; no midpoint-only edge. |
| Replay frequency | Evaluate at configured intervals or quote changes while respecting timestamp availability. |
| Split method | Chronological walk-forward validation with final untouched holdout. |
| Costs | Include spread crossing, fees, slippage assumptions, latency buffer, and model uncertainty buffer. |
| Ablation | Question Answered |
| --- | --- |
| Core BTC Monte Carlo only | Does the path-probability engine have standalone signal? |
| Core BTC + structure filters | Do support/resistance, liquidity, and data-quality gates improve outcomes? |
| Core BTC + XGBoost blocker | Does the shadow model reduce false positives out of sample? |
| Core BTC + ETF options context | Do ETF IV/skew/flow variables improve volatility, p_no_touch, uncertainty, or required-edge decisions? |
| Live shadow data versus historical replay | Do live as-of logs disagree with OHLC or reconstructed historical assumptions? |
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