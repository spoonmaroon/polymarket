<!-- converted from BTC_Binary_Path_Probability_Incomplete_Research_Paper.before_gex_section.docx -->

A Remaining-Path Probability Framework for Short-Dated BTC Binary Markets
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
z_path normalizes distance from the threshold by expected remaining movement. It prevents the model from treating an $80 cushion as equally meaningful in calm and violent regimes.


# 5. Volatility and Path Methodology
The volatility model estimates how much BTC can plausibly move before the binary expires. The first implementation should use an empirical realized-volatility surface rather than a complicated stochastic-volatility model. The surface is indexed by asset, horizon, seconds to expiry, and market regime.

# 6. ETF Options Context
BTC ETF options data is included because it may contain information about expected volatility, skew, and risk appetite. However, this data is not used as a direct entry signal at the beginning. The design uses ETF options as a modifier of confidence and risk.

# 7. Deterministic Decision Tree
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
# 8. Decision Equation
The key economic comparison is not fair value versus midpoint. It is fair value versus executable price after costs and uncertainty.

# 9. Role of XGBoost
XGBoost can be used from the beginning, but only as a shadow model until enough labeled BTC data exists. Its purpose is to learn when the deterministic probability engine is likely to be wrong. It should not control trade decisions before it demonstrates out-of-sample improvement.

# 10. Backtesting and Validation Plan
The backtest must avoid midpoint illusion. A signal only counts if it could be executed at the observed bid/ask with enough depth and after realistic cost assumptions.
- Collect market observations, feature frames, decisions, paper fills, missed fills, and final outcomes.
- Compare p_finish calibration against actual settlement outcomes.
- Compare p_no_touch calibration against whether price crossed the danger line.
- Measure Brier score and log loss against executable venue prices.
- Run ablations: core model only, core plus structure filter, core plus ETF context, core plus XGBoost shadow score.
- Separate results by 5m versus 15m, seconds-to-expiry bucket, spread bucket, and volatility regime.
# 11. Falsification Criteria
The project should stop or change direction if any of these persist after enough data:
- The model does not beat executable venue prices on calibration or scoring.
- Apparent edge exists only against midpoint, not bid/ask.
- Edge disappears after fees, spread, slippage, latency, and uncertainty buffers.
- p_no_touch is unreliable near final resolution.
- ETF options context does not improve out-of-sample calibration or blocking.
- XGBoost overfits and fails out of sample.
- Venue latency and liquidity make realistic fills unlikely.
# 12. Open Questions for Review
- Is the p_finish and p_no_touch split mathematically useful, or does it double-count risk?
- What is the simplest robust approximation for p_no_touch at 5m/15m horizons?
- How much historical data is needed before XGBoost can be trusted even as a filter?
- Is ETF options context likely to matter at this short horizon, or is it too slow/noisy?
- Which decision gates are likely to be redundant or overfit?
- What would be the fastest falsification experiment before building more infrastructure?
# 13. Incomplete Work
- The exact p_finish and p_no_touch formulas must be selected and validated.
- The settlement-source mapping for target Polymarket BTC markets must be verified.
- ETF options source quality and licensing must be resolved.
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
| Settlement/reference BTC price | The BTC price source closest to the contract resolution rule | Provides current price S, threshold distance, stale-source flags, and final settlement labels. |
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