# Research Worthiness Memo

Date: 2026-05-28

Question: are the core ideas behind the remaining-path probability engine worth
building?

## Short Verdict

Yes, but the edge is narrower than the broad idea.

The strongest idea is not "predict BTC/ETH/SOL direction." The stronger idea is
to price short-dated binary contracts as path-dependent digital options using
settlement-source price, remaining time, realized volatility, executable venue
bid/ask, and no-touch/reversal risk.

The literature supports the core math and the market-structure logic:

- binary prediction contracts can be interpreted as probability-like claims, but
  prices are not always calibrated probabilities;
- short-dated crypto prediction markets are close enough to digital options that
  derivatives pricing tools apply directly;
- realized-volatility models are a legitimate way to forecast near-term
  dispersion;
- barrier/no-touch logic is the right mathematical family for "will it avoid
  crossing back against me";
- market making requires inventory, latency, and adverse-selection controls;
- options order flow can contain information, but the ETF-options lane should be
  treated as an out-of-sample-tested context feature, not a direct trigger.

The project is worth doing if the first milestone is a calibration engine and
paper-trading ledger, not immediate live trading.

## Thesis By Component

| Idea | Research Support | Worth Building? | Caveat |
| --- | --- | --- | --- |
| Remaining-path probability for binaries | Strong | Yes | Must calibrate on actual venue outcomes, not textbook assumptions |
| Use spot/oracle/vol to price crypto binaries | Strong | Yes | Settlement-source price matters more than generic exchange spot |
| `p_no_touch` / reversal-risk model | Strong | Yes | Jumps and microstructure noise will break naive Brownian assumptions |
| Volatility-decreasing entry filter | Medium-strong | Yes | Need robust realized-vol windows; avoid overfitting thresholds |
| Avoid support/resistance | Medium | Yes, as a risk filter | Not strong enough as primary alpha |
| Market making / fast cancel-reprice | Strong conceptually | Yes, but later gated | Adverse selection is the main danger |
| Multi-venue adapter | Strong practical case | Yes | Venue rules and legal access differ sharply |
| BTC ETF options/orderflow as context | Plausible, weaker direct evidence | Yes, research lane only | ETF options are new; direct BTC binary evidence is thin |

## Source Map

### Prediction Market Prices Are Useful But Imperfect Probabilities

Wolfers and Zitzewitz (2004) summarize evidence that prediction markets aggregate
dispersed information and often outperform benchmark forecasts, but market
design matters. In a later NBER paper, Wolfers and Zitzewitz (2006) argue that
prices usually approximate mean beliefs under broad conditions, while Manski
(2004/2006) warns that a prediction-market price does not mechanically equal a
clean physical probability under heterogeneous beliefs.

Design implication: the system should not blindly trust venue probabilities.
It should estimate its own probability and measure calibration against actual
outcomes.

Sources:

- Wolfers & Zitzewitz, "Prediction Markets," Journal of Economic Perspectives,
  2004: https://www.aeaweb.org/articles?id=10.1257/0895330041371321
- Wolfers & Zitzewitz, "Interpreting Prediction Market Prices as
  Probabilities," NBER, 2006: https://www.nber.org/papers/w12200
- Manski, "Interpreting the Predictions of Prediction Markets," NBER/Economics
  Letters: https://www.nber.org/papers/w10359
- Page & Clemen, "Do Prediction Markets Produce Well-Calibrated Probability
  Forecasts?", Economic Journal, 2013:
  https://scholars.duke.edu/display/pub765630

### Crypto Prediction Markets Look Like Digital Options

Recent working papers are directly on-point. Lee, Lee, and Lee (2026) study
BTC/ETH contracts on Kalshi and Polymarket as cash-or-nothing digital options,
extracting implied volatility surfaces, risk-neutral densities, and variance
risk premia. They report systematic underconfidence and stronger distortion on
Polymarket. Fabi, Schonleber, Ruffo, and Marfe (2026) compare Polymarket
BTC/ETH probabilities against option-implied benchmarks and find broad tracking
with systematic deviations, especially in tails, high-volatility periods, and
macro shocks.

These are working papers, not settled peer-reviewed literature, but they line up
almost exactly with this project's core idea.

Design implication: build the probability engine as a derivatives-pricing and
calibration system, not a generic ML classifier.

Sources:

- Lee, Lee & Lee, "Cryptocurrency Prediction Markets through the Derivatives
  Lens: Evidence from Kalshi and Polymarket," SSRN, 2026:
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6748186
- Fabi, Schonleber, Ruffo & Marfe, "Market Efficiency in Prediction Markets - A
  Comparison with Derivatives," SSRN, 2026:
  https://ssrn.com/abstract=6565258

### Polymarket Microstructure Has Real Frictions

Dubach (2026) studies Polymarket's public order-book feed and on-chain trade
record at tick scale. The key warning for this project is that inferred trade
direction from the public feed is noisy, and effective spread / Kyle lambda
measurements can change materially depending on whether direction is inferred
from feed data or on-chain fills.

Design implication: record executable bid/ask/depth and actual fill data. Do
not infer too much from displayed midpoint or public-feed trade direction.

Source:

- Dubach, "The Anatomy of a Decentralized Prediction Market: Microstructure
  Evidence from the Polymarket Order Book," arXiv, 2026:
  https://arxiv.org/abs/2604.24366

### Barrier / No-Touch Math Is The Right Family

The user's "critical time when it will not go against you" maps to first-passage
and barrier-option math. Reiner and Rubinstein's barrier-option work is the
standard finance reference family; reflection-principle results are the
probability-theory base for crossing/no-crossing calculations.

Design implication: the engine should output both terminal probability
(`p_finish`) and path-survival probability (`p_no_touch`). `p_no_touch` is the
better trade-safety metric for this strategy.

Sources:

- Reiner & Rubinstein, "Breaking Down the Barriers," Risk, 1991:
  https://digicoll.lib.berkeley.edu/record/86304/files/b120984374_C044481811.pdf
- "Generalizing the reflection principle of Brownian motion, and closed-form
  pricing of barrier options and autocallable investments," North American
  Journal of Economics and Finance, 2019:
  https://www.sciencedirect.com/science/article/pii/S1062940818306028
- "Multi-step Reflection Principle and Barrier Options," arXiv, 2021:
  https://arxiv.org/abs/2105.15008

### Realized Volatility Is A Legitimate Input

Andersen, Bollerslev, Diebold, and Labys (2003) are a core realized-volatility
reference. Corsi's HAR-RV model supports the intuition that volatility over
multiple horizons matters. This maps well to the proposed `rv_10s`, `rv_30s`,
`rv_120s`, and `rv_300s` stack.

Design implication: use multi-horizon realized-volatility features from the
start. The simple "vol decreasing" filter should be expressed as a feature and
calibration report, not hand-waved as a rule forever.

Sources:

- Andersen, Bollerslev, Diebold & Labys, "Modeling and Forecasting Realized
  Volatility," Econometrica, 2003:
  https://ideas.repec.org/a/ecm/emetrp/v71y2003i2p579-625.html
- Corsi, "A Simple Approximate Long-Memory Model of Realized Volatility,"
  Journal of Financial Econometrics, 2009:
  https://academic.oup.com/jfec/article-pdf/7/2/174/2543795/nbp001.pdf
- Barndorff-Nielsen & Shephard, "Econometric analysis of realised volatility
  and its use in estimating stochastic volatility models," JRSS-B, 2002:
  https://pure.au.dk/portal/en/publications/econometric-analysis-of-realised-volatility-and-its-use-in-estima

### Market Making Requires Adverse-Selection Controls

Glosten and Milgrom (1985) give the classic information-based spread model:
spreads compensate market makers for trading against better-informed traders.
Avellaneda and Stoikov (2008) give the classic inventory-aware limit-order-book
market-making framework. Their lesson is not "just quote both sides." The lesson
is quote only when spread, fill risk, inventory, and adverse selection are
controlled.

Design implication: speed is necessary but not sufficient. The architecture
should optimize cancel/reprice and risk decisions, but the strategy should avoid
quoting stale prices when the underlying distribution changes.

Sources:

- Glosten & Milgrom, "Bid, Ask, and Transaction Prices in a Specialist Market
  with Heterogeneously Informed Traders," Journal of Financial Economics, 1985:
  https://business.columbia.edu/faculty/research/bid-ask-and-transaction-prices-specialist-market-heterogeneously-informed-traders
- Avellaneda & Stoikov, "High-frequency trading in a limit order book,"
  Quantitative Finance, 2008:
  https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf
- Cont, Stoikov & Talreja, "A Stochastic Model for Order Book Dynamics," SSRN,
  2008: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1273160

### Support / Resistance Has Enough Evidence For A Filter, Not A Thesis

Lo, Mamaysky, and Wang (2000) show that technical patterns can be defined and
tested computationally, and that some patterns have incremental information in
historical data. Brock, Lakonishok, and LeBaron (1992) find evidence for simple
technical rules in older equity-index data. More recent work is mixed and
context-specific.

Design implication: support/resistance should block fragile trades, not create
standalone alpha. This is exactly how the current design uses it.

Sources:

- Lo, Mamaysky & Wang, "Foundations of Technical Analysis," Journal of Finance,
  2000: https://www.mit.edu/people/wangj/pap/LoMamayskyWang00.pdf
- Brock, Lakonishok & LeBaron, "Simple Technical Trading Rules and the
  Stochastic Properties of Stock Returns," Journal of Finance, 1992:
  https://ideas.repec.org/a/bla/jfinan/v47y1992i5p1731-64.html
- "Evidence and Behaviour of Support and Resistance Levels in Financial Time
  Series," arXiv, 2021: https://arxiv.org/abs/2101.07410

### Options Flow Can Contain Information, But Crypto/ETF Translation Is A Test

Classic options-market papers support the idea that options volume and relative
option/equity volume contain information about future returns or future news.
Bitcoin-options evidence is more nuanced: Alexander et al. (2023) find bitcoin
option order imbalance has strong volatility information and some directional
information, but bitcoin options differ from equity index options.

BTC ETF options are now liquid enough to investigate. Nasdaq reported IBIT
options quickly became one of the most active ETF option underliers after
listing. But the direct question "does BTC ETF options flow predict 5m/15m BTC
binary outcomes?" is not settled academically.

Design implication: build the ETF-options lane as a context adapter and
out-of-sample feature test. Do not let it directly drive orders until it proves
incremental calibration value.

Sources:

- Easley, O'Hara & Srinivas, "Option Volume and Stock Prices: Evidence on Where
  Informed Traders Trade," Journal of Finance, 1998:
  https://www.semanticscholar.org/paper/a2555661d05c49627b833be271fe804e7327855a
- Pan & Poteshman, "The Information in Option Volume for Future Stock Prices,"
  Review of Financial Studies, 2006:
  https://www.mit.edu/~junpan/volume.pdf
- Johnson & So, "The Option to Stock Volume Ratio and Future Returns," Journal
  of Financial Economics, 2012:
  https://eso.scripts.mit.edu/docs/The-option-to-stock-volume.pdf
- Alexander, Deng, Feng & Wan, "Net buying pressure and the information in
  bitcoin option trades," Journal of Financial Markets, 2023:
  https://www.sciencedirect.com/science/article/pii/S1386418122000544
- Nasdaq, "Nasdaq Listed IBIT Options End First Day in Top 1% of all Options
  Products Traded," 2024:
  https://www.nasdaq.com/newsroom/nasdaq-listed-ibit-options-end-first-day-top-1-all-options-products-traded

## Current Venue/API Reality

The venue architecture should stay multi-source:

- Polymarket has public market data endpoints for events, markets, prices,
  books, spreads, historical prices, trades, holders, and open interest. Its
  CLOB uses off-chain matching with on-chain settlement.
- Jupiter's Prediction API is beta and exposes events, markets, orders,
  positions, and history. It can aggregate liquidity from Polymarket and Kalshi,
  and it uses unsigned transactions that the user signs and submits.

Design implication: venue adapters should normalize market metadata, order book
state, fees, settlement rules, order intent, and fills. Do not let the core
probability engine depend on a single venue's raw API shape.

Sources:

- Polymarket trading overview: https://docs.polymarket.com/trading/overview
- Polymarket market data overview:
  https://docs.polymarket.com/market-data/overview
- Jupiter Prediction API guide:
  https://developers.jup.ag/docs/guides/how-to-build-a-prediction-market-app-on-solana

## What The Research Says To Build

### 1. Core Probability Engine

Build:

- `p_finish`
- `p_no_touch`
- `z_path = distance_from_line / expected_remaining_move`
- jump/volatility flags
- calibration by horizon, venue, asset, spread, and time-left bucket

This is the most defensible part of the project.

### 2. Multi-Horizon Volatility Engine

Build:

- realized volatility at multiple horizons;
- microstructure-noise handling;
- volatility trend features;
- jump and range-expansion flags.

Do not rely on one volatility number.

### 3. Executable-Price Scanner

Build:

- best bid/ask/depth capture;
- spread-adjusted fair value;
- fee-adjusted edge;
- slippage and queue-risk assumptions;
- actual fill replay when available.

Midpoint edge does not count.

### 4. Support/Resistance Filter

Build as a blocking and labeling system:

- recent swing highs/lows;
- session high/low;
- VWAP bands;
- round-number proximity;
- failed-break / acceptance flags.

Log blocked signals. This gives a dataset to test whether the filter helps.

### 5. ETF Options Context Adapter

Build as a separate lane:

- IBIT/FBTC/ARKB/BITB chain snapshots;
- top-N OI contract selection;
- IV/skew/OI/volume/flow features;
- quality flags for derived vs direct trade tape;
- incremental calibration tests.

Do not entangle it with the core binary model until it proves value.

### 6. Compiled Hot Core

A compiled core is justified if it owns:

- rolling volatility state;
- probability calculations;
- risk checks;
- cancel/reprice decisions;
- order-intent generation.

But C++/Rust will not solve venue latency. The system still needs telemetry
around feed delay, order round-trip time, fill probability, and stale quote risk.

## What Would Kill The Project

The project should stop or pivot if these are true after enough logged windows:

1. The model has worse Brier score or log loss than venue executable prices.
2. Apparent edge exists only against midpoint, not bid/ask.
3. Edge disappears after fees, spread, slippage, and stale-feed penalties.
4. `p_no_touch` is badly miscalibrated near final resolution.
5. Support/resistance filter removes winners and keeps losers.
6. ETF options context adds no out-of-sample calibration improvement.
7. Live venue latency is too variable to cancel/reprice safely.

## Recommended First Research Milestone

Before live execution, collect at least:

- 5m and 15m BTC/ETH/SOL binary markets across target venues;
- settlement-source price at tick or sub-second cadence where available;
- venue bid/ask/depth snapshots;
- realized-volatility windows;
- support/resistance labels;
- ETF options context for BTC only;
- final outcome and max adverse excursion.

Minimum reports:

- calibration curve for `p_finish`;
- calibration curve for `p_no_touch`;
- Brier/log-loss versus venue price;
- edge-after-costs distribution;
- false-positive analysis near support/resistance;
- fill simulation using executable bid/ask, not midpoint;
- ablation: core model vs core+structure filter vs core+ETF options context.

## Bottom Line

This is worth doing as a serious research system.

The highest-confidence path is:

1. remaining-path probability engine;
2. executable-price mispricing scanner;
3. calibration reports;
4. support/resistance risk filter;
5. ETF options context as an add-on feature lane;
6. only then supervised live execution.

The low-confidence path is:

- "fast bot that trades every apparent edge";
- pure market making without adverse-selection controls;
- using ETF options orderflow as a direct BTC binary signal before it proves
  incremental value.

The idea survives the academic research pass, but the project lives or dies on
calibration, executable prices, and latency telemetry.

