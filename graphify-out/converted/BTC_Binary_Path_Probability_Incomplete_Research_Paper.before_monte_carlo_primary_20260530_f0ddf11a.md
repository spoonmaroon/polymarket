<!-- converted from BTC_Binary_Path_Probability_Incomplete_Research_Paper.before_monte_carlo_primary_20260530.docx -->

Polymarket Idea: A Remaining-Path Probability Framework for Short-Dated BTC Binary Markets
Incomplete research draft for external review
Prepared for discussion | 2026-05-29
# Abstract
This draft proposes a research system for pricing short-dated BTC binary prediction-market contracts. The central claim is that the tradable object is not BTC direction itself, but the remaining path risk of a specific binary payoff. The system separates three things: the core probability outputs, the as-of methodology used to estimate them, and the decision gates that decide whether a quoted contract is tradable. The first implementation is limited to BTC 5-minute and 15-minute binaries in read-only and paper-trading mode.
# Introduction
Short-dated BTC binary markets look simple because the payoff is only one or zero, but the pricing problem is not simply whether BTC is bullish or bearish. Each contract is tied to a specific threshold, a short expiry window, an executable market price, and a venue-defined settlement source. A useful model must therefore price the exact remaining state of the contract.
The paper now follows a stricter order. First it defines the instrument and the data. Then Section 4 defines the core outputs only: p_finish, p_no_touch, and z_path. Later sections explain how those outputs are estimated, how historical replay avoids future leakage, how ETF options context may be added later, and how the outputs become decisions.
This draft is intentionally incomplete. Its goal is to make the methodology explicit enough for criticism before more engineering or live-capital decisions.
# 1. Research Question and Scope
The research question is whether a remaining-path probability engine can identify BTC 5-minute and 15-minute binary contracts whose executable price is mispriced after spread, fees, slippage, latency, model uncertainty, and fill risk.
# 2. Instrument Definition and Settlement Source
The venue defines the binary contract. The model does not choose the threshold K. It decides whether the current executable price is attractive for that venue-defined contract.
The settlement source is the official price feed or rule the venue uses to decide whether the binary pays out. It is the scoreboard for the contract. The model should not treat generic BTC spot, a chart price, or a random exchange last trade as truth unless the market rules name that source.
# 3. Data Required Before Modeling
The model needs two historical datasets with different jobs. Historical BTC data reconstructs what the engine would have seen at each timestamp. Historical Polymarket BTC contract data provides the contract object, executable price, and eventual label. These roles should not be mixed.
# 4. Core Model Outputs and First-Pass Calculations
Section 4 defines the quantities the engine calculates and gives the first-pass formulas used to sanity-check the system. These formulas are deliberately transparent. They are not the final proof of edge; they are the baseline that later empirical replay and Monte Carlo must beat or calibrate.
The sequence is important: first define the output, then show the first-pass calculation, then pass the output to the decision layer. This keeps the paper from mixing the calculator, the data inputs, and the trading rules into one tangled section.
## 4.1 Shared Variables
- K = venue-defined threshold or reference price.
- S_t = current settlement-source BTC price at decision time t.
- S_T = final settlement-source BTC price at expiry T.
- tau = seconds remaining until expiry.
- side = UP or DOWN contract direction.
- S_u = BTC settlement-source path between current time t and expiry T.
- sigma_tau = expected remaining BTC log-price movement over tau.
- mu_tau = expected side-adjusted drift over tau; this is zero in the first version unless validated.
- P_exec = executable contract price, usually ask for entry and bid for exit.
- Phi(.) = standard normal cumulative distribution function.
Important: S_t, S_u, and S_T should refer to the settlement-source price or the closest validated proxy, not a random BTC chart price. If the venue settlement source is unclear, stale, or materially different from the proxy feed, the model should block or demand a larger uncertainty buffer.
## 4.2 p_finish: What It Means
p_finish is the terminal win probability. It asks only whether the contract finishes on the winning side of K at expiry. For an UP contract, the finish event is S_T > K. For a DOWN contract, the finish event is S_T < K.
p_finish is useful because a one-dollar binary payoff has a raw fair value close to its win probability. If p_finish is 0.84, the raw pre-cost fair value is about $0.84. But p_finish alone is not a trade decision because it ignores path instability, liquidity, support/resistance risk, and execution costs.
## 4.3 First-Pass p_finish Calculation
The first p_finish calculator treats the remaining BTC log return as approximately normal over the remaining time window. This is not the final model. It is the transparent baseline because it is fast, explainable, and easy to falsify in backtests.

Why this is only first-pass: 5-minute and 15-minute BTC returns are not perfectly normal. They can have jumps, wicks, volatility clustering, exchange dislocations, and final-window instability. Later sections therefore test empirical and Monte Carlo estimates against this baseline.
## 4.4 p_no_touch: What It Means
p_no_touch is the path survival probability. It asks whether BTC avoids crossing back through the danger line before expiry. This is the part that makes the project different from a simple terminal prediction model.
A contract can have high p_finish but low p_no_touch. That means the final settlement may still be favorable, but the path is unstable enough that entering is dangerous. The decision layer should wait, block, or demand more edge in that case.

## 4.5 First-Pass p_no_touch Calculation
The first p_no_touch calculator uses the same side-adjusted distance and volatility scale as p_finish, but it asks a harder question: not just whether the final point wins, but whether the path survives without crossing the danger line.

This baseline will usually be more conservative than p_finish. Example: if z_path = 1 and drift is zero, p_finish is about 84%, while p_no_touch is about 68%. That difference is exactly the path-risk warning the strategy cares about.
The real system should later estimate p_no_touch with replayed paths or Monte Carlo because continuous Brownian assumptions miss discrete ticks, jumps, final-window wicks, and exchange-feed gaps.
## 4.6 z_path: Distance Normalization
z_path measures how far the current BTC price is from the threshold after scaling by expected remaining movement. Raw dollar distance is intuitive but weak because an $80 cushion means different things at different BTC price levels and in different volatility regimes.

Log distance is cleaner than raw dollars because BTC movement is naturally proportional and log returns handle compounding. z_path then scales that distance by expected remaining movement.
- z_path near 0 = price is close to the danger line.
- z_path around 1 = current cushion is about one expected remaining move.
- z_path around 2 = current cushion is about two expected remaining moves.
## 4.7 Core Outputs Passed to the Decision Layer
The calculator should pass calibrated probabilities and diagnostic distances forward. The decision layer can then decide whether the edge is tradable after costs and risk gates.

Later sections explain how p_finish_decision is calibrated, how p_no_touch is improved with empirical path behavior, and how the decision layer turns these outputs into trade, no-trade, or wait decisions.
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
This path library is what later lets empirical Monte Carlo improve on the clean formulas from Section 4. Instead of assuming every remaining path is smooth and normal, the engine can sample path fragments from similar as-of states.
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
# 6. As-Of Walk-Forward Methodology
The proposed estimation method is As-Of Walk-Forward Empirical Monte Carlo. The phrase “as-of” is the important part. At every historical replay timestamp t, the engine behaves as if it is living at that moment and cannot see the future.
1.  Load the historical Polymarket contract state as of t: K, side, expiry, bid/ask, and available depth.
2.  Load BTC market data timestamped at or before t: S_t, recent returns, realized volatility, and source-quality flags.
3.  Estimate the current path distribution using only data available at or before t.
4.  Simulate or sample possible remaining paths from S_t to expiry.
5.  Compute p_finish, p_no_touch, and z_path.
6.  Compare the output to the executable Polymarket price and log the decision.
7.  After expiry, use the final outcome only for scoring and calibration, not as an input.
# 7. Historical Polymarket Backtest Design
Historical Polymarket contracts are the test objects. BTC history reconstructs the market state around those contracts. The backtest should evaluate whether the engine would have identified mispriced executable contracts without knowing the future.
The backtest should use chronological splits, not random train/test splits. Tuning on the final holdout period is not allowed. If the methodology changes after reviewing holdout failures, a new later holdout is needed.
# 8. ETF Options and GEX Context
BTC ETF options data may contain useful information about volatility, skew, and risk appetite, but it should not be required for the first historical backtest unless true timestamped historical option-chain snapshots are available.
If historical ETF chain data is missing, the first clean backtest should exclude ETF options features. ETF/GEX context should then be collected prospectively and tested later as an ablation: core engine versus core engine plus ETF options context.
# 9. Decision Gates and Execution Logic
The probability engine produces p_finish, p_no_touch, and z_path. The decision layer decides whether those outputs are good enough to trade at the executable market price.
The venue defines K. The model chooses decision cutoffs. Those cutoffs should be learned by bucket: horizon, side, seconds left, volatility regime, support/resistance state, and spread bucket.
- p_finish must be high enough relative to executable price.
- p_no_touch must be high enough to avoid unstable entries.
- z_path must show enough cushion from the danger line.
- Spread, depth, quote age, and source agreement must pass.
- Support/resistance blockers must pass.
- Portfolio and daily-loss rules must pass before live money is considered.
# 10. XGBoost Calibrated Probability
XGBoost is a later challenger, not the first authority. It can learn nonlinear combinations of features, but it needs clean labels, strict leakage control, and probability calibration before its output can influence decisions.
A reasonable first promotion is not direct trading authority. XGBoost should first act as a false-positive blocker only if it improves out-of-sample expected value without deleting most opportunities.
# 11. Validation and Falsification
The backtest must avoid midpoint illusion. A signal only counts if it could be executed at the observed bid/ask with enough depth and after realistic cost assumptions.
- Compare p_finish calibration against actual settlement outcomes.
- Compare p_no_touch calibration against whether price crossed the danger line.
- Measure Brier score and log loss against executable venue prices.
- Run ablations: core only, core plus structure, core plus ETF context, core plus XGBoost.
- Separate results by 5m versus 15m, seconds-to-expiry bucket, spread bucket, volatility regime, and support/resistance state.
- Stop or redesign if edge exists only against midpoint, disappears after costs, or fails out of sample.
# 12. Open Questions and Next Work
- Which settlement-source proxy is closest to each venue rule when the official tick is not directly streamed?
- How much historical Polymarket BTC contract data can be reconstructed with true bid/ask timing?
- How much BTC tick or 1-second historical data is available with reliable timestamps?
- Does empirical Monte Carlo beat the lognormal sanity-check baseline out of sample?
- When should ETF options context be tested prospectively?
- Which decision gates are redundant or overfit?
# 13. References and Data Documentation
- Polymarket WebSocket overview: https://docs.polymarket.com/market-data/websocket/overview
- Polymarket order book docs: https://docs.polymarket.com/trading/orderbook
- Polymarket RTDS: https://docs.polymarket.com/market-data/websocket/rtds
- Binance Spot WebSocket streams: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams
- Coinbase Advanced Trade WebSocket: https://help.coinbase.com/en/developer-platform/websocket-feeds/advanced-trade
- Kraken WebSocket v2 ticker: https://docs.kraken.com/api/docs/websocket-v2/ticker/
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
| Decision rule: If the settlement source is unknown, stale, or materially disagreeing with validated proxies, the market is blocked or assigned a larger uncertainty buffer. |
| --- |
| Data Source | Fields Needed | Purpose |
| --- | --- | --- |
| Historical Polymarket BTC contracts | market id, side, K, expiry, bid/ask, depth if available, trades, resolution | Backtest old contracts and compare model fair value against executable market prices. |
| Historical BTC price/path data | timestamp, price, source, OHLCV or ticks, missing-data flags | Reconstruct S_t, recent realized volatility, path behavior, and as-of market state. |
| Settlement-source or proxy data | official settlement feed if available; otherwise validated proxy snapshots | Prevent the model from using a BTC price that does not match the contract rule. |
| Live BTC polling | current S_t, recent returns, volatility, source disagreement | Used later in live/read-only mode to update the current state. |
| ETF options / GEX context | IBIT/FBTC IV, skew, volume, open interest, quote age when timestamped | Prospective enhancement. Exclude from first historical backtest unless true as-of history exists. |
| Anti-overfit principle: When replaying an old contract at time t, the model may only use data timestamped at or before t. Future BTC movement, final settlement, and future Polymarket prices are labels only. |
| --- |
| First-pass p_finish formula | Variable Key |
| --- | --- |
| x_UP = ln(S_t / K)
x_DOWN = ln(K / S_t)
x_side = favorable log distance for the contract side | x_side is positive when current price is on the favorable side of the threshold. |
| p_finish = Phi((x_side + mu_tau) / sigma_tau) | sigma_tau is expected remaining log-price movement. mu_tau is the side-adjusted expected drift over the remaining window. |
| If mu_tau = 0, then p_finish = Phi(z_path) | The first version should usually set mu_tau to zero unless a drift estimate survives out-of-sample testing. |
| Path survival event | Meaning |
| --- | --- |
| UP no-touch event = min(S_u for t <= u <= T) > K | The path must stay above K until expiry. |
| DOWN no-touch event = max(S_u for t <= u <= T) < K | The path must stay below K until expiry. |
| First-pass p_no_touch formula | Variable Key |
| --- | --- |
| z_path = x_side / sigma_tau | z_path measures the current cushion relative to expected remaining movement. |
| p_no_touch = max(0, 2 * Phi(z_path) - 1) | Driftless continuous-path baseline from the reflection-principle intuition. It is valid only when current price is already on the favorable side. |
| If z_path <= 0, p_no_touch = 0 for entry purposes | The contract has already touched or is on the wrong side of the danger line, so this no-touch entry setup is not active. |
| z_path formula | Variable Key |
| --- | --- |
| d_UP = ln(S_t / K)
d_DOWN = ln(K / S_t) | d_side is the favorable log distance for the contract side. |
| z_path = d_side / sigma_tau | sigma_tau is the expected remaining log-price movement over tau. |
| Output | How It Is Used |
| --- | --- |
| p_finish_formula | Fast terminal win-probability baseline. |
| p_no_touch_formula | Fast path-survival baseline and instability warning. |
| z_path | Normalized cushion from the danger line; used by probability estimates, buckets, and cutoffs. |
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
| No future leakage: The model cannot use future BTC candles, final settlement, future Polymarket prices, future contract outcome, future volatility, or end-of-day summaries that were not available at replay time t. |
| --- |
| Backtest Item | Purpose |
| --- | --- |
| Contract metadata | Reconstruct K, side, expiry, market rules, and settlement source. |
| Market price snapshots | Compare p_finish-derived fair value against bid/ask that could actually be executed. |
| BTC as-of path data | Compute S_t, recent volatility, z_path, and path-risk inputs at each replay timestamp. |
| Final settlement | Label whether p_finish was correct. Used only after the simulated decision. |
| Barrier touch label | Label whether p_no_touch was correct. Used only after the simulated decision. |
| GEX Component | Reuse Plan |
| --- | --- |
| Schwab authentication and option-chain collection | Reuse the access pattern prospectively for IBIT first, then FBTC if useful. |
| Option-chain normalization | Adapt into an OptionsContextFrame with expiry, strike, right, bid, ask, mid, IV, Greeks, volume, open interest, and quote age. |
| Contract selection logic | Reuse bounded option-contract selection so the system watches relevant near-ATM/front-expiry ETF options instead of everything. |
| Parquet and DuckDB research tier | Reuse the storage pattern for durable feature history and backtests. |
| As-of rule: In live mode, current option-chain data is allowed. In historical replay, option-chain data is allowed only if timestamped at or before the replay decision time. |
| --- |
| Decision equation | Variable Key |
| --- | --- |
| edge_after_costs = p_finish_decision - P_exec - fees - b_slippage - b_latency - b_model
trade_allowed = 1{all calibrated gates pass} | P_exec = executable venue price
b_slippage = slippage buffer
b_latency = latency buffer
b_model = model uncertainty buffer
p_finish_decision = calibrated terminal probability used for fair value |
| Need | Requirement |
| --- | --- |
| Clean labels | finish_win, danger_line_touch, profitable_after_costs, false_positive, missed_winner. |
| No leakage | Features must be available at decision time only. No final candle, final settlement, future market price, or post-trade outcome data. |
| Time-based validation | Walk-forward splits by date/time. Random splits are dangerous for market data. |
| Calibration | Predicted 70% events should happen about 70% of the time. |
| Ablation tests | Compare core engine only, core plus structure filter, core plus ETF context, and core plus XGBoost. |