<!-- converted from BTC_Binary_Path_Probability_Incomplete_Research_Paper.before_section4_5_calc_rewrite_20260530.docx -->

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
# 4. Core Probability Outputs
Section 4 defines what the core engine must output. It does not yet decide the full estimation method. That separation matters: the reader should understand the quantities first, then later evaluate how the system estimates them without future leakage.
The core outputs are p_finish, p_no_touch, and z_path. Other diagnostics such as max adverse excursion, final-window wick risk, or model uncertainty may be useful later, but they are not required to define the core probability engine.
## 4.1 Shared Variables
The formulas use the following variables:
- K = venue-defined threshold or reference price.
- S_t = current settlement-source BTC price at decision time t.
- S_T = final settlement-source BTC price at expiry T.
- tau = seconds remaining until expiry.
- side = UP or DOWN contract direction.
- S_u = BTC settlement-source path between current time t and expiry T.
- sigma_tau = expected remaining BTC movement over tau.
- P_exec = executable contract price, usually ask for entry and bid for exit.
The important point is that S_t, S_u, and S_T should refer to the settlement-source price or the closest validated proxy, not a random BTC chart price.
## 4.2 Terminal Win Probability: p_finish
p_finish is the probability that the contract finishes on the winning side of the threshold at expiry. It is the terminal probability, not the full trade decision.
If p_finish = 0.84, the raw fair value of a one-dollar binary payoff is approximately $0.84 before spread, fees, slippage, latency, model uncertainty, and fill risk.
p_finish does not tell us whether the path before expiry is stable, whether the market is liquid enough to enter, or whether price is too close to support/resistance. Those questions are handled by p_no_touch, z_path, and the later decision gates.
## 4.3 Path Survival Probability: p_no_touch
p_no_touch is the probability that BTC does not cross back through the danger line before expiry. This is the path-risk quantity that makes the project different from a simple terminal prediction model.
Interpretation: high p_finish plus high p_no_touch is a cleaner setup. High p_finish plus low p_no_touch is unstable; the decision layer should wait, block, or demand more edge before entry.
## 4.4 Distance Normalization: z_path
z_path measures how far the current BTC price is from the threshold after adjusting for expected remaining movement. It is better than raw dollar distance because an $80 cushion means different things at different BTC price levels and in different volatility regimes.
Raw dollar distance is intuitive but weak. Percentage distance is better. Log distance is cleaner for price-path math because BTC movement is proportional and log returns handle compounding cleanly. z_path then scales that log distance by expected remaining movement.
Interpretation: z_path near 0 means price is close to the danger line. z_path around 1 means the current cushion is about one expected remaining move. z_path around 2 means the current cushion is about two expected remaining moves.
z_path is not the final probability. It is a standardized distance measure used by the probability engine, the path-risk model, empirical buckets, and the decision gates.
## 4.5 Core Outputs Passed Forward
The core probability engine passes three outputs forward:
- p_finish = probability the contract finishes on the winning side of K.
- p_no_touch = probability the path does not cross back through the danger line before expiry.
- z_path = normalized cushion from the danger line relative to expected remaining movement.
Later sections explain how these outputs are estimated, how historical replay avoids overfit, and how the decision layer turns them into trade, no-trade, or blocked decisions.
# 5. Estimation Inputs: Volatility and Path Distribution
After the outputs are defined, the next question is how the engine estimates the remaining BTC path distribution. The main input is not a Black-Scholes implied-volatility surface. It is an as-of realized-volatility and path-risk estimate built from BTC data available at the decision time.
Historical BTC data is not used as a magic look-ahead table. In historical replay, it reconstructs what the engine would have known at each old timestamp. In live mode, live BTC polling updates the current state.
- sigma_tau estimates expected remaining movement over tau.
- Recent realized volatility identifies the current movement scale.
- Volatility trend indicates whether movement is expanding, falling, or flat.
- Historical path behavior provides empirical shocks and path shapes for later simulation.
- Support/resistance distance may be used later as a blocker rather than as the core probability output.
The detailed method for turning these inputs into p_finish and p_no_touch is intentionally placed after Section 4 so the paper first defines the target quantities.
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
| p_finish definition | Variable Key |
| --- | --- |
| UP finish event: S_T > K
DOWN finish event: S_T < K
p_finish = P(contract finishes on winning side of K) | S_T = final settlement-source BTC price
K = contract threshold
UP wins above K
DOWN wins below K |
| p_no_touch definition | Variable Key |
| --- | --- |
| p_no_touch_UP = P(min(S_u for t <= u <= T) > K)
p_no_touch_DOWN = P(max(S_u for t <= u <= T) < K) | S_u = BTC settlement-source path from t to T
K = danger line / threshold
UP survives if the path stays above K
DOWN survives if the path stays below K |
| z_path definition | Variable Key |
| --- | --- |
| d_UP = ln(S_t / K)
d_DOWN = ln(K / S_t)
z_path = d_side / sigma_tau | S_t = current settlement-source BTC price
K = contract threshold
sigma_tau = expected remaining movement over tau
d_side = favorable log distance for the contract side |
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