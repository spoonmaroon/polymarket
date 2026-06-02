<!-- converted from BTC_Binary_Path_Probability_Incomplete_Research_Paper.docx -->

A Remaining-Path Probability Framework for Short-Dated BTC and ETH Binary Contracts
Terminal Probability, Path Survival, and Executable Edge for Crypto Binary Markets
Enoch Poon
May 31, 2026
# Abstract
This paper studies short-dated crypto binary contracts, with BTC and ETH as the first markets of interest. These contracts look simple because they resolve to either one dollar or zero, but their pricing problem is not simply whether the asset is bullish or bearish. Each contract is tied to a venue-defined threshold, a settlement source, a short expiry window, and an executable order-book price.
The proposed framework estimates the remaining state of a specific contract at a specific decision time. It separates terminal win probability, path-survival probability, normalized distance from the danger line, movement scale, and executable edge after costs. This separation matters because a contract can be likely to finish on the correct side while still being unsafe to enter if the remaining path is unstable or the order book is too expensive.
The first version should remain read-only. It should collect BTC and ETH market data, settlement-source prices, order-book snapshots, and decision logs before placing capital at risk. The research question is whether as-of path simulation and executable-price comparison can identify binary contracts whose quoted prices are mispriced after costs, latency, data quality, and risk controls.
# Contents
1. introduction and research objective
2. market instrument, rule parsing, and settlement source
2.1 binary payoff definition
2.2 settlement-source hierarchy
3. asset universe and scope: btc, eth, and future sol
4. as-of data architecture and state construction
5. core hybrid probability engine
5.1 p_finish: terminal win probability
5.2 p_no_touch: path-survival probability
5.3 z_path: normalized cushion from the danger line
5.4 movement scale: sigma_tau
5.5 executable edge after costs
6. monte carlo methodology and multiple path generators
6.1 prior distribution for monte carlo path generation
6.2 are we using multiple generations?
6.3 generator set
6.4 ensemble probability and dispersion
6.5 monte carlo test cases and unanswered questions
6.6 cached grids and refresh rules
7. polymarket order book and executable price
7.1 executable price
7.2 market impact and crowding
8. decision features and structure risk
8.1 decision outputs
8.2 structure and support/resistance features
9. conclusion and future research plan
# 1. Introduction and Research Objective
Short-dated crypto binary contracts are threshold instruments. A BTC or ETH UP contract pays only if the settlement-source price finishes above the contract threshold; a DOWN contract pays only if the settlement-source price finishes below it. The payoff is simple, but the decision problem is not. At any decision time, the trader has to evaluate the current asset price, the threshold, the time remaining, the settlement source, the live order book, and the probability that the remaining path stays tradable.
This paper focuses on BTC and ETH because they are the first contracts the system is meant to study and eventually trade. The framework is not designed to predict whether crypto is generally bullish or bearish. It is designed to price a specific binary contract at a specific moment and decide whether the executable quote is attractive enough after costs and risk controls.
The core idea is remaining-path probability. A model that only asks whether the final price will finish above or below the threshold is incomplete. It also needs to ask whether the path before expiry is stable enough to justify entry. This is why the framework separates p_finish, the probability of finishing on the winning side, from p_no_touch, the probability of avoiding a dangerous threshold crossing before expiry.
The research question is: can a BTC and ETH binary-contract engine use as-of data, Monte Carlo path simulation, volatility-aware distance measures, order-book execution costs, and risk gates to identify contracts whose quoted prices are mispriced?
# 2. Market Instrument, Rule Parsing, and Settlement Source
Before any probability model can be trusted, the contract rule has to be read exactly. The model does not invent the threshold, change the settlement source, or reinterpret the market after the fact.
The venue defines the contract. The model should not choose the threshold, alter the start/end time, replace the settlement source, or reinterpret the rule text. The model may reject a market, demand more edge, or size down, but it must price the contract as written.
Polymarket documentation describes market resolution through UMA’s Optimistic Oracle process, where outcomes can be proposed and disputed before finalization. Current short-dated crypto Up/Down market pages also state that BTC, ETH, and SOL Up/Down contracts resolve using Chainlink Data Streams for the relevant asset pair, such as BTC/USD, ETH/USD, and SOL/USD. Polymarket’s RTDS documentation also lists real-time crypto price streams with Chainlink and Binance symbols. The practical implementation rule is simple: parse the actual market rule text for each contract, store the rule hash, and treat the named settlement source as the scoreboard. [1][2][5][6][7][8]

## 2.1 Binary payoff definition
For a generic threshold binary, the payoff is:


Variables:
= venue-defined threshold or reference price.
= final settlement-source price at expiry .
= indicator function equal to 1 if the condition is true and 0 otherwise.
Important rule note: some Up/Down markets use the end price relative to the start price and may define Up as greater than or equal to the start price. The rule parser must store the exact comparison operator, such as , , , or , instead of assuming one default.
## 2.2 Settlement-source hierarchy
The settlement-source layer should use the following hierarchy:
Primary: the exact source named in the market rules. For current short-dated crypto Up/Down examples, this appears to be Chainlink Data Streams for BTC/USD, ETH/USD, or SOL/USD. [5][6][7]
Secondary: Polymarket RTDS crypto stream if it provides the named Chainlink symbol or a venue-supported representation. [2]
Proxy: Binance, Coinbase, Kraken, or a robust exchange basket only for quality checks or when the primary source is unavailable.
Block: if the source is unknown, stale, missing, or materially inconsistent with validated proxies.
A useful source-disagreement measure is:

Variables:
= absolute disagreement between primary and proxy price at time .
= current price from the named settlement source.
= robust proxy price from exchange or venue-supported feeds.
Decision rule: if  is above the configured tolerance, the system should block the market or add a source-risk buffer to the required edge.

# 3. Asset Universe and Scope: BTC, ETH, and Future SOL
This section keeps the project from becoming too broad too early. BTC and ETH are the first real target markets because they have the most natural short-dated crypto binary use case. SOL can remain a later extension after the BTC/ETH methodology is validated.
The first implementation should focus on BTC and ETH short-dated Polymarket contracts. SOL should be logged and researched later, but it should not be part of the first trading scope unless liquidity, settlement-source quality, and shadow results justify promotion.
The model should not use BTC/ETH correlation to create a directional entry signal. For example, the system should not buy an ETH contract just because BTC moved first unless a later, separately validated feature proves that relationship out of sample. In v1, BTC and ETH are priced independently.
Correlation and common crypto beta still matter for portfolio risk. If the system has simultaneous BTC and ETH exposure during the same macro event or same crypto liquidation cascade, the portfolio layer should know that risk may concentrate. That is a sizing and cap issue, not a signal-generation issue.


Figure 1. Multi-asset as-of data architecture. BTC and ETH are separate contract signals, while source validation, order-book handling, risk control, and logging are shared infrastructure.
# 4. As-Of Data Architecture and State Construction
The model can only use information that would have existed at decision time. This is the anti-overfit rule. Future price movement, final settlement, and later Polymarket prices are labels for scoring, not inputs for the decision. The state object is the model's snapshot of the world. At every decision timestamp, it should contain the current contract, current settlement-source price, recent realized movement, order-book state, source-quality flags, and time remaining.
Every decision must be as-of. At decision time , the system can use only information timestamped at or before . Future settlement, later prediction-market quotes, later candles, later news interpretation, and end-of-day summaries are labels or research data, not decision inputs.
The state builder converts raw event streams into a compact object that the probability engine, execution model, portfolio layer, shadow logger, dashboard, and kill switch can all read.
# 5. Core Hybrid Probability Engine
This is the mathematical core of the paper. The math should be read as a translation layer: it turns the contract state into probabilities, then compares those probabilities against executable market prices.
The core outputs are p_finish, p_no_touch, z_path, sigma_tau, and executable edge. They are not separate trading strategies. They are different measurements of the same contract state.
Quick symbol guide: S means price, K means threshold, t means now, T means expiry, tau means time left, sigma means movement or volatility scale, p means probability, and e means edge.
The hybrid engine uses terminal probability to estimate fair value and path/risk variables to decide whether the edge is usable. This avoids two bad extremes. It does not ignore path risk, but it also does not turn p_no_touch into a second payoff probability unless the strategy explicitly uses early exits or mark-to-market stops.


Figure 2. Hybrid decision model. p_finish prices the payout, while path survival, execution quality, event risk, uncertainty, and blockers decide whether the edge can be used.
## 5.1 p_finish: terminal win probability
p_finish asks only where the contract ends. For an UP contract, it asks whether the final BTC or ETH settlement price finishes above the threshold. For a DOWN contract, it asks whether the final settlement price finishes below the threshold.
p_finish does not tell us whether the path before expiry is stable, whether the order book is liquid, or whether the contract is cheap enough to buy.
p_finish asks whether the contract finishes on the winning side at expiry. It is the raw probability that the one-dollar binary pays out.
For Monte Carlo path :


Variables:
= number of simulated remaining paths.
= index for one simulated path.
= terminal win indicator for path .
= estimated terminal win probability.
Interpretation: if , the raw pre-cost fair value of a one-dollar binary payoff is about $0.74. It is not yet a trade decision.
## 5.2 p_no_touch: path-survival probability
p_no_touch asks the harder question. It asks whether the asset can stay on the favorable side without crossing back through the danger line before expiry.
This is why p_no_touch can be lower than p_finish. The contract may still be likely to finish correctly, but if the path is unstable, the trade may require more edge or should be blocked.
p_no_touch asks whether the simulated path avoids crossing the danger line before expiry. It captures the stability of the remaining path.
For an UP contract:

For a DOWN contract:

The Monte Carlo estimate is:

Variables:
= simulated settlement-source price at time  on path .
= remaining interval from decision time  to expiry .
= venue-defined threshold or start reference price.
= path-survival indicator for path .
= estimated path-survival probability.
Interpretation: a contract can have high p_finish but low p_no_touch. That means the final settlement may still be favorable, but the path is unstable enough that the system should wait, block, demand more edge, or reduce size.
## 5.3 z_path: normalized cushion from the danger line
Plain English: z_path measures how much cushion the contract has. It compares the current distance from the threshold against the expected remaining movement.
A raw dollar distance is not enough. Being 100 dollars above the threshold means one thing in quiet BTC and another thing in violent ETH. z_path makes the distance volatility-aware.
z_path measures the current cushion in units of expected remaining movement.
For an UP contract:

For a DOWN contract:

Side-specific distance:

Normalized cushion:

Variables:
= current settlement-source price at decision time .
= venue-defined threshold or start reference price.
= favorable log distance for an UP contract.
= favorable log distance for a DOWN contract.
= side-specific favorable distance.
= expected remaining log-price movement over the time left.
= current cushion measured in expected remaining-move units.
Interpretation:  means price is near the danger line.  means the cushion is about one expected remaining move.  means the cushion is about two expected remaining moves.
## 5.4 Movement scale: sigma_tau
sigma_tau estimates how much the asset can still move before expiry. It is not a direction signal. It tells the Monte Carlo engine how wide the simulated future paths should be.
If sigma_tau is too small, the model becomes overconfident. If it is too large, the model becomes too scared to trade. The first version should therefore use a conservative realized-volatility estimate with a floor and regime adjustment.
The system is not trying to forecast direction with sigma_tau. It is estimating how much the settlement-source price can still move before expiry.
A first-pass movement scale is:

Variables:
= expected remaining log-price movement from  to .
= minimum movement assumption so the model never becomes risk-free in a quiet tape.
= volatility-regime multiplier.
= seconds or fraction of time remaining until expiry, using the same unit as the volatility estimates.
= short-window realized volatility.
= medium-window realized volatility.
= longer-window realized volatility.
, ,  = preset weights that sum to one.
The starting weights should be preset before testing, then evaluated walk-forward. They should not be optimized until they only fit old contracts.
Origin note: the exact sigma_tau formula is a project-defined movement-scale heuristic. It borrows the idea that volatility has short-, medium-, and long-horizon components from realized-volatility research such as HAR-RV [21], but the weights, volatility floor, and regime multiplier are design choices that must be fixed before validation.
## 5.5 Executable edge after costs
Plain English: a probability estimate only matters after it is compared against the price we can actually trade. A 74 percent fair probability is not useful if the contract costs 76 cents after spread, fees, slippage, and latency buffer.
This section is where the model leaves pure probability and becomes a decision system. The trade is only interesting if edge remains after executable price and all cost buffers.
The raw fair value is:

For a one-dollar binary, the first edge estimate is:

The usable edge is:

Variables:
= candidate contract.
= terminal win probability for contract .
= executable entry price, ideally target-size VWAP rather than midpoint.
= book-crossing and execution costs: bid-ask crossing, visible-depth slippage, fees, latency, and uncertain-fill risk.
= model uncertainty buffer from Monte Carlo dispersion, sparse bucket, calibration error, or data quality.
= path-risk and event-risk buffer from p_no_touch, z_path, structure risk, and news/event risk.
The required edge is:

Variables:
= minimum edge required before a trade is allowed.
= base edge requirement.
= bid-ask crossing and uncertain-fill buffer.
= buffer for quote movement between signal and fill.
= feed noise, bad ticks, and source-disagreement buffer.
= Monte Carlo uncertainty and sparse-bucket buffer.
= path-instability buffer from p_no_touch, z_path, and structure risk.
= economic news, ETF, regulatory, exchange-outage, or event-risk buffer.
Trade condition:

Interpretation: Version C is the baseline. p_finish prices the payout. p_no_touch, z_path, execution quality, data quality, news/event risk, XGBoost, and portfolio limits decide whether to trade, wait, block, demand more edge, or size down.
Origin note: the edge equations are project-defined decision equations. They are not a published option-pricing formula; they translate a one-dollar binary expected value into a tradability rule after executable price, costs, uncertainty, and path-risk buffers.
### Formula provenance note.
The formulas below have different origins. Some are standard probability or financial-engineering estimators; others are project-defined diagnostics. Project-defined does not mean arbitrary: it means the paper is proposing the calculation and it must be validated before it can be trusted.
# 6. Monte Carlo Methodology and Multiple Path Generators
Purpose of this section: Monte Carlo is the main estimator because it can answer both terminal and path questions. It can count how many simulated paths finish correctly and how many survive without crossing the danger line.
The important implementation rule is as-of simulation. At decision time, the engine may simulate possible futures or sample historical fragments, but it may not use the actual future of the contract being tested as an input.
The primary estimator should be as-of walk-forward empirical Monte Carlo. At every decision time , the engine builds the state as if it is living at , then simulates possible remaining paths using only data available before  or historical fragments from earlier periods. The realized future of the current contract is never an input.


Figure 3. Multiple Monte Carlo path generators. The ensemble uses several ways to generate paths from the same as-of state so model risk becomes visible.


Figure 4. As-of decision snapshot. The observed series stops at the decision boundary; the hidden future is used only later for scoring.

## 6.1 Prior distribution for Monte Carlo path generation
Monte Carlo paths do not come from nowhere. Before the engine can simulate future BTC or ETH prices, it must define a prior distribution: the set of remaining path behaviors the engine believes is realistic before the future of the current contract is known.
In this paper, prior distribution does not mean guessing by opinion. The first version should use an empirical conditional prior: historical BTC or ETH path fragments that match the current as-of state and were available before the decision time.
The prior answers a simple question: historically, when the contract state looked like this at time t, what did the remaining path usually look like?
The prior should condition on:
- asset: BTC or ETH;
- contract horizon: 5-minute or 15-minute;
- seconds left until expiry;
- side and distance from the threshold;
- z_path bucket;
- realized-volatility regime and volatility trend;
- recent wick frequency, threshold crosses, and adverse excursion;
- source-quality state, including stale feeds or settlement/proxy disagreement;
- event-window flag if scheduled or breaking news is active.
The no-future-data rule still controls this section. The prior may use older historical data and live observations up to time t. It may not use the realized future of the contract being replayed. Future asset movement, final settlement, and later Polymarket prices are labels only.
If the matched bucket has enough examples, sampled fragments become the base Monte Carlo shocks. If the bucket is sparse, the engine should widen to a coarser bucket, increase the uncertainty buffer, or block the trade. This prevents fake confidence from a tiny historical sample.
The prior is then adapted to the current market. sigma_tau scales sampled shocks to current expected remaining movement, while stress overlays can add final-window wick risk or event-window risk. This gives the engine historical path realism without ignoring live volatility.
Plain English: the prior is the simulation's starting belief. Monte Carlo is the counting machine. The prior decides what kinds of paths the machine is allowed to count. The counted paths then produce p_finish and p_no_touch.
This design uses Monte Carlo as the counting framework [20], bootstrap-style resampling for dependent time-series paths [9], and realized-volatility scaling from models such as HAR-RV [21].

## 6.2 Are we using multiple generations?
Yes, but the paper should call them multiple path generators rather than future generations. This avoids confusion. A path generator is a way of creating simulated remaining paths from the same as-of state. It does not look at the future of the current contract.

Using multiple generators is useful because each generator fails differently:
empirical conditional priors preserve real wicks but may have sparse comparable buckets;
block bootstrap preserves short-term dependence but can create unnatural joins;
filtered historical simulation handles volatility scaling but can smooth path shape too much;
stress overlays expose final-window and news-window risk but should not dominate the central estimate.
The point is not to average random models blindly. The point is to measure whether the trade only works under one fragile path assumption. If the edge disappears under reasonable path generators, the system should demand more edge or block.
## 6.3 Generator set
Block and stationary bootstrap methods are standard ways to resample dependent time-series data without pretending every return is independent. The stationary bootstrap is a classic method for weakly dependent time series. [9]
## 6.4 Ensemble probability and dispersion
Each generator  produces a terminal probability and path-survival probability:

The ensemble probability can start as a weighted average:


Variables:
= number of path generators.
= generator index.
= preset generator weight, with .
= terminal win probability from generator .
= path-survival probability from generator .
= ensemble terminal win probability.
= ensemble path-survival probability.


Generator disagreement becomes part of uncertainty:

Variables:
= uncertainty from disagreement across path generators.
Higher  means the result depends heavily on modeling choice.
## 6.5 Monte Carlo test cases and unanswered questions
The research plan should explicitly test the following:
## 6.6 Cached grids and refresh rules
A full Monte Carlo run on every tick is unnecessary. The live path should use cached probability grids and refresh only when the state changes enough to matter.
# 7. Polymarket Order Book and Executable Price
Purpose of this section: the market price used in the decision must be executable. The model should not compare fair value against a midpoint if the real entry would require crossing the ask or accepting depth and latency risk.
The probability engine estimates value. The execution model decides whether that value is tradable. Polymarket describes its trading system as a central limit order book, and its docs state that all orders are technically limit orders; a market order is a limit order priced to execute immediately against resting orders. Live order-book data should use the WebSocket market channel rather than only polling, and the REST order-book endpoint can provide a book summary with bids, asks, market details, and last trade price. [3][4]
This means the document should avoid saying “send a market order” as if execution were free. The correct execution assumption is:
Submit a marketable limit order with a maximum acceptable price, and simulate whether visible depth can fill the target size after latency stress

Figure 5. Execution model. Shadow fills use visible depth, VWAP, impact, quote-age checks, and latency stress rather than midpoint prices.
## 7.1 Executable price
For buying a YES-like outcome, the executable price should use the ask side of the book. For target size :


Variables:
= target order size.
= maximum acceptable price for a marketable limit order.
= price at order-book level .
= quantity available at level .
= quantity available up to .
= set of price levels needed to fill the target size.
= depth-weighted entry price for the target size.
Execution is valid only if:

and quote age, bid-ask spread, latency, visible depth, and source-quality gates all pass.
## 7.2 Market impact and crowding
Thin books can erase the edge. The system should treat its own order as part of the execution problem.
A first impact metric is:

Variables:
= proposed order size divided by visible depth near the executable price.
= target size for contract .
= visible depth within an acceptable price band .
First rule: block or reduce size when the target order is too large relative to visible depth. The system should also track crowding risk: quote update bursts, vanishing depth, high bid-ask spread volatility, repeated small fills just before expiry, and sudden order-book imbalance changes.
# 8. Decision Features and Structure Risk
Purpose of this section: risk gates prevent a theoretically attractive probability from becoming a bad trade. The model should be allowed to say wait, block, or demand more edge even when p_finish looks favorable.
The decision layer turns model outputs into trade, wait, block, demand-more-edge, and size-adjustment decisions. The key improvement is to make structure and threshold-risk features mechanical rather than discretionary chart commentary.
## 8.1 Decision outputs
## 8.2 Structure and support/resistance features
Support and resistance should be quantified as structure risk around the contract’s threshold.
One simple congestion formula is:

Variables:
= fraction of lookback window spent near threshold .
= settlement-source or robust price at time .
= near-threshold band.
= lookback window.
Decision rule: high congestion, high crossing count, or large adverse wick ratio should increase required edge or block if the threshold is too unstable.
Origin note: the congestion formula is project-defined. It comes from the practical idea that a threshold becomes more dangerous when price repeatedly hovers near it. Mathematically, it is just the fraction of observations in a lookback window that fall inside a band around K; it is a structure-risk feature, not a standard option-pricing formula.
# 9. Conclusion and Future Research Plan
The validation requirement is intentionally stricter than a normal chart backtest: the framework only matters if as-of replay and live shadow logs show calibrated probabilities and positive executable expected value without using future data.
This paper narrows the project to a research question: can short-dated BTC and ETH binary contracts be priced more accurately by separating terminal win probability from path-survival probability and then comparing the resulting fair value against executable market prices?
The proposed answer is a remaining-path probability framework. p_finish estimates whether the contract finishes on the winning side. p_no_touch estimates whether the path survives without crossing the danger line. z_path converts the current cushion into volatility-aware distance units. sigma_tau estimates the remaining movement scale. Executable edge then compares the model probability against the actual order-book price after costs and buffers.
The key methodological constraint is the as-of rule. At any decision timestamp, the engine may use only information that existed at or before that timestamp. Future settlement, future BTC or ETH movement, and later market prices are labels for scoring, not inputs for decision-making.
# References and Data Documentation
[1] Polymarket Documentation, “Resolution.” Notes UMA Optimistic Oracle resolution mechanics. https://docs.polymarket.com/concepts/resolution
[2] Polymarket Documentation, “Real-Time Data Socket.” Describes RTDS streaming and supported crypto price symbols. https://docs.polymarket.com/market-data/websocket/rtds
[3] Polymarket Documentation, “Prices & Orderbook.” Describes order-book concepts and limit-order treatment. https://docs.polymarket.com/concepts/prices-orderbook
[4] Polymarket Documentation, “Market Channel” and “Orderbook.” Describes real-time order-book updates and REST order-book access. https://docs.polymarket.com/market-data/websocket/market-channel and https://docs.polymarket.com/trading/orderbook
[5] Polymarket BTC Up/Down market example. Rule text identifies Chainlink BTC/USD as the resolution source. https://polymarket.com/event/btc-updown-5m-1774121700
[6] Polymarket ETH Up/Down market example. Rule text identifies Chainlink ETH/USD as the resolution source. https://polymarket.com/event/eth-updown-5m-1780065900
[7] Polymarket SOL Up/Down market example. Rule text identifies Chainlink SOL/USD as the resolution source. https://polymarket.com/event/sol-updown-5m-1780060800
[8] Polymarket Documentation, “Resolution” and current crypto market pages. Practical note: rule text remains the authority for each contract.
[9] Politis, D. N., and Romano, J. P. (1994). “The Stationary Bootstrap.” Journal of the American Statistical Association. https://www.ssc.wisc.edu/~bhansen/718/Politis%20Romano.pdf
[10] scikit-learn documentation, brier_score_loss. Defines Brier score for probabilistic binary outcomes. https://scikit-learn.org/stable/modules/generated/sklearn.metrics.brier_score_loss.html
[11] scikit-learn documentation, TimeSeriesSplit. Describes validation for time-ordered data. https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
[12] scikit-learn documentation, log_loss. Describes probabilistic log loss. https://scikit-learn.org/stable/modules/generated/sklearn.metrics.log_loss.html
[13] scikit-learn documentation, calibration_curve. Describes reliability diagrams for probability calibration. https://scikit-learn.org/stable/modules/generated/sklearn.calibration.calibration_curve.html
[14] XGBoost documentation, “Parameters.” Notes probability-producing binary classification objectives such as binary:logistic. https://xgboost.readthedocs.io/en/stable/parameter.html
[15] XGBoost documentation, “Monotonic Constraints.” Describes constraints for enforcing directional feature relationships. https://xgboost.readthedocs.io/en/latest/tutorials/monotonic.html
[16] XGBoost documentation, “Feature Interaction Constraints.” Describes restricting which variables may interact. https://xgboost.readthedocs.io/en/stable/tutorials/feature_interaction_constraint.html
[17] Kevin Davey, KJ Trading Systems, “What 567,000 Backtests Taught Me About Algo Trading Exits.” Used as a research prompt for testing stop-and-exit and stop-and-reverse policies; the paper still requires Polymarket-specific validation. https://kjtradingsystems.com/algo-trading-exits.html
[18] Black, F., and Scholes, M. (1973). “The Pricing of Options and Corporate Liabilities.” Journal of Political Economy, 81(3), 637-654. https://doi.org/10.1086/260062
[19] Merton, R. C. (1973). “Theory of Rational Option Pricing.” The Bell Journal of Economics and Management Science, 4(1), 141-183. https://doi.org/10.2307/3003143
[20] Glasserman, P. (2004). Monte Carlo Methods in Financial Engineering. Springer. https://link.springer.com/book/10.1007/978-0-387-21617-1
[21] Corsi, F. (2009). “A Simple Approximate Long-Memory Model of Realized Volatility.” Journal of Financial Econometrics, 7(2), 174-196. https://doi.org/10.1093/jjfinec/nbp001
| Asset | Role in v1 | Why included |
| --- | --- | --- |
| BTC | Baseline asset | Most natural starting point for the original engine and usually the main crypto binary market. |
| ETH | First expansion | Separate contract universe with its own path behavior, volatility, liquidity, and ETF/options context. |
| SOL | Future research asset | Worth collecting, but should be promoted only after data quality, liquidity, and calibration are proven. |
| Formula or output | Origin | Role in this paper |
| --- | --- | --- |
| Binary payoff and option-like framing | Standard digital/cash-or-nothing payoff logic from option-pricing theory [18][19]. | Defines why the contract can be treated as an option-like binary payoff. |
| p_finish and p_no_touch Monte Carlo estimators | Standard Monte Carlo sample-average estimator applied to terminal and path indicators [20]. | Main estimator: count simulated paths that finish correctly or survive the danger line. |
| z_path | Project-defined normalization using log price distance and expected remaining movement. | Makes threshold distance comparable across price levels and volatility regimes. |
| sigma_tau | Project-defined realized-volatility blend, inspired by multi-horizon realized-volatility forecasting such as HAR-RV [21]. | Sets the expected remaining movement scale; weights, floor, and regime multiplier are design choices. |
| Executable edge and required edge | Project-defined decision accounting built from binary expected value minus executable price, costs, and buffers. | Separates fair probability from tradability. |
| Target-size VWAP / executable price | Depth-weighted average fill calculation applied to Polymarket order-book levels [3][4]. | Prevents the model from pretending midpoint prices are executable. |
| congestion_K_L | Project-defined threshold-instability feature. | Measures the fraction of a lookback window spent near the contract threshold. |
| Generator | Purpose | First-pass use |
| --- | --- | --- |
| G1: empirical conditional prior | Sample historical same-asset, same-horizon, similar-state path fragments from data available before the decision time. | Primary estimate because it preserves real crypto wicks, jumps, and path shape. |
| G2: moving or stationary block bootstrap | Resample blocks of short returns to preserve dependence in time-series data. | Challenger and uncertainty source. |
| G3: filtered historical simulation | Normalize historical residuals by realized volatility, then rescale to current sigma_tau. | Useful when current volatility differs from the historical prior bucket. |
| G4: stress overlays | Add final-window wicks, source-disagreement, or news-window shocks. | Risk overlay, not central fair value unless validated. |
| Question | Test |
| --- | --- |
| How many paths are enough near a trade? | Compare 1,000, 5,000, 10,000, and cached estimates against live shadow calibration. |
| How should fragments be selected? | Compare same asset only, same horizon, same volatility regime, and same wick regime. |
| Should fragments be scaled? | Compare raw fragments vs sigma_tau-scaled fragments. |
| How should final seconds be handled? | Test final-window overlays and separate final-30-second buckets. |
| What happens during macro news? | Compare event-window vs non-event-window path libraries. |
| Does ETH need a separate path library? | Yes by default; test cross-asset pooling only as future research. |
| What if the bucket is sparse? | Increase uncertainty, fall back to coarser bucket, or block. |
| How should Chainlink/proxy disagreement be handled? | Add source buffer or block, then validate against final labels. |
| Do multiple generators agree? | Track generator dispersion and edge sensitivity. |
| How should the prior be built? | Compare strict conditional buckets, coarser fallback buckets, and sigma-scaled fragments. |
| Trigger | Action |
| --- | --- |
| New contract appears | Initialize relevant asset/side/horizon grid. |
| Time bucket changes | Move to nearest cached bucket or refresh if missing. |
| z_path bucket changes | Interpolate or refresh near entry boundary. |
| Volatility regime changes | Refresh because path distribution changed. |
| News/event flag changes | Demand more edge; refresh if event-window generator is enabled. |
| Source disagreement spike | Block or refresh with source-risk overlay. |
| Near-entry state | Use higher path count before real trade eligibility. |
| Cache stale | Refresh or block. |
| Decision | Meaning |
| --- | --- |
| Trade | Edge after costs exceeds required edge, path and execution gates pass, portfolio size is allowed, and blockers do not reject. |
| Wait | Direction is interesting, but timing, path stability, quote quality, news window, or price level is not clean enough yet. |
| Block | A hard gate fails: stale data, source mismatch, thin depth, high latency, bad rule parse, excessive uncertainty, kill switch, or high false-positive risk. |
| Demand more edge | Trade might be valid only at a better price because uncertainty, book-crossing cost, path risk, news risk, or execution risk is elevated. |