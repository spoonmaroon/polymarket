<!-- converted from BTC_Binary_Path_Probability_Review_Brief.docx -->

BTC Binary Path Probability Engine
External Review Brief for Strategy, Data, and Decision Logic
Prepared for review | 2026-05-29 | Scope: BTC 5m/15m prediction-market binaries

# Executive Summary
We propose a BTC-only research and paper-trading system for short-dated prediction-market binaries. The engine does not attempt to predict BTC direction in general. It prices a specific binary contract at a specific moment by estimating terminal win probability, path survival probability, executable venue edge, and risk blockers.

# Strategy Thesis
A BTC binary contract is an option-like digital payoff:

The proposed edge is not generic BTC prediction. The proposed edge is estimating whether the remaining path risk is lower than the venue's executable price implies, after spread, depth, fees, slippage, stale-data risk, and model uncertainty.
# Data Lineage

# Core Computed Values

# Model Roles

# Decision Policy Tree
No model directly trades. Models produce evidence; the deterministic policy tree turns evidence into action.
- Market eligibility: BTC only, 5m/15m horizon, open market, known settlement source.
- Data quality: settlement price fresh, order book fresh, enough ticks, no major source disagreement.
- Path probability: correct side of threshold, enough z_path, p_finish high enough, p_no_touch high enough.
- Fragility: gamma acceptable, vega acceptable, no major volatility expansion warning.
- ETF context: no severe IV expansion, skew warning, or flow-stress condition against the trade.
- Microstructure: spread, depth, order-book movement, and fill quality acceptable.
- Structure: not near blocked support/resistance or threshold-on-structure levels.
- Edge: edge_after_costs exceeds minimum required edge.
- Risk/sizing: daily loss, VaR, exposure, and correlation limits pass.
- Execution mode: log-only, paper trade, or supervised approval; live disabled by default.


# ETF Options Usage
BTC ETF options data is included because BTC now has liquid ETF-linked options that may reflect short-term volatility demand, downside skew, and event stress. The initial design does not let ETF options create trades directly.

# Backtest And Falsification Plan
The project should be killed or redesigned if it fails the simplest evidence tests.
- The model does not beat executable venue prices on Brier score or log loss.
- Apparent edge exists only against midpoint, not bid/ask.
- Edge disappears after fees, spread, slippage, and stale-data buffers.
- p_no_touch is miscalibrated near final resolution.
- ETF context adds no out-of-sample improvement to calibration or blocking.
- XGBoost improves in-sample metrics but fails out of sample.
- Polymarket liquidity or latency makes assumed fills unrealistic.
# Specific Review Questions
- Is the digital-option plus remaining-path framing valid for 5m/15m BTC binaries?
- Are p_finish and p_no_touch the right separation, or are we double-counting risk?
- Are the proposed data feeds sufficient to estimate remaining path risk?
- Is ETF options context useful at this horizon, or too slow/noisy?
- Are the decision gates ordered correctly?
- Which gates are redundant, overfit-prone, or missing?
- Should XGBoost be shadow-only first, or can it safely influence paper decisions earlier?
- What is the simplest falsification test before more engineering work?
# Known Limitations
- Prediction-market WebSocket data may not be enough for historical order-book replay; forward capture is required.
- Settlement-source mismatch can make a good BTC forecast a bad contract forecast.
- Public venue feeds and real fill quality may differ under latency.
- ETF options data may be licensed, delayed, expensive, or too slow for 5m decisions.
- Short-horizon models are vulnerable to overfitting and final-minute microstructure noise.
# Reference Data Sources

# Decision Requested From Reviewer

| REVIEW ASK: Evaluate whether the proposed BTC binary pricing engine is conceptually sound, whether its data inputs can support the decisions claimed, and where the design is most likely to fail before any live capital is used. |
| --- |
| Decision | Current Recommendation | Reason |
| --- | --- | --- |
| Initial market | BTC 5m and 15m binaries only | BTC has the best liquidity, spot/perp data, and ETF options context. |
| Initial venue | Polymarket market data first | Official WebSockets provide order book, price-change, best-bid/ask, and trade events. |
| Execution | Read-only and paper mode only | The edge must be proven after costs and latency before live capital. |
| ETF options | Context/risk modifier only | Useful for volatility and stress context; not a direct buy/sell trigger. |
| XGBoost | Shadow model from day one | Can learn false positives later, but should not control decisions without labels. |
| PAYOFF: Pays 1 if BTC settles on the winning side of the threshold; pays 0 otherwise. |
| --- |
| Data Source | What We Collect | Computed Values | Decision Use |
| --- | --- | --- | --- |
| Polymarket market data | Market metadata, order book snapshots, best bid/ask, price changes, trade events, resolution state | Executable price, spread, depth, venue latency, market status | Determines whether apparent edge is actually tradable. |
| Settlement/reference BTC price | The price source used or closest to the contract resolution rule | Current price, threshold distance, stale-source flags | Drives p_finish, p_no_touch, z_path, and data-quality gates. |
| Free BTC spot/order-flow feeds | Trades, candles, L2 books, volume, imbalance from Binance/Coinbase/Kraken and optionally Hyperliquid | Realized vol, volume, order-flow imbalance, redundant price checks | Estimates expected remaining move and detects volatility/order-flow stress. |
| BTC ETF options data | IBIT/FBTC/BITB/ARKB IV, skew, call/put imbalance, OI changes, large-flow flags | ETF vol regime, skew warning, flow stress flag | Modifies risk buffers or blocks trades during options-market stress. |
| Logged outcomes | Final settlement, path touch, max adverse excursion, paper fill/miss | Labels for calibration and XGBoost | Tests whether the model beats executable venue prices out of sample. |
| Value | Definition | How It Affects Decisions |
| --- | --- | --- |
| p_finish | Probability BTC finishes on the winning side at expiry. | Creates raw fair value. If p_finish is below threshold, no trade. |
| p_no_touch | Probability BTC does not cross back through the danger line before expiry. | Blocks unstable paths even when p_finish looks attractive. |
| z_path | Distance from threshold divided by expected remaining move. | Blocks trades that are too close to the threshold for current volatility. |
| edge_after_costs | p_finish minus executable price, fees, slippage, latency buffer, and model uncertainty. | Candidate trades require positive edge above a configured threshold. |
| gamma / vega risk | Sensitivity of binary probability to price and volatility near the threshold. | Blocks fragile trades where tiny moves or vol errors can flip the edge. |
| Model | Role | Decision Contribution |
| --- | --- | --- |
| Digital option framing | Defines the binary payoff. | Transforms p_finish into first fair value. |
| Realized-vol/path-risk surface | Estimates expected remaining BTC movement. | Feeds z_path, p_finish, p_no_touch. |
| Remaining path model | Prices terminal win and path survival. | Main signal engine. |
| Greeks | Measures probability fragility around the threshold. | Blocks high-gamma or unstable-vega setups. |
| Monte Carlo | Stress-tests remaining paths offline. | Calibrates and validates the fast path model. |
| GARCH | Warns when volatility may persist or expand. | Raises expected move or buffers; can block vol-expansion setups. |
| ETF options context | Captures BTC-related volatility/skew/flow stress. | Adjusts risk confidence, not direct entries. |
| XGBoost | Learns false positives after labels exist. | Shadow model first; future blocker only if out-of-sample performance proves value. |
| EXAMPLE ACCEPTED DECISION: BTC UP, 90 seconds left, ask 0.78, p_finish 0.86, p_no_touch 0.81, z_path 1.75, edge_after_costs 0.05, no ETF/structure/latency warnings. Output: PAPER_TRADE, reason positive_edge_path_survival. |
| --- |
| EXAMPLE BLOCKED DECISION: p_finish 0.86 but p_no_touch 0.58, high gamma, and near resistance. Output: BLOCKED, reason weak_path_survival + threshold_gamma_risk + near_resistance. |
| --- |
| ETF Signal | Interpretation | Engine Response |
| --- | --- | --- |
| IBIT/FBTC IV rising quickly | Options market expects larger BTC movement. | Increase expected remaining move; reduce p_no_touch; widen uncertainty buffer. |
| Put skew expanding | Downside protection demand rising. | Reduce confidence in BTC UP trades or block during severe stress. |
| Large flow flag | Potential event/stress regime. | Block or require higher edge and p_no_touch. |
| No abnormal options stress | No additional warning from ETF lane. | Decision continues through normal gates. |
| Area | Reference |
| --- | --- |
| Polymarket WebSocket | https://docs.polymarket.com/market-data/websocket/overview |
| Polymarket order book | https://docs.polymarket.com/trading/orderbook |
| Polymarket RTDS | https://docs.polymarket.com/market-data/websocket/rtds |
| Binance Spot WebSocket | https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams |
| Coinbase WebSocket | https://help.coinbase.com/en/developer-platform/websocket-feeds/advanced-trade |
| Kraken WebSocket | https://docs.kraken.com/api/docs/websocket-v2/ticker/ |
| Polygon options trades | https://polygon.io/docs/rest/options/trades-quotes/trades |
| Polygon options WebSocket | https://polygon.io/docs/websocket/options/overview |
| DECISION REQUEST: Tell us whether this is a defensible research system, what should be removed before implementation, and what single falsification test should run first. We are not asking for approval to trade live capital. |
| --- |