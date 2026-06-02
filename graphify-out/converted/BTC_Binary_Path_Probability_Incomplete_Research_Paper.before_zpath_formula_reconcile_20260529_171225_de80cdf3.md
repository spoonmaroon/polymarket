<!-- converted from BTC_Binary_Path_Probability_Incomplete_Research_Paper.before_zpath_formula_reconcile_20260529_171225.docx -->

Polymarket Idea: A Remaining-Path Probability Framework for Short-Dated BTC Binary Markets
Incomplete research draft for external review
Prepared for discussion | 2026-05-29

# Abstract
This draft proposes a research system for pricing short-dated BTC binary prediction-market contracts. The central claim is that the tradable object is not BTC direction itself, but the remaining path risk of a specific binary payoff. The proposed engine estimates terminal win probability, path survival probability, executable edge, and risk blockers using venue data, settlement/reference price data, free crypto market data, and BTC ETF options context. The initial implementation is limited to BTC 5-minute and 15-minute binaries in read-only and paper-trading mode. The purpose of this draft is to make the methodology explicit enough for criticism before further engineering or any live-capital decision.
# 1. Research Question
The project asks whether a BTC binary contract can be priced more accurately than the venue's executable bid/ask at specific moments. The model does not ask whether BTC is bullish or bearish in general. It asks whether the remaining distribution of BTC paths makes a specific short-dated binary contract cheap or expensive after costs.

# 2. Instrument Definition
A BTC binary contract pays one unit if BTC settles on the winning side of a threshold at expiry and zero otherwise. This is similar to a digital option payoff. The system therefore begins with an option-like payoff definition, then adds path survival and microstructure constraints.

# 3. Data Sources and Purpose
Each data source has a specific role. The model should not collect data simply because it is available; every input must either produce a probability estimate, adjust risk, block a trade, or label outcomes for validation.

# 4. Core Model Outputs
## 4.1 Terminal Probability: p_finish
p_finish is the probability that BTC finishes on the winning side of the binary threshold at expiry. Because the payoff is binary, p_finish is also the first approximation of fair value before costs. For example, if p_finish is 0.84, the raw fair value is approximately $0.84.
## 4.2 Path Survival Probability: p_no_touch
p_no_touch is the probability that BTC does not cross back through the danger line before expiry. It captures the user's original idea: the trade is attractive only when the price is already on the correct side and the remaining path is unlikely to move against the position. A contract can have a high p_finish but low p_no_touch if the final settlement is favorable yet the path is unstable.
## 4.3 Distance Normalization: z_path
z_path is the standardized distance between the current settlement-source BTC price and the binary threshold. It measures the current cushion in units of expected remaining movement, not in raw dollars.
The plain-English question is: how far is BTC from the line compared with how much BTC can realistically still move before expiry?
Variables: S_t is the current settlement-source BTC price; K is the threshold or opening reference price; expected_remaining_move is the forecast absolute BTC move for the remaining time window, estimated from the realized-vol surface and adjusted by volatility-regime warnings when needed.
For an UP contract:
z_path_up = (S_t - K) / expected_remaining_move
For a DOWN contract:
z_path_down = (K - S_t) / expected_remaining_move
Interpretation: z_path > 0 means the contract is currently on the winning side. z_path < 0 means the contract is currently on the losing side. z_path near 0 means price is sitting near the danger line and the contract is fragile. z_path = 1 means the cushion is one expected remaining move. z_path = 2 means the cushion is two expected remaining moves.
Example: if the UP threshold is $100,000, current settlement-source BTC is $100,080, and expected_remaining_move is $40, then z_path = 80 / 40 = 2.0. The contract has a two-expected-move cushion. If expected_remaining_move is $160 instead, then z_path = 80 / 160 = 0.5. The same $80 cushion is now weak because volatility is large relative to the distance from the threshold.
Decision use: z_path is not the final probability. It is a path-safety input used by p_finish, p_no_touch, the decision tree, and sizing. Low z_path should usually mean wait or block, even if the raw contract price looks cheap. High z_path makes the trade structurally cleaner, but the system still needs p_finish, p_no_touch, order-book quality, support/resistance, and risk checks before entering.
Important: z_path must be calculated from the settlement-source price, not a generic exchange spot price. If the contract resolves on Chainlink BTC/USD, then S_t should be the Chainlink/venue settlement-source BTC price or the closest validated live proxy.
# 5. Volatility and Path Methodology
The volatility model estimates how much BTC can plausibly move before the binary expires. The first implementation should use an empirical realized-volatility surface rather than a complicated stochastic-volatility model. The surface is indexed by asset, horizon, seconds to expiry, and market regime.

## 5.1 Model Stack and Rationale
The core model treats the BTC binary as an option-like payoff, but the project is not trying to replicate a full exchange-traded option. The option framing gives the mathematical language: a digital payoff for terminal settlement and a barrier/no-touch lens for path survival. The remaining-path probability engine is the actual pricing engine.
- Digital option backbone: the contract pays one unit if the settlement-source BTC price finishes on the winning side of the threshold and zero otherwise.
- Barrier/no-touch layer: the trade is fragile when price can cross back through the threshold before expiry, even if the current mark is favorable.
- Realized-vol surface: expected movement comes first from an empirical surface built from collected BTC tick/second/minute data, indexed by asset, horizon, seconds left, and regime.
- Greeks: binary delta, gamma, vega, and theta measure sensitivity around the threshold and decide when a trade is too unstable.
- Monte Carlo: offline and shadow-live simulations validate p_finish, p_no_touch, max adverse excursion, and final-minute wick risk.
- Volatility forecast layer: GARCH and HAR-RV are challengers or modifiers to the realized-vol baseline, not standalone trading signals.
- Risk layer: VaR, portfolio exposure, and correlation control sizing and kill-switches. They do not predict direction.
- ML layer: XGBoost is the first practical challenger after enough labels exist; HMM can later label regimes; deep learning waits until the simpler models fail in a measurable way.
## 5.2 First-Pass p_finish Calculation
p_finish is the terminal win probability: the probability that the official settlement-source BTC price finishes on the correct side of the threshold at expiry. The first implementation should use a transparent analytic estimate, then compare it against Monte Carlo and realized outcomes.
Variables: S_t is the current settlement-source BTC price; K is the threshold or opening reference price; tau is seconds remaining; sigma_remaining is the expected remaining log-price standard deviation from the realized-vol surface, adjusted by the volatility forecast layer when needed.
For an UP contract, the baseline estimator is:
z_finish = ln(S_t / K) / sigma_remaining
p_finish_up = Phi(z_finish)
For a DOWN contract, the baseline estimator is:
p_finish_down = 1 - p_finish_up
At 5m/15m horizons, drift should initially be set to zero unless backtesting proves that a short-horizon drift term improves calibration after costs. The model should prefer a slightly conservative probability over a beautiful but overfit formula.
## 5.3 First-Pass p_no_touch Calculation
p_no_touch is the path survival probability: the probability that price does not cross back through the danger line before expiry. This is the user-originated idea that makes the system more than a simple terminal prediction model.
For an UP contract already above the threshold:
p_no_touch_up = P(min path price before expiry > K)
For a DOWN contract already below the threshold:
p_no_touch_down = P(max path price before expiry < K)
The first production-friendly method should be Monte Carlo because it is easy to audit. Simulate many remaining BTC paths from the current settlement-source price using the current volatility estimate; count the fraction that finish correctly for p_finish and the fraction that never cross the danger line for p_no_touch.
- Start from the current settlement-source BTC price, not a generic exchange spot price.
- Choose a short time step, initially one second or one observed tick interval.
- Sample path shocks from the calibrated realized-vol distribution for the current seconds-left and regime bucket.
- Apply GARCH/HAR-RV and ETF stress modifiers only as volatility or uncertainty adjustments.
- For each simulated path, record terminal side, barrier touch, maximum adverse excursion, and final-minute wick size.
- Estimate p_finish, p_no_touch, expected adverse excursion, and wick-risk percentiles from the simulated path set.
A Brownian barrier formula can be used later as a fast benchmark, but Monte Carlo should be the first implementation because it keeps the path assumption visible and testable.
## 5.4 Greeks as Binary Risk Sensitivities
The Greeks are not used because the market is literally an exchange option. They are used because a binary payoff has sharp threshold sensitivity, and the system needs to know when a small BTC move or small volatility error can flip the trade quality.
- Delta: how much p_finish changes if BTC moves. High binary delta near the threshold means the contract price should react sharply to spot movement.
- Gamma: how quickly delta changes near the threshold. High gamma means tiny BTC moves can radically change fair value, so the system should demand more edge or block the trade.
- Vega: how sensitive p_finish and p_no_touch are to the volatility estimate. High vega means the trade depends too much on the volatility model being right.
- Theta: how probability changes as expiry approaches. Theta is useful for deciding whether a currently favorable trade is becoming safer through time decay or more fragile because there is too little time to recover.
## 5.5 Volatility Surface and Forecasting
The first volatility surface should be empirical, not a fancy options implied-volatility surface. It should be built from collected BTC data and later extended to ETH and SOL. The surface key is asset x horizon x seconds_left x regime.
- Realized-vol surface: baseline expected remaining movement, calibrated from historical windows that match current asset, horizon, seconds left, and regime.
- HAR-RV: a simple realized-volatility forecast using short, medium, and longer realized-vol windows. This is useful because volatility clusters across multiple horizons.
- GARCH: a volatility-clustering challenger. It can warn that volatility is expanding, but alone it may feel too slow for 5m binaries, so it should be paired with realized vol and HAR-RV.
- ETF options context: IBIT/FBTC implied volatility, skew, and flow can widen uncertainty buffers or block trades during stress, but should not directly issue direction signals.
## 5.6 Risk Layer: Correlation, VaR, and Portfolio
The risk layer is separate from prediction. It decides how much capital can be exposed after the model has already found an apparent edge.
- Correlation: required once the system trades multiple BTC, ETH, or SOL markets. It prevents hidden stacked exposure when several binaries are really the same crypto risk.
- VaR: used for sizing, max-loss limits, and kill-switches. It should not be treated as a directional signal.
- Portfolio: required before real money. Every active binary belongs to one portfolio with shared capital, shared drawdown limits, and shared exposure limits.
## 5.7 Deferred Models and Why They Wait
Several models are useful conceptually but should not control the first implementation. The first system should be transparent enough to falsify before adding heavier modeling.
- XGBoost: best first ML model after enough labeled data exists. It should begin as a challenger/filter that learns when the deterministic model is likely wrong.
- HMM: useful later for regime labels such as calm, trend, chop, high-vol, and event mode. It should not be required for the first pass.
- Neural nets, LSTM, and attention models: do not use first. They need more clean labeled data than the project will have at launch and can hide overfitting.
- Gaussian processes: useful for uncertainty modeling, but too heavy for the first trading loop.
- Heston: useful conceptually for stochastic-volatility thinking, but too heavy as the first pricing engine for 5m/15m binaries.
- Rates: theoretically part of option pricing, but for 5m/15m binaries rates are not a meaningful first-order driver.
The sharp initial system is therefore: digital/barrier option model + realized-vol surface + Greeks + Monte Carlo calibration + GARCH/HAR-RV volatility forecast + VaR/portfolio/correlation risk layer + XGBoost challenger later + HMM regime filter later.
# 6. ETF Options Context
BTC ETF options data is included because it may contain information about expected volatility, skew, and risk appetite. However, this data is not used as a direct entry signal at the beginning. The design uses ETF options as a modifier of confidence and risk.
# 7. GEX Infrastructure Reuse Plan
The BTC binary project should reuse selected GEX infrastructure, but it should not import the GEX trading thesis as a decision rule. GEX is useful here because it already contains working patterns for option-chain collection, normalization, watchlist control, storage, and API serving. The binary engine remains a separate probability system whose target is the venue-defined settlement price.
## 7.1 Components to Reuse
- Schwab authentication and option-chain collection patterns: reuse the existing Schwab access pattern to collect IBIT option chains first, then FBTC if liquidity and data quality justify a second ETF source.
- Option-chain normalization: adapt the existing contract normalization approach so ETF options become a clean OptionsContextFrame with expiry, strike, right, bid, ask, mid, IV, Greeks, volume, open interest, quote age, and data-quality flags.
- Contract selection logic: reuse the orderflow-picker idea from GEX, which selects a bounded set of relevant contracts instead of trying to stream every listed strike. For this project, the selector should prioritize near-ATM IBIT contracts, front expiries, high open interest, high volume, and acceptable spreads.
- Watchlist and persistence controls: reuse the GEX watchlist-control pattern so ETF symbols and option-flow persistence are explicitly enabled. The default should be conservative: collect IBIT first; add FBTC only after IBIT proves useful.
- Parquet and DuckDB research tier: reuse the GEX storage pattern in which Parquet is the durable research source and DuckDB performs feature aggregation for backtests, reports, and calibration studies.
- FastAPI serving pattern: reuse the route style, not necessarily the exact code, to expose normalized outputs such as /api/btc/options-context, /api/markets/{market_id}/decision, and /api/reports/calibration.
## 7.2 How GEX-Derived Data Enters the Binary Model
The GEX-derived ETF layer produces context features, not direct buy or sell commands. The core remaining-path engine estimates p_finish and p_no_touch from the settlement-source BTC price, realized volatility, distance from threshold, and time to expiry. ETF options then adjust uncertainty, sizing, and blocking rules.
- If IBIT near-ATM IV is rising quickly, the expected remaining move widens; this reduces confidence in p_no_touch and raises the required edge buffer.
- If downside skew or put-premium pressure expands, the system marks the window as stress-prone; this can reduce size or block trades that depend on price stability.
- If spreads are wide, quote age is stale, or ETF option prints are sparse, the ETF context is marked low quality and cannot influence a live decision.
- If ETF context is calm while realized BTC volatility is falling, the model may allow normal sizing, provided the settlement price, order book, structure filters, and edge checks also pass.
## 7.3 Components Not to Reuse as Strategy
The project should not reuse Net GEX as a directional BTC binary signal. GEX sign conventions are useful for describing option exposure under assumptions about customer and dealer positioning, but the binary contract pays on a venue-defined settlement event. Therefore, GEX-style calculations can support volatility-regime and stress features, but the final decision must still come from executable market price versus calibrated settlement probability.

# 8. Deterministic Decision Tree
The live policy is deterministic. Models produce evidence; the decision tree converts that evidence into log-only, paper-trade, no-trade, or blocked decisions. This is separate from an ML decision tree. The deterministic tree exists so every decision has a reason.
- Market eligibility: BTC only, 5m/15m, open market, known settlement source.
- Data quality: fresh settlement price, fresh order book, enough ticks, no major source disagreement.
- Path probability: correct side of threshold, sufficient z_path, p_finish above minimum, p_no_touch above minimum.
- Fragility: gamma and vega are within limits; GARCH does not warn of severe volatility expansion.
- ETF context: no severe IV expansion, skew shock, or flow-stress warning against the trade.
- Market microstructure: spread, depth, book movement, and fill quality are acceptable.
- Structure: price is not near blocked support/resistance and threshold is not on a major structure level.
- Edge: p_finish minus executable price and all buffers exceeds the minimum edge requirement.
- Risk and sizing: daily loss, VaR, and correlated BTC exposure limits pass.
- Execution mode: read-only logs the decision; paper mode simulates the trade; live mode remains disabled.
# 9. Decision Equation
The key economic comparison is not fair value versus midpoint. It is fair value versus executable price after costs and uncertainty.

# 10. Role of XGBoost
XGBoost can be used from the beginning, but only as a shadow model until enough labeled BTC data exists. Its purpose is to learn when the deterministic probability engine is likely to be wrong. It should not control trade decisions before it demonstrates out-of-sample improvement.

# 11. Backtesting and Validation Plan
The backtest must avoid midpoint illusion. A signal only counts if it could be executed at the observed bid/ask with enough depth and after realistic cost assumptions.
- Collect market observations, feature frames, decisions, paper fills, missed fills, and final outcomes.
- Compare p_finish calibration against actual settlement outcomes.
- Compare p_no_touch calibration against whether price crossed the danger line.
- Measure Brier score and log loss against executable venue prices.
- Run ablations: core model only, core plus structure filter, core plus ETF/GEX-derived options context, core plus XGBoost shadow score.
- Separate results by 5m versus 15m, seconds-to-expiry bucket, spread bucket, and volatility regime.
# 12. Falsification Criteria
The project should stop or change direction if any of these persist after enough data:
- The model does not beat executable venue prices on calibration or scoring.
- Apparent edge exists only against midpoint, not bid/ask.
- Edge disappears after fees, spread, slippage, latency, and uncertainty buffers.
- p_no_touch is unreliable near final resolution.
- ETF/GEX-derived options context does not improve out-of-sample calibration, blocking, or sizing decisions.
- XGBoost overfits and fails out of sample.
- Venue latency and liquidity make realistic fills unlikely.
# 13. Open Questions for Review
- Is the p_finish and p_no_touch split mathematically useful, or does it double-count risk?
- Does Monte Carlo p_no_touch beat a faster Brownian barrier approximation at 5m/15m horizons after calibration?
- How much historical data is needed before XGBoost can be trusted even as a filter?
- Is ETF options context likely to matter at this short horizon, or is it too slow/noisy?
- Which decision gates are likely to be redundant or overfit?
- What would be the fastest falsification experiment before building more infrastructure?
# 14. Incomplete Work
- The baseline p_finish and p_no_touch estimators described above must be calibrated, stress-tested, and possibly replaced if out-of-sample results show poor reliability.
- The settlement-source mapping for target Polymarket BTC markets must be verified.
- ETF options source quality, licensing, and the boundary between reused GEX code and new polymarket-specific adapters must be resolved.
- Paper fill assumptions must be calibrated against observed order-book behavior.
- The first labeled dataset must be collected before any ML model can be judged.
# References and Data Documentation
- Polymarket WebSocket overview: https://docs.polymarket.com/market-data/websocket/overview
- Polymarket order book docs: https://docs.polymarket.com/trading/orderbook
- Polymarket RTDS: https://docs.polymarket.com/market-data/websocket/rtds
- Binance Spot WebSocket streams: https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams
- Coinbase Advanced Trade WebSocket: https://help.coinbase.com/en/developer-platform/websocket-feeds/advanced-trade
- Kraken WebSocket v2 ticker: https://docs.kraken.com/api/docs/websocket-v2/ticker/
- Polygon options trades: https://polygon.io/docs/rest/options/trades-quotes/trades
- Polygon options WebSocket overview: https://polygon.io/docs/websocket/options/overview
- Wolfers and Zitzewitz on prediction markets; Reiner and Rubinstein on barrier options; Andersen, Bollerslev, Diebold, and Labys on realized volatility; GARCH and XGBoost literature for later model validation.
- Internal GEX reference: src/gex/green/orderflow_picker.py for bounded option-contract selection.
- Internal GEX reference: docs/plans/green-option-chain-and-source-migration.md for the Schwab -> hot rows -> Parquet -> DuckDB storage pattern.
- Internal GEX reference: docs/superpowers/specs/2026-05-17-orderflow-live-toggle-design.md for per-symbol orderflow persistence controls.
- Internal GEX reference: docs/decisions/gex-conventions.md for the caveat that GEX sign conventions are descriptive assumptions, not direct dealer-position truth.
| Status: This is an incomplete research paper. It explains the current idea, proposed data sources, model outputs, and decision methodology. It is not a finished empirical result and does not recommend live trading. |
| --- |
| Primary question: Can a remaining-path probability engine identify BTC 5m/15m binary contracts whose executable price is mispriced after spread, fees, slippage, latency, model uncertainty, and fill risk? |
| --- |
| BTC UP contract:
  payoff = 1 if settlement_price > threshold_price
  payoff = 0 otherwise

BTC DOWN contract:
  payoff = 1 if settlement_price < threshold_price
  payoff = 0 otherwise |
| --- |
| Source | Data Collected | Purpose in Model |
| --- | --- | --- |
| Polymarket market data | Market metadata, order books, best bid/ask, price changes, trades, resolution events | Defines the contract and executable price. Used for spread, depth, fill assumptions, and outcome labels. |
| Settlement BTC price | The BTC binary contracts offered on Polymarket closest to the actual price of bitcoin offered in the 5m and 15m timeframes. | Provides current price S, threshold distance, stale-source flags, and final settlement labels. |
| Free BTC spot/order-flow feeds | Trades, candles, L2 books, volume, order-flow imbalance from Binance/Coinbase/Kraken and optionally Hyperliquid | Builds realized-volatility windows, market pressure features, and redundant price checks. |
| BTC ETF options context | IBIT/FBTC/BITB/ARKB IV, skew, volume imbalance, open-interest changes, large-flow flags | Acts as a volatility and stress modifier. It does not directly create trade signals initially. |
| Logged decisions and outcomes | All accepted, blocked, and missed signals with final outcomes | Creates labels for calibration, falsification tests, and later XGBoost training. |
| distance = ln(current_price / threshold_price)      # BTC UP
distance = ln(threshold_price / current_price)      # BTC DOWN
expected_remaining_move = volatility_estimate * sqrt(seconds_left)
z_path = distance / expected_remaining_move |
| --- |
| Component | Role | Decision Effect |
| --- | --- | --- |
| Realized-vol surface | Estimates expected remaining movement from live and historical BTC data. | Feeds z_path, p_finish, and p_no_touch. |
| GARCH challenger | Detects volatility clustering and persistence. | Raises expected move or blocks trades during volatility expansion. |
| Monte Carlo | Simulates possible remaining paths. | Validates p_finish, p_no_touch, max adverse excursion, and final-minute wick risk. |
| Binary Greeks | Measures sensitivity near the threshold. | Blocks high-gamma or unstable-vega trades. |
| ETF Observation | Interpretation | Engine Response |
| --- | --- | --- |
| ETF implied volatility rising | The options market expects larger BTC movement. | Increase expected remaining move, lower p_no_touch, widen model buffer. |
| Put skew expanding | Downside stress or protection demand is increasing. | Reduce confidence in BTC UP contracts or block during severe stress. |
| Large unusual flow | Possible event regime or information shock. | Require more edge or block until path risk normalizes. |
| No abnormal ETF stress | No additional warning from options context. | Continue through the normal decision tree. |
| edge_after_costs =
    p_finish
    - executable_price
    - fees
    - slippage_buffer
    - latency_buffer
    - model_uncertainty_buffer

if edge_after_costs < minimum_edge:
    no trade |
| --- |
| XGBoost Item | Proposed Use |
| --- | --- |
| Inputs | p_finish, p_no_touch, z_path, vol windows, spread, depth, order-flow imbalance, ETF IV/skew features, time-left bucket. |
| Targets | finish win, danger-line touch, profitable after costs, false positive. |
| Initial role | Shadow model only. Log predictions beside deterministic decisions. |
| Future role | False-positive blocker if out-of-sample results justify it. |