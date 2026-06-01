# BTC/ETH Binary Contract Engine Build Plan

This plan preserves and reorganizes the implementation material from `polymarket idea.docx` as of 2026-05-31. The companion paper should stay focused on the research argument: contract definition, as-of data, probability outputs, Monte Carlo methodology, executable edge, and validation. This plan carries the build details that are useful but too operational for the main paper.

## Source Preservation Rule

No implementation idea from the paper is intentionally discarded. The build sections below reorganize the material into engineering workstreams. The full extracted source is preserved at the end of this file as a source appendix so future edits can recover any wording that was moved out of the paper.

## Scope

The first tradable universe is BTC and ETH short-dated binary contracts. SOL remains a future extension. The first operating mode is read-only shadow logging; live capital is gated behind validation, calibration, execution-quality checks, and operational safety controls.

## Build Sections

### 1. Contract Rules and Settlement Source

Purpose: parse each venue-defined binary contract exactly as written. The engine must store the asset, side, threshold or start-price rule, expiry, settlement source, comparison operator, market id, token ids, and rule hash. It should reject ambiguous rule text instead of guessing.

Outputs:
- normalized contract object
- settlement-source hierarchy
- rule-parser test suite
- explicit edge-case list for greater-than, greater-than-or-equal, less-than, less-than-or-equal, missing settlement feed, stale source, and unsupported market rules

### 2. BTC/ETH Data and As-Of State Construction

Purpose: reconstruct what the model could see at every decision timestamp. Historical future movement is a label, not a feature. Live mode should collect settlement-source prices, proxy prices, order-book snapshots, WebSocket events, source-quality flags, and market metadata.

The first live collector should track only the current and next BTC/ETH 5-minute contracts. This keeps the order-book set small: BTC current, BTC next, ETH current, and ETH next, each with UP and DOWN sides. Broader contract discovery can be added later after the first live replay path is stable.

State groups:
- contract state
- price state
- volatility state
- path-shape state
- order-book state
- event/news state
- model state
- risk state

### 3. Core Probability Engine

Purpose: compute the contract-level outputs used by the decision layer.

Core outputs:
- `p_finish`: terminal win probability
- `p_no_touch`: path-survival probability
- `z_path`: normalized cushion from the danger line
- `sigma_tau`: expected remaining movement scale
- executable edge after costs

Engineering rule: the live decision should not depend on one fragile formula. Monte Carlo is the primary estimator; closed-form formulas are debugging baselines.

### 4. Monte Carlo Path Generation

Purpose: estimate both terminal and path-sensitive probabilities from as-of state. The simulation must start from an explicit prior distribution: the engine's pre-expiry belief about realistic remaining BTC or ETH paths before the future is known.

First-pass prior:
- empirical conditional prior from historical BTC/ETH path fragments available before the decision time
- conditioned on asset, horizon, seconds left, volatility regime, distance from threshold, recent wick/cross behavior, and source-quality state
- updated by current live volatility through `sigma_tau`
- rejected or widened when the comparable historical bucket is sparse

Path generators:
- empirical same-asset fragments
- block bootstrap fragments
- filtered historical simulation
- stress overlays for event and final-window risk

The ensemble should expose disagreement. If generator outputs diverge, the system should increase uncertainty, demand more edge, or block.

### 5. Order Book and Execution Model

Purpose: compare model fair value against an executable price, not a midpoint. Entry should use the ask side or target-size ask VWAP; exit should use the bid side or target-size bid VWAP. Depth, quote age, book update rate, partial-fill risk, and latency are execution features or costs.

Execution modes:
- read-only shadow decision
- paper-trading simulation
- later target-size marketable limit order
- manual approval before live capital

### 6. Exit Strategy and Position Management

Purpose: separate entry logic from exit logic. Entry asks whether a new position has edge. Exit asks whether an existing position is still worth holding after updated probability, path risk, order-book friction, and remaining time.

Exit modes preserved from the paper:
- hold-to-expiry baseline
- noise-aware stop-and-exit
- stop-and-reverse challenger
- confirmation, hysteresis, and cooldown controls
- exit state machine
- exit-strategy test matrix

### 7. Decision Features, News Context, and Risk Gates

Purpose: let the engine say trade, wait, block, or demand more edge. A favorable `p_finish` is not enough if path survival is poor, source data is stale, execution is thin, or news/event risk is high.

Feature families:
- structure and support/resistance
- order-book and microstructure
- source quality
- news and event risk
- noise control and residual uncertainty policy
- mandatory hard gates

### 8. Portfolio Management and Position Sizing

Purpose: prevent individually attractive contracts from creating unsafe combined exposure. Sizing should start conservatively with expected value, binary payoff variance, fractional Kelly, hard caps, same-window limits, per-asset limits, and portfolio drawdown limits.

### 9. Validation and Ablation

Purpose: prove whether the methodology has edge without future leakage.

Validation requirements:
- as-of replay only
- executable prices, not midpoint-only scoring
- separate BTC and ETH results
- calibration checks
- ablations for path survival, volatility state, order-book features, event/news features, and XGBoost blocker
- pass/fail thresholds before live trading is considered

### 10. Live Shadow Logger, Database, and Dashboard

Purpose: collect the raw evidence needed before capital is placed at risk.

Core tables:
- contracts
- order book snapshots
- price snapshots
- model decisions
- path-generator outputs
- labels after expiry
- incidents and kill-switch events

Dashboard purpose: show current contracts, model probability, executable edge, risk gates, source quality, and decision history.

### 11. Failure Modes and Operational Safety

Purpose: stop the system from trading when data, execution, model behavior, or operator control is unsafe.

Safety controls:
- soft kill
- hard kill
- manual kill switch
- wallet/key/treasury separation before live trading
- exit-storm freeze rule
- Docker/VPS migration checklist before remote deployment
- incident runbook
- alert thresholds

### 12. XGBoost Challenger and Calibration Protocol

Purpose: keep machine learning behind the transparent engine at first. XGBoost should begin as a challenger, blocker, or calibration layer after enough clean labeled decision snapshots exist.

Allowed targets:
- calibrated `p_finish`
- calibrated `p_no_touch`
- block/allow classifier
- edge degradation predictor

Banned features:
- future BTC/ETH movement
- final settlement
- later Polymarket prices
- labels created after the decision timestamp

Promotion rule: XGBoost cannot become live authority until it improves out-of-sample calibration, drawdown, and execution-adjusted expectancy without hiding leakage.

### 13. Implementation Roadmap

Immediate order:
1. rule parser and contract normalizer
2. BTC/ETH data ingestion and as-of state builder
3. read-only shadow logger
4. Monte Carlo path generator
5. probability and edge outputs
6. validation harness
7. operator dashboard
8. Docker/VPS deployment hardening for read-only collection
9. XGBoost challenger after labeled data exists
10. paper trading
11. manual live pilot only after explicit approval

### 14. Open Research and Build Questions

- Which settlement-source proxy is acceptable when official venue settlement history is unavailable?
- How often should probability grids refresh near expiry?
- How should path-generator weights be fixed before testing?
- How should final-window wick risk be estimated without overfitting?
- Which event/news sources are reliable enough to become gates?
- What pass/fail standard is strict enough before real money?

---

## Build Slice: Sections 1-3 Bridge Completion

This slice completes the bridge before probability modeling:

- contract rules become side-level `ContractSpec` rows;
- normalized price and order-book observations can be written to DuckDB;
- `DecisionState` joins contract, price, volatility placeholder, and order-book state;
- replay queries select only rows with timestamps `<= asof_ts`;
- future settlement, later BTC/ETH movement, final labels, and later Polymarket quotes remain labels only;
- retention metadata is recorded for raw partitions, but automatic deletion is not enabled.

Retention defaults:

- keep contract rules, rule hashes, decision states, labels, daily/hourly metrics, incident logs, and kill-switch logs forever;
- keep raw tick/event data hot for 90 days if disk allows;
- after the hot window, prefer aggregation/archive over deletion;
- never delete without a retention manifest containing source, stream, partition, row count, sha256, first/last timestamp, retention class, and archive/delete timestamp.

Deployment boundary:

- `collect` mode starts live collection;
- `paper` mode can later start live data plus simulated decisions/orders;
- `live` mode later requires explicit mode selection, valid keys, kill-switch health, clock health, disk health, monitoring health, and an armed confirmation;
- keys existing must not arm live trading by itself.

---

## Concrete Implementation Blueprint

The sections above describe what the system needs. This section describes how the system should build those pieces in the current repository.

### Implementation Principle

The first version should be a read-only research engine. It should not place orders. It should collect contracts, prices, order books, and model decisions; reconstruct the model state at each timestamp; calculate probabilities; log the decision; and score the result only after expiry.

Python should own orchestration, ingestion, storage, feature construction, research notebooks, and the FastAPI service. C++ should remain reserved for the hot probability loop only after the Python version is correct and too slow. The system should be built so every calculation can be replayed from stored snapshots.

### Proposed Package Layout

Use the existing repo root `/Users/goon/polymarket`.

```text
src/polymarket_engine/
  app.py
  config.py
  domain/
    contracts.py
    market_state.py
    decisions.py
  venues/
    polymarket.py
    jupiter.py
  ingestion/
    price_feed.py
    order_book.py
    contract_sync.py
  storage/
    duckdb_store.py
    schema.sql
  features/
    state_builder.py
    volatility.py
    path_features.py
    orderbook_features.py
  probability/
    monte_carlo.py
    path_generators.py
    probability_outputs.py
    closed_form.py
  decision/
    edge.py
    gates.py
    sizing.py
  validation/
    replay.py
    labels.py
    metrics.py
    ablation.py
  reporting/
    model_report.py
```

Test files should mirror this layout:

```text
tests/domain/
tests/ingestion/
tests/features/
tests/probability/
tests/decision/
tests/validation/
```

### 1. Contract Rules and Settlement Source: How It Builds

Create `src/polymarket_engine/domain/contracts.py`.

This file defines the normalized contract object. Every venue adapter must output this shape before the rest of the engine is allowed to touch the market.

```python
class ContractSpec(BaseModel):
    venue: str
    market_id: str
    token_id: str | None
    asset: Literal["BTC", "ETH", "SOL"]
    side: Literal["UP", "DOWN"]
    threshold_type: Literal["fixed_price", "start_price"]
    threshold_price: float | None
    comparison: Literal[">", ">=", "<", "<="]
    start_time: datetime
    expiry_time: datetime
    settlement_source: str
    rule_text: str
    rule_hash: str
```

Build method:

1. `venues/polymarket.py` fetches market metadata and raw rule text.
2. `contract_sync.py` sends raw market data into a parser.
3. The parser extracts asset, side, expiry, threshold rule, settlement source, and comparison operator.
4. The parser hashes the raw rule text and stores it with the normalized contract.
5. If the parser cannot prove the rule, it marks the contract as unsupported.

The engine must not silently assume that every UP contract means `S_T > K`. Some markets compare the end price to the start price, and some may use `>=` instead of `>`. That rule has to be explicit.

Tests:

- `tests/domain/test_contracts.py` should verify BTC UP, BTC DOWN, ETH UP, ETH DOWN, fixed-threshold contracts, start-price contracts, and unsupported ambiguous rule text.
- A test should prove unsupported markets are rejected before probability calculation.

### 2. Data Ingestion and As-Of State: How It Builds

Create `src/polymarket_engine/domain/market_state.py`.

This file defines the snapshot the model is allowed to see at decision time.

```python
class DecisionState(BaseModel):
    decision_time: datetime
    contract: ContractSpec
    seconds_left: float
    settlement_price: float
    proxy_prices: dict[str, float]
    best_bid: float | None
    best_ask: float | None
    bid_size: float | None
    ask_size: float | None
    book_age_ms: int | None
    source_age_ms: int | None
    source_disagreement_bps: float | None
    realized_returns: list[float]
    data_quality_flags: list[str]
```

Build method:

1. `ingestion/price_feed.py` collects BTC and ETH prices from the settlement source or the closest validated proxy.
2. `ingestion/order_book.py` collects best bid, best ask, depth, quote age, and update time.
3. `features/state_builder.py` joins the contract, price, volatility window, and order-book snapshot into one `DecisionState`.
4. `storage/duckdb_store.py` writes the raw observations and the final `DecisionState` to DuckDB.

The important rule is timestamp discipline. If the decision timestamp is `t`, the state builder can only use records with timestamps `<= t`. The final settlement and later price movement are not allowed into `DecisionState`.

Tests:

- `tests/features/test_state_builder.py` should create fake price records before and after `t` and prove the builder excludes future records.
- A test should prove stale settlement data adds a `stale_source` flag.
- A test should prove source disagreement adds a `source_disagreement` flag.

### 3. Storage: How It Builds

Create `src/polymarket_engine/storage/schema.sql`.

The first storage layer should use DuckDB because the project is research-heavy and needs local replay more than production distribution.

Minimum tables:

```sql
contracts(
  venue TEXT,
  market_id TEXT,
  token_id TEXT,
  asset TEXT,
  side TEXT,
  threshold_type TEXT,
  threshold_price DOUBLE,
  comparison TEXT,
  start_time TIMESTAMP,
  expiry_time TIMESTAMP,
  settlement_source TEXT,
  rule_text TEXT,
  rule_hash TEXT
);

price_ticks(
  source TEXT,
  asset TEXT,
  ts TIMESTAMP,
  price DOUBLE,
  ingest_ts TIMESTAMP
);

orderbook_snapshots(
  venue TEXT,
  market_id TEXT,
  token_id TEXT,
  ts TIMESTAMP,
  best_bid DOUBLE,
  best_ask DOUBLE,
  bid_size DOUBLE,
  ask_size DOUBLE,
  book_age_ms INTEGER
);

decision_snapshots(
  decision_id TEXT,
  ts TIMESTAMP,
  market_id TEXT,
  token_id TEXT,
  state_json JSON,
  model_json JSON,
  decision TEXT
);

labels(
  decision_id TEXT,
  expiry_time TIMESTAMP,
  settlement_price DOUBLE,
  did_finish_win BOOLEAN,
  did_no_touch BOOLEAN,
  realized_edge DOUBLE
);
```

Build method:

1. Raw ingestion writes to `contracts`, `price_ticks`, and `orderbook_snapshots`.
2. The state builder writes one `decision_snapshots` row per evaluated contract per decision timestamp.
3. The labeler writes to `labels` only after expiry.
4. Backtests join `decision_snapshots` to `labels` by `decision_id`.

Tests:

- `tests/storage/test_duckdb_store.py` should create a temp DuckDB database, apply `schema.sql`, insert one fake contract, one price tick, one order-book snapshot, one decision, and one label.

### 4. Volatility and `sigma_tau`: How It Builds
Implementation status: this section is implemented by `src/polymarket_engine/features/volatility.py`, replayed through `src/polymarket_engine/features/state_replay.py`, and covered by `tests/features/test_volatility.py` plus `tests/storage/test_state_replay.py`. The implementation is as-of safe: future ticks are labels or ignored, never volatility inputs.

Source rule: BTC/ETH volatility and `sigma_tau` are calculated from the Chainlink settlement-reference stream only, stored as `polymarket_rtds_chainlink`. Coinbase, Binance, and other exchange feeds are proxy/quality-check inputs, not volatility inputs. If a historical proxy point exactly matches the Chainlink point, it can be used as validation evidence, but it is not added as an extra return observation because duplicate rows can distort realized-volatility windows.

Create `src/polymarket_engine/features/volatility.py`.

This module turns recent log returns into the movement scale used by Monte Carlo.

Build method:

1. Select only Chainlink settlement-reference prices for the contract asset.
2. Convert those prices into log returns: `r_t = ln(S_t / S_{t-1})`.
3. Compute short, medium, and long realized-volatility windows.
4. Blend them using preset weights.
5. Apply a minimum volatility floor.
6. Apply a regime multiplier when volatility is expanding or contracting.
7. Scale the result to seconds left.

First-pass function:

```python
def estimate_sigma_tau(
    returns: Sequence[float],
    seconds_left: float,
    short_window: int = 20,
    medium_window: int = 60,
    long_window: int = 180,
    weights: tuple[float, float, float] = (0.50, 0.30, 0.20),
    sigma_floor: float = 0.00005,
    regime_multiplier: float = 1.0,
) -> float:
    ...
```

The exact numbers are starting defaults, not optimized truth. They should be stored in config and changed only through walk-forward validation.

Tests:

- `tests/features/test_volatility.py` should prove higher recent returns produce higher `sigma_tau`.
- A flat tape should still return at least `sigma_floor`.
- Missing Chainlink reference data should produce missing volatility, not fake confidence.
- Proxy feeds should be rejected or ignored for volatility construction.
- Weights must sum to one; invalid weights should raise a clear error.

### 5. Probability Outputs: How It Builds

Create `src/polymarket_engine/probability/probability_outputs.py`.

This module defines the model output object:

```python
class ProbabilityOutput(BaseModel):
    p_finish: float
    p_no_touch: float
    z_path: float
    sigma_tau: float
    generator_name: str
    sample_size: int
    uncertainty: float
```

Build method:

1. Compute side-adjusted log distance:
   - UP: `d_side = ln(S_t / K)`
   - DOWN: `d_side = ln(K / S_t)`
2. Compute `z_path = d_side / sigma_tau`.
3. Run Monte Carlo path generators.
4. Count simulated terminal wins to estimate `p_finish`.
5. Count paths that never cross the danger line to estimate `p_no_touch`.
6. Save the full output into `decision_snapshots.model_json`.

Tests:

- `tests/probability/test_probability_outputs.py` should prove `z_path` is positive when the contract is on the favorable side.
- A contract already on the wrong side should produce negative or weak `z_path`.
- Monte Carlo counts should produce probabilities between 0 and 1.

### 6. Monte Carlo Path Generation: How It Builds

Create `src/polymarket_engine/probability/path_generators.py`.

The first version should not try to invent a perfect stochastic process. It should use several simple generators and compare them.

Generator 1: empirical fragments.

- Find historical BTC or ETH fragments with similar seconds-left bucket, volatility bucket, and `z_path` bucket.
- Replay the next `tau` seconds from those historical fragments as candidate futures.
- This preserves real wicks and jumps but may have sparse matches.

Generator 2: block bootstrap.

- Sample short blocks of recent log returns.
- Stitch blocks until the simulated path reaches expiry.
- This preserves local volatility clustering better than independent random draws.

Generator 3: filtered historical simulation.

- Sample historical returns from the same asset and similar volatility state.
- Rescale them to current `sigma_tau`.
- This gives more samples when exact fragments are sparse.

Generator 4: stress overlay.

- Take another generated path and inject final-window wick or event shock scenarios.
- This should not dominate the central estimate; it should inform uncertainty and required edge.

Monte Carlo output:

```python
class PathSimulationResult(BaseModel):
    paths: list[list[float]]
    terminal_prices: list[float]
    terminal_wins: list[bool]
    no_touch_survivals: list[bool]
    max_adverse_excursions: list[float]
```

Tests:

- Paths must start from the current settlement price.
- Paths must have timestamps or step counts matching `seconds_left`.
- UP no-touch should fail if any path goes below or equal to the threshold, depending on rule comparison.
- DOWN no-touch should fail if any path goes above or equal to the threshold, depending on rule comparison.

### 7. Decision Layer and Risk Gates: How It Builds

Create `src/polymarket_engine/decision/edge.py`, `src/polymarket_engine/decision/gates.py`, and later `src/polymarket_engine/decision/noise.py` once live shadow data is available.

`edge.py` converts probabilities into tradability:

```python
edge_after_costs = p_decision - executable_price - execution_cost - uncertainty_buffer
```

`gates.py` decides whether to trade, wait, block, or demand more edge.

First decision enum:

```python
Decision = Literal["TRADE", "WAIT", "BLOCK", "DEMAND_MORE_EDGE"]
```

Build method:

1. Use `p_finish` as the fair-value anchor.
2. Penalize or block when `p_no_touch` is weak.
3. Penalize or block when `z_path` is too close to zero.
4. Penalize or block stale source data, stale order book, wide spread, thin depth, source disagreement, or event risk.
5. Remove, reconcile, or confirm noisy inputs before they reach the decision. Treat unresolved noise as wait, lower-confidence replay, or hard block.
6. Compare `edge_after_costs` against required edge.
7. Emit a decision with explicit reasons.

Decision output:

```python
class DecisionRecord(BaseModel):
    decision_id: str
    decision_time: datetime
    contract: ContractSpec
    probability: ProbabilityOutput
    executable_price: float
    edge_after_costs: float
    required_edge: float
    decision: Decision
    reasons: list[str]
```

Tests:

- Positive edge with clean gates should return `TRADE`.
- Positive edge with stale source should return `BLOCK`.
- Positive edge with weak `p_no_touch` should return `WAIT` or `DEMAND_MORE_EDGE`.
- Negative edge should not trade.

### 8. Entry Strategy and Decision Tree: How It Builds

Entry is the process of deciding whether a new binary position should be opened. It is not the same as calculating `p_finish`. The entry strategy starts with fair value, then applies path stability, execution quality, source quality, event risk, and portfolio limits.

Create `src/polymarket_engine/decision/entry.py`.

Entry input:

```python
class EntryCandidate(BaseModel):
    state: DecisionState
    probability: ProbabilityOutput
    executable_price: float
    target_size: float
    edge_after_costs: float
    required_edge: float
    hard_gate_failures: list[str]
    soft_warnings: list[str]
```

Entry decision tree:

```text
1. Is the contract rule supported?
   no  -> BLOCK
   yes -> continue

2. Is the settlement source fresh and accepted?
   no  -> BLOCK
   yes -> continue

3. Is the order book fresh enough and deep enough for target size?
   no  -> BLOCK or DEMAND_MORE_EDGE
   yes -> continue

4. Is p_finish high enough to create positive fair value?
   no  -> WAIT
   yes -> continue

5. Is p_no_touch/path stability acceptable?
   no  -> WAIT or DEMAND_MORE_EDGE
   yes -> continue

6. Is z_path far enough from the danger line?
   no  -> WAIT or DEMAND_MORE_EDGE
   yes -> continue

7. Is edge_after_costs greater than required_edge?
   no  -> WAIT
   yes -> continue

8. Do portfolio caps and same-window exposure limits pass?
   no  -> BLOCK or SIZE_DOWN
   yes -> TRADE in paper mode / log eligible entry in read-only mode
```

The first implementation should not place a live order. It should emit an `EntryDecision` and log the exact reasons.

```python
class EntryDecision(BaseModel):
    decision_id: str
    action: Literal["TRADE", "WAIT", "BLOCK", "DEMAND_MORE_EDGE", "SIZE_DOWN"]
    candidate: EntryCandidate
    reasons: list[str]
    max_acceptable_price: float | None
    simulated_entry_vwap: float | None
```

Build method:

1. `edge.py` computes executable edge after costs.
2. `gates.py` returns hard failures and soft warnings.
3. `entry.py` walks the decision tree in fixed order.
4. The decision is stored in `decision_snapshots`.
5. In read-only mode, `TRADE` means “this would have been eligible,” not “send an order.”

Tests:

- Unsupported contract returns `BLOCK`.
- Stale settlement source returns `BLOCK`.
- Positive edge with weak `p_no_touch` returns `WAIT` or `DEMAND_MORE_EDGE`.
- Positive edge with fresh source, clean path, executable book, and portfolio capacity returns `TRADE`.
- Same candidate under read-only mode writes a decision but does not create an order.

### 9. Exit Strategy and Position Management: How It Builds

Exit is the process of deciding whether an already-open position should be closed before expiry. It must be more conservative than entry because exiting pays book-crossing cost and can turn a temporary noisy move into a realized loss.

Create `src/polymarket_engine/decision/exit.py`.

Exit state machine:

```text
FLAT
  No position is open.

ENTRY_CANDIDATE
  Entry edge exists, but gates are still being checked.

OPEN_NORMAL
  Position is open. Hold and continue logging.

OPEN_WATCH
  Raw exit warning exists, but confirmation is incomplete.

EXIT_PENDING
  Exit condition is confirmed and executable exit price is fresh.

EXITED
  Position was closed before expiry.
```

The default exit benchmark is hold-to-expiry. Every exit strategy must beat holding after costs before it can be promoted.

Exit strategy set:

1. Hold-to-expiry baseline.
   - Always tracked.
   - Used as the benchmark.

2. Noise-aware stop-and-exit.
   - Primary candidate.
   - Exit only if cost-adjusted exit value is better than cost-adjusted hold value by more than the exit buffer.

3. Stop-and-reverse challenger.
   - Research-only at first.
   - Allowed only if the opposite side independently passes entry gates and survives round-trip book-crossing cost.

Exit value comparison:

```text
V_exit = executable_exit_price - exit_cost
V_hold = updated_hold_probability - hold_uncertainty_buffer

Exit only if:
V_exit > V_hold + exit_hysteresis_buffer
```

Build method:

1. Track every hypothetical or paper position in `positions`.
2. On each new decision timestamp, recompute `p_finish`, `p_no_touch`, `z_path`, executable bid-side VWAP, and source-quality flags.
3. If path risk worsens, move from `OPEN_NORMAL` to `OPEN_WATCH`, not directly to exit.
4. Confirm exit only if source data is fresh, book data is fresh, exit VWAP is executable, and `V_exit` beats `V_hold` by the hysteresis buffer.
5. Log both exit result and hold-to-expiry counterfactual after settlement.

Exit output:

```python
class ExitDecision(BaseModel):
    position_id: str
    state_before: str
    state_after: str
    action: Literal["HOLD", "WATCH", "EXIT", "REVERSE_BLOCKED", "REVERSE_CANDIDATE"]
    v_exit: float | None
    v_hold: float
    exit_hysteresis_buffer: float
    reasons: list[str]
```

Tests:

- A one-tick threshold flicker moves position to `OPEN_WATCH`, not `EXIT_PENDING`.
- Stale bid-side exit price blocks exit.
- Exit occurs only when `V_exit > V_hold + buffer`.
- Stop-and-reverse is blocked if the opposite side does not pass independent entry gates.
- Labels record `exit_vs_hold_delta`, `premature_exit_rate`, `saved_loss_rate`, and `noise_exit`.

### 10. Validation and Backtesting: How It Builds

Create `src/polymarket_engine/validation/replay.py`, `labels.py`, `metrics.py`, and `ablation.py`.

Build method:

1. Replay historical contracts one decision timestamp at a time.
2. Build `DecisionState` using only data timestamped `<= t`.
3. Calculate probability and decision.
4. Log the decision before the label is known.
5. After expiry, attach labels: finish result, no-touch result, realized executable outcome.
6. Score calibration, Brier score, log loss, realized edge, drawdown, blocked-trade diagnostics, and ablation deltas.

Validation must answer:

- Did predicted 70 percent probabilities win about 70 percent of the time?
- Did `p_no_touch` identify path instability?
- Did execution costs erase theoretical edge?
- Did support/resistance, source-quality, news, and order-book gates improve or hurt results?
- Did the model work separately for BTC and ETH?

Tests:

- A replay fixture should prove future prices are not available to `DecisionState`.
- A label fixture should prove labels are attached only after expiry.
- An ablation test should compare core model versus core model without `p_no_touch`.

### 11. XGBoost Challenger: How It Builds Later

Create `src/polymarket_engine/validation/xgb_dataset.py` later, after enough shadow decisions exist.

Build method:

1. Use decision snapshots as rows.
2. Use only features that existed at decision time.
3. Train on older windows and validate on later windows.
4. Calibrate probabilities.
5. Compare against the transparent Monte Carlo model.
6. Promote only if it improves calibration and after-cost performance without increasing hidden leakage or drawdown.

First XGBoost should be a challenger, not the main engine. It can suggest blocking or probability adjustment, but the transparent model remains the anchor until live shadow evidence says otherwise.

### 12. Dashboard and API: How It Builds

Extend `src/polymarket_engine/app.py`.

First API routes:

```text
GET /health
GET /contracts/active
GET /decisions/latest
GET /decisions/{decision_id}
GET /reports/calibration
```

The UI should show:

- active BTC/ETH contracts
- current settlement-source price
- threshold and time left
- `p_finish`
- `p_no_touch`
- `z_path`
- executable bid/ask
- edge after costs
- decision and block reasons
- data-quality flags

The UI should not have live-trading buttons in v1.

### 13. Build Order With Acceptance Criteria

Build in this order:

1. Contract parser and `ContractSpec`.
   - Acceptance: tests prove supported contracts normalize and ambiguous contracts reject.
2. DuckDB schema and store.
   - Acceptance: tests insert and read contracts, prices, books, decisions, and labels.
3. Price and order-book ingestion adapters.
   - Acceptance: real adapters parse live-source payloads, and deterministic fixtures prove parser and storage behavior without exposing a synthetic collector mode.
4. `DecisionState` builder.
   - Acceptance: tests prove no future data enters state.
5. Volatility and `sigma_tau`.
   - Acceptance: tests prove volatility floor, weights, and regime multiplier work.
6. Monte Carlo path generators.
   - Acceptance: tests prove path counts, terminal wins, and no-touch survival.
7. Probability outputs.
   - Acceptance: tests prove `p_finish`, `p_no_touch`, `z_path`, and uncertainty are logged.
8. Edge, gates, and entry decision tree.
   - Acceptance: tests prove trade/wait/block/demand-more-edge/size-down outcomes.
9. Exit state machine and exit strategy logging.
   - Acceptance: tests prove hold/watch/exit transitions and hold-to-expiry counterfactual labels.
10. Replay and validation.
    - Acceptance: one historical contract replays end-to-end without future leakage.
11. API and dashboard.
    - Acceptance: UI can inspect latest read-only decisions.

### 14. What Not To Build Yet

Do not build these first:

- live order placement
- automatic wallet signing
- C++ probability engine as the primary implementation
- XGBoost as the primary decision-maker
- SOL live support
- ETF options context as a required input
- market-making inventory logic

These can be added after the BTC/ETH read-only engine proves its basic calibration and after-cost edge.

---

## Source Appendix: Full Extracted Paper Text

The following section is a preservation appendix. It contains the extracted text from the full paper before the focused-paper trim, including material that now belongs in this plan rather than the main research paper.

# Abstract

This paper studies short-dated crypto binary contracts, with BTC and ETH as the first markets of interest. A binary contract looks simple because it pays either one dollar or zero, but the pricing problem is not simply whether the asset is bullish or bearish. Each contract has a venue-defined threshold, a settlement source, a short expiry window, an executable order-book price, and path risk before expiry.

The central proposal is a remaining-path probability framework. The model estimates two related probabilities: p_finish, the probability that the contract finishes on the winning side of the threshold, and p_no_touch, the probability that the asset avoids crossing back through the danger line before expiry. The difference matters because a contract can have a favorable terminal probability but still be unsafe if the remaining path is unstable.

The first version should remain read-only. It should collect BTC and ETH market data, settlement-source prices, order-book snapshots, and decision logs before placing real money at risk. The research question is whether as-of path simulation, volatility-aware distance measures, executable price comparison, and strict validation can identify binary contracts whose quoted prices are mispriced after costs, latency, and risk controls.

# Contents

1\. Introduction and research objective

2\. Market instrument, rule parsing, and settlement source

3\. Asset universe and scope: BTC, ETH, and future SOL

4\. As-of data architecture and state construction

5\. Core hybrid probability engine

6\. Monte Carlo methodology and multiple path generators

7\. Polymarket order book and execution model

8\. Exit strategy and position management

9\. Decision features, news context, and risk gates

10\. Portfolio management and position sizing

11\. Validation, pass/fail standards, and ablation plan

12\. Live shadow logger, database schema, and dashboard

13\. Failure modes, kill switch, and operational safety

14\. XGBoost challenger and calibration protocol

15\. Implementation roadmap and future research

16\. Remaining implementation specifications before build

17\. Final architecture and conclusion

References and data documentation

# 1. Introduction and Research Objective

Reader map: this paper is about pricing a specific short-dated binary contract, not predicting the general direction of crypto. The model asks whether the executable quote is attractive for the exact BTC or ETH contract in front of it.

The sections build in order: first define the contract, then define the data the model is allowed to see, then define the probability outputs, then explain how those outputs become a trade, wait, block, or demand-more-edge decision.

The original research idea was to estimate the probability that BTC would finish on the correct side of a short-dated binary threshold while also measuring whether the remaining path was clean enough to hold through expiry. That remains the core insight, but the current architecture should be expanded in three ways.

First, the system should support both BTC and ETH contracts. The decision engine should estimate the edge of each active BTC and ETH contract separately. ETH is not included because it predicts BTC or because BTC predicts ETH. ETH is included because ETH contracts may offer their own independent opportunities, especially during regimes where BTC and ETH short-horizon behavior differs. The portfolio layer may still track shared crypto exposure, but cross-asset correlation is a risk-control input, not a directional trading signal.

Second, the system should use a hybrid decision model. The terminal win probability, `p_finish`, is the cleanest estimate of the binary payoff. A one-dollar binary contract with an 82 percent terminal win probability has a raw fair value near 82 cents before costs. But that does not mean the system should trade automatically. `p_no_touch`, `z_path`, support/resistance structure, execution quality, source disagreement, news risk, and model uncertainty should block trades, demand more edge, or reduce size.

Third, the system needs a portfolio and operational layer. A single contract can have positive expected value while the total system is unsafe because too many positions share the same expiry, asset, news window, liquidity condition, or data-source dependency. The implementation should therefore separate the contract edge problem from the portfolio sizing problem.

The research question becomes:

> Can a hybrid remaining-path probability engine identify short-dated BTC and ETH Polymarket contracts whose executable prices are mispriced after costs, latency, source-quality risk, path instability, and portfolio constraints?

The goal is not to forecast crypto broadly. The goal is narrower and more testable: at a specific as-of time, for a specific venue-defined contract, decide whether the executable quote is attractive enough to trade, wait, block, or demand a better price.

# 2. Market Instrument, Rule Parsing, and Settlement Source

Purpose of this section: before any probability model can be trusted, the contract rule has to be read exactly. The model does not invent the threshold, change the settlement source, or reinterpret the market after the fact.

In plain terms, this section answers: what event pays one dollar, what price source decides the result, and what rule details would cause a backtest or live system to score the contract incorrectly?

The venue defines the contract. The model should not choose the threshold, alter the start/end time, replace the settlement source, or reinterpret the rule text. The model may reject a market, demand more edge, or size down, but it must price the contract as written.

Polymarket documentation describes market resolution through UMA’s Optimistic Oracle process, where outcomes can be proposed and disputed before finalization. Current short-dated crypto Up/Down market pages also state that BTC, ETH, and SOL Up/Down contracts resolve using Chainlink Data Streams for the relevant asset pair, such as BTC/USD, ETH/USD, and SOL/USD. Polymarket’s RTDS documentation also lists real-time crypto price streams with Chainlink and Binance symbols. The practical implementation rule is simple: parse the actual market rule text for each contract, store the rule hash, and treat the named settlement source as the scoreboard. \[1\]\[2\]\[5\]\[6\]\[7\]\[8\]

## 2.1 Binary payoff definition

For a generic threshold binary, the payoff is:

``` math
\text{Payoff}_{\text{UP}} = \mathbf{1}\{ S_{T} > K\}
```

``` math
\text{Payoff}_{\text{DOWN}} = \mathbf{1}\{ S_{T} < K\}
```

Variables:

- $`K`$ = venue-defined threshold or reference price.
- $`S_{T}`$ = final settlement-source price at expiry $`T`$.
- $`\mathbf{1}\{ \cdot \}`$ = indicator function equal to 1 if the condition is true and 0 otherwise.

Important rule note: some Up/Down markets use the end price relative to the start price and may define Up as greater than or equal to the start price. The rule parser must store the exact comparison operator, such as $`>`$, $`<`$, $`\geq`$, or $`\leq`$, instead of assuming one default.

## 2.2 Rule parser requirements

| Field | Required treatment |
|----|----|
| `market_id` | Unique Polymarket identifier. |
| `asset` | BTC, ETH, or later SOL. |
| `contract_type` | Up/Down, above/below threshold, range, or other rule family. |
| `side` | UP, DOWN, YES, NO, or venue-specific outcome token. |
| `start_time` | Required for Up/Down contracts that compare end price to start price. |
| `end_time` / `expiry` | Exact timestamp used to score the final price. |
| `K` | Venue-defined threshold or start reference price. |
| `comparison_operator` | $`>`$, $`<`$, $`\geq`$, or $`\leq`$ exactly as written. |
| `settlement_source_id` | Chainlink stream, official venue source, or validated proxy if no direct stream is available. |
| `rule_text_hash` | Hash of the rule text at collection time. |
| `resolution_status` | Pending, proposed, disputed, resolved, canceled, or invalid. |

## 2.3 Settlement-source hierarchy

The settlement-source layer should use the following hierarchy:

1.  **Primary:** the exact source named in the market rules. For current short-dated crypto Up/Down examples, this appears to be Chainlink Data Streams for BTC/USD, ETH/USD, or SOL/USD. \[5\]\[6\]\[7\]
2.  **Secondary:** Polymarket RTDS crypto stream if it provides the named Chainlink symbol or a venue-supported representation. \[2\]
3.  **Proxy:** Binance, Coinbase, Kraken, or a robust exchange basket only for quality checks or when the primary source is unavailable.
4.  **Block:** if the source is unknown, stale, missing, or materially inconsistent with validated proxies.

A useful source-disagreement measure is:

``` math
\Delta_{\text{source},t} = \left| S_{t}^{\text{primary}} - S_{t}^{\text{proxy}} \right|
```

Variables:

- $`\Delta_{\text{source},t}`$ = absolute disagreement between primary and proxy price at time $`t`$.
- $`S_{t}^{\text{primary}}`$ = current price from the named settlement source.
- $`S_{t}^{\text{proxy}}`$ = robust proxy price from exchange or venue-supported feeds.

Decision rule: if $`\Delta_{\text{source},t}`$ is above the configured tolerance, the system should block the market or add a source-risk buffer to the required edge.

## 2.4 Rule edge cases that must be explicitly tested

| Edge case | Why it matters | First treatment |
|----|----|----|
| Greater-than vs greater-than-or-equal | Ties can flip the outcome near the boundary. | Store operator exactly from rule text. |
| Start price vs fixed threshold | Up/Down markets may compare end price to start price, not a static external K. | Capture `start_reference_price` separately from fixed K. |
| Missing final tick | The final Chainlink update may not align perfectly with the contract end second. | Store source timestamp and selection rule. |
| Delayed source update | A stale price may produce a false state. | Use source freshness gate. |
| Disputed resolution | Outcome may not finalize immediately. | Store `resolution_status` and delay final label. |
| Market cancellation | Invalid markets should not be treated as normal losses or wins. | Exclude or label separately. |
| Time-zone ambiguity | Contract title and source timestamp may use different zones. | Normalize all timestamps to UTC. |
| Rule text change | Backtests become invalid if old rules are overwritten. | Store rule text and hash at collection time. |

# 3. Asset Universe and Scope: BTC, ETH, and Future SOL

This section keeps the project from becoming too broad too early. BTC and ETH are the first real target markets because they have the most natural short-dated crypto binary use case. SOL can remain a later extension after the BTC/ETH methodology is validated.

The first implementation should focus on BTC and ETH short-dated Polymarket contracts. SOL should be logged and researched later, but it should not be part of the first trading scope unless liquidity, settlement-source quality, and shadow results justify promotion.

| Asset | Role in v1 | Why included |
|----|----|----|
| BTC | Baseline asset | Most natural starting point for the original engine and usually the main crypto binary market. |
| ETH | First expansion | Separate contract universe with its own path behavior, volatility, liquidity, and ETF/options context. |
| SOL | Future research asset | Worth collecting, but should be promoted only after data quality, liquidity, and calibration are proven. |

The model should not use BTC/ETH correlation to create a directional entry signal. For example, the system should not buy an ETH contract just because BTC moved first unless a later, separately validated feature proves that relationship out of sample. In v1, BTC and ETH are priced independently.

Correlation and common crypto beta still matter for portfolio risk. If the system has simultaneous BTC and ETH exposure during the same macro event or same crypto liquidation cascade, the portfolio layer should know that risk may concentrate. That is a sizing and cap issue, not a signal-generation issue.

Figure 1. Multi-asset as-of data architecture. BTC and ETH are separate contract signals, while source validation, order-book handling, risk control, and logging are shared infrastructure.

# 4. As-Of Data Architecture and State Construction

Purpose of this section: the model can only use information that would have existed at decision time. This is the anti-overfit rule. Future price movement, final settlement, and later Polymarket prices are labels for scoring, not inputs for the decision.

The state object is the model's snapshot of the world. At every decision timestamp, it should contain the current contract, current settlement-source price, recent realized movement, order-book state, source-quality flags, and time remaining.

Every decision must be as-of. At decision time $`t`$, the system can use only information timestamped at or before $`t`$. Future settlement, later prediction-market quotes, later candles, later news interpretation, and end-of-day summaries are labels or research data, not decision inputs.

The state builder converts raw event streams into a compact object that the probability engine, execution model, portfolio layer, shadow logger, dashboard, and kill switch can all read.

## 4.1 Required data lanes

| Data lane | Required fields | Purpose |
|----|----|----|
| Contract rules | `market_id`, asset, side, expiry, rule text, rule hash, settlement source | Defines the object being priced. |
| Polymarket order book | bids, asks, depth, timestamp, quote age, order-book hash if available | Defines executable entry and liquidity. |
| Polymarket trades | trade price, size, side if available, timestamp | Helps validate activity and reconstruct fills. |
| Settlement source | Chainlink stream value, timestamp, source status | Defines $`S_{t}`$, $`S_{T}`$, realized volatility, and `sigma_tau`. |
| Spot proxies | Binance, Coinbase, Kraken, robust basket, feed disagreement | Quality checks only; not volatility inputs. |
| Chainlink live ticks | 1-second or tick-level Chainlink price data | Needed for `p_no_touch`, wick risk, and volatility. |
| News/event calendar | macro releases, Fed events, ETF events, exchange incidents, regulatory events | Risk context, gate, or required-edge buffer. |
| Future ETF/GEX context | IV, skew, flow, GEX level proximity, quote age | Later ablation layer, not v1 authority. |

## 4.2 State object emitted at each decision time

The state builder should emit:

| Group | Fields |
|----|----|
| Contract state | timestamp, `market_id`, asset, side, $`K`$, start reference, expiry, seconds left, rule hash, settlement source. |
| Price state | $`S_{t}`$, robust $`S_{t}`$, source timestamp, feed disagreement, source quality flag. |
| Distance state | side-specific log distance, `z_path`, distance to K in dollars and basis points. |
| Volatility state | short/medium/long realized-vol windows, volatility trend, volatility regime, volatility floor status. |
| Path-shape state | recent wick frequency, adverse wick size, threshold crossing count, congestion around K. |
| Order-book state | best bid, best ask, bid-ask spread, target-size VWAP, available depth, quote age, book update rate. |
| Event state | minutes to scheduled event, minutes since release, event importance, asset relevance, surprise if already released. |
| Model state | cache bucket, path generator outputs, ensemble probability, uncertainty, XGBoost blocker state. |
| Risk state | portfolio exposure, asset exposure, same-window exposure, daily PnL, drawdown, kill-switch state. |

## 4.3 Data-quality principle

Data-quality problems should become explicit features, gates, or uncertainty buffers. They should never be silently ignored.

| Data problem | First treatment |
|----|----|
| Bad tick | Use robust median and feed agreement checks. |
| Stale Chainlink or RTDS price | Block if freshness fails. |
| Missing order-book depth | Block real trade simulation; log as lower-quality research row. |
| OHLC-only replay | Mark as lower confidence because intraperiod path ordering is unknown. |
| WebSocket gap | Block affected markets until snapshot reconciliation passes. |
| Source disagreement | Demand more edge or block. |
| Sparse path bucket | Increase `mc_uncertainty` or block. |

# 5. Core Hybrid Probability Engine

This is the mathematical core of the paper. The math should be read as a translation layer: it turns the contract state into probabilities, then compares those probabilities against executable market prices.

The core outputs are p_finish, p_no_touch, z_path, sigma_tau, and executable edge. They are not separate trading strategies. They are different measurements of the same contract state.

Quick symbol guide: S means price, K means threshold, t means now, T means expiry, tau means time left, sigma means movement or volatility scale, p means probability, and e means edge.

The hybrid engine uses terminal probability to estimate fair value and path/risk variables to decide whether the edge is usable. This avoids two bad extremes. It does not ignore path risk, but it also does not turn `p_no_touch` into a second payoff probability unless the strategy explicitly uses early exits or mark-to-market stops.

Figure 2. Hybrid decision model. `p_finish` prices the payout, while path survival, execution quality, event risk, uncertainty, and blockers decide whether the edge can be used.

## 5.1 p_finish: terminal win probability

Plain English: p_finish asks only where the contract ends. For an UP contract, it asks whether the final BTC or ETH settlement price finishes above the threshold. For a DOWN contract, it asks whether the final settlement price finishes below the threshold.

What it does not answer: p_finish does not tell us whether the path before expiry is stable, whether the order book is liquid, or whether the contract is cheap enough to buy.

`p_finish` asks whether the contract finishes on the winning side at expiry. It is the raw probability that the one-dollar binary pays out.

For Monte Carlo path $`i`$:

``` math
I_{\text{finish}}^{(i)} = \left\{ \begin{matrix}
1, & \text{if the simulated final price wins the contract} \\
0, & \text{otherwise}
\end{matrix} \right.\ 
```

``` math
{\widehat{p}}_{\text{finish}} = \frac{1}{N}\sum_{i = 1}^{N}I_{\text{finish}}^{(i)}
```

Variables:

- $`N`$ = number of simulated remaining paths.
- $`i`$ = index for one simulated path.
- $`I_{\text{finish}}^{(i)}`$ = terminal win indicator for path $`i`$.
- $`{\widehat{p}}_{\text{finish}}`$ = estimated terminal win probability.

Interpretation: if $`{\widehat{p}}_{\text{finish}} = 0.74`$, the raw pre-cost fair value of a one-dollar binary payoff is about \$0.74. It is not yet a trade decision.

## 5.2 p_no_touch: path-survival probability

Plain English: p_no_touch asks the harder question. It asks whether the asset can stay on the favorable side without crossing back through the danger line before expiry.

This is why p_no_touch can be lower than p_finish. The contract may still be likely to finish correctly, but if the path is unstable, the trade may require more edge or should be blocked.

`p_no_touch` asks whether the simulated path avoids crossing the danger line before expiry. It captures the stability of the remaining path.

For an UP contract:

``` math
I_{\text{survive}}^{(i)} = \mathbf{1}\left\{ \min_{u \in \lbrack t,T\rbrack}S_{u}^{(i)} > K \right\}
```

For a DOWN contract:

``` math
I_{\text{survive}}^{(i)} = \mathbf{1}\left\{ \max_{u \in \lbrack t,T\rbrack}S_{u}^{(i)} < K \right\}
```

The Monte Carlo estimate is:

``` math
{\widehat{p}}_{\text{no touch}} = \frac{1}{N}\sum_{i = 1}^{N}I_{\text{survive}}^{(i)}
```

Variables:

- $`S_{u}^{(i)}`$ = simulated settlement-source price at time $`u`$ on path $`i`$.
- $`\lbrack t,T\rbrack`$ = remaining interval from decision time $`t`$ to expiry $`T`$.
- $`K`$ = venue-defined threshold or start reference price.
- $`I_{\text{survive}}^{(i)}`$ = path-survival indicator for path $`i`$.
- $`{\widehat{p}}_{\text{no touch}}`$ = estimated path-survival probability.

Interpretation: a contract can have high `p_finish` but low `p_no_touch`. That means the final settlement may still be favorable, but the path is unstable enough that the system should wait, block, demand more edge, or reduce size.

## 5.3 z_path: normalized cushion from the danger line

Plain English: z_path measures how much cushion the contract has. It compares the current distance from the threshold against the expected remaining movement.

A raw dollar distance is not enough. Being 100 dollars above the threshold means one thing in quiet BTC and another thing in violent ETH. z_path makes the distance volatility-aware.

`z_path` measures the current cushion in units of expected remaining movement.

For an UP contract:

``` math
d_{\text{UP}} = log\left( \frac{S_{t}}{K} \right)
```

For a DOWN contract:

``` math
d_{\text{DOWN}} = log\left( \frac{K}{S_{t}} \right)
```

Side-specific distance:

``` math
d_{\text{side}} = \left\{ \begin{matrix}
d_{\text{UP}}, & \text{UP contract} \\
d_{\text{DOWN}}, & \text{DOWN contract}
\end{matrix} \right.\ 
```

Normalized cushion:

``` math
z_{\text{path}} = \frac{d_{\text{side}}}{\sigma_{\tau}}
```

Variables:

- $`S_{t}`$ = current settlement-source price at decision time $`t`$.
- $`K`$ = venue-defined threshold or start reference price.
- $`d_{\text{UP}}`$ = favorable log distance for an UP contract.
- $`d_{\text{DOWN}}`$ = favorable log distance for a DOWN contract.
- $`d_{\text{side}}`$ = side-specific favorable distance.
- $`\sigma_{\tau}`$ = expected remaining log-price movement over the time left.
- $`z_{\text{path}}`$ = current cushion measured in expected remaining-move units.

Interpretation: $`z_{\text{path}} \approx 0`$ means price is near the danger line. $`z_{\text{path}} \approx 1`$ means the cushion is about one expected remaining move. $`z_{\text{path}} \approx 2`$ means the cushion is about two expected remaining moves.

## 5.4 Movement scale: sigma_tau

Plain English: sigma_tau estimates how much the asset can still move before expiry. It is not a direction signal. It tells the Monte Carlo engine how wide the simulated future paths should be.

If sigma_tau is too small, the model becomes overconfident. If it is too large, the model becomes too scared to trade. The first version should therefore use a conservative realized-volatility estimate with a floor and regime adjustment.

The system is not trying to forecast direction with `sigma_tau`. It is estimating how much the settlement-source price can still move before expiry.

For BTC/ETH binary contracts, the settlement-source price means the Chainlink reference stream named by the rule. Exchange proxy feeds can diagnose feed disagreement and bad ticks, but they must not be used to calculate realized volatility or `sigma_tau`. If Chainlink data is missing for a replay window, the correct output is missing volatility, not a proxy-derived substitute.

A first-pass movement scale is:

``` math
\sigma_{\tau} = max\left( \sigma_{\text{floor}},\mspace{6mu} m_{\text{regime}}\sqrt{\tau}\left( w_{s}\sigma_{s} + w_{m}\sigma_{m} + w_{l}\sigma_{l} \right) \right)
```

Variables:

- $`\sigma_{\tau}`$ = expected remaining log-price movement from $`t`$ to $`T`$.
- $`\sigma_{\text{floor}}`$ = minimum movement assumption so the model never becomes risk-free in a quiet tape.
- $`m_{\text{regime}}`$ = volatility-regime multiplier.
- $`\tau`$ = seconds or fraction of time remaining until expiry, using the same unit as the volatility estimates.
- $`\sigma_{s}`$ = short-window realized volatility.
- $`\sigma_{m}`$ = medium-window realized volatility.
- $`\sigma_{l}`$ = longer-window realized volatility.
- $`w_{s}`$, $`w_{m}`$, $`w_{l}`$ = preset weights that sum to one.

The starting weights should be preset before testing, then evaluated walk-forward. They should not be optimized until they only fit old contracts.

## 5.5 Executable edge after costs

Plain English: a probability estimate only matters after it is compared against the price we can actually trade. A 74 percent fair probability is not useful if the contract costs 76 cents after spread, fees, slippage, and latency buffer.

This section is where the model leaves pure probability and becomes a decision system. The trade is only interesting if edge remains after executable price and all cost buffers.

The raw fair value is:

``` math
\text{FairValue}_{i} = {\widehat{p}}_{\text{finish},i}
```

For a one-dollar binary, the first edge estimate is:

``` math
\text{EdgeBeforeCosts}_{i} = {\widehat{p}}_{\text{finish},i} - P_{\text{exec},i}
```

The usable edge is:

``` math
\text{EdgeAfterCosts}_{i} = {\widehat{p}}_{\text{finish},i} - P_{\text{exec},i} - C_{i} - U_{i} - R_{i}
```

Variables:

- $`i`$ = candidate contract.
- $`{\widehat{p}}_{\text{finish},i}`$ = terminal win probability for contract $`i`$.
- $`P_{\text{exec},i}`$ = executable entry price, ideally target-size VWAP rather than midpoint.
- $`C_{i}`$ = book-crossing and execution costs: bid-ask crossing, visible-depth slippage, fees, latency, and uncertain-fill risk.
- $`U_{i}`$ = model uncertainty buffer from Monte Carlo dispersion, sparse bucket, calibration error, or data quality.
- $`R_{i}`$ = path-risk and event-risk buffer from `p_no_touch`, `z_path`, structure risk, and news/event risk.

The required edge is:

``` math
e_{\text{req}} = e_{0} + b_{\text{spread}} + b_{\text{latency}} + b_{\text{noise}} + b_{\text{MC}} + b_{\text{path}} + b_{\text{event}}
```

Variables:

- $`e_{\text{req}}`$ = minimum edge required before a trade is allowed.
- $`e_{0}`$ = base edge requirement.
- $`b_{\text{spread}}`$ = bid-ask crossing and uncertain-fill buffer.
- $`b_{\text{latency}}`$ = buffer for quote movement between signal and fill.
- $`b_{\text{noise}}`$ = feed noise, bad ticks, and source-disagreement buffer.
- $`b_{\text{MC}}`$ = Monte Carlo uncertainty and sparse-bucket buffer.
- $`b_{\text{path}}`$ = path-instability buffer from `p_no_touch`, `z_path`, and structure risk.
- $`b_{\text{event}}`$ = economic news, ETF, regulatory, exchange-outage, or event-risk buffer.

Trade condition:

``` math
\text{TradeAllowed}_{i} = \mathbf{1}\left\{ \text{EdgeAfterCosts}_{i} > e_{\text{req}} \right\} \cdot \mathbf{1}\{\text{AllHardGatesPass}_{i}\}
```

Interpretation: Version C is the baseline. `p_finish` prices the payout. `p_no_touch`, `z_path`, execution quality, data quality, news/event risk, XGBoost, and portfolio limits decide whether to trade, wait, block, demand more edge, or size down.

## 5.6 Polymarket book-crossing clarification

The cost language in the edge equation should be read as book-crossing cost, not as a vague market-order assumption. Polymarket should be modeled from the order book: a buy pays the ask side or the target-size ask VWAP, while a sell or exit receives the bid side or the target-size bid VWAP. A user-facing instant trade is best represented as a marketable limit order that still depends on visible depth, quote freshness, latency, and maximum acceptable price.

Therefore, spread remains important, but the cleaner term for the formula is execution cost or book-crossing cost. The bid-ask spread is one component of that cost; depth, partial-fill risk, slippage, fees, latency, and order-book movement are the rest.

# 6. Monte Carlo Methodology and Multiple Path Generators

Purpose of this section: Monte Carlo is the main estimator because it can answer both terminal and path questions. It can count how many simulated paths finish correctly and how many survive without crossing the danger line.

The important implementation rule is as-of simulation. At decision time, the engine may simulate possible futures or sample historical fragments, but it may not use the actual future of the contract being tested as an input.

The primary estimator should be as-of walk-forward empirical Monte Carlo. At every decision time $`t`$, the engine builds the state as if it is living at $`t`$, then simulates possible remaining paths using only data available before $`t`$ or historical fragments from earlier periods. The realized future of the current contract is never an input.

Figure 3. Multiple Monte Carlo path generators. The ensemble uses several ways to generate paths from the same as-of state so model risk becomes visible.

Figure 4. As-of decision snapshot. The observed series stops at the decision boundary; the hidden future is used only later for scoring.

## 6.1 Prior distribution for path generation

Monte Carlo paths do not come from nowhere. Before the engine can simulate future prices, it must define a prior distribution: the distribution of remaining path behavior the engine believes is realistic before the future of the current contract is known.

For this project, the prior should be empirical and conditional rather than a purely subjective guess. It should be built from historical BTC and ETH path fragments that would have been available before the decision time. The prior answers:

> Historically, when the contract state looked like this as of time $`t`$, what did the remaining path usually look like?

The prior is conditioned on a small set of state variables:

- asset: BTC or ETH;
- horizon: 5-minute or 15-minute contract;
- seconds left until expiry;
- side and distance from the threshold;
- `z_path` bucket;
- realized-volatility regime and volatility trend;
- recent wick frequency, threshold-cross behavior, and adverse excursion;
- source-quality state, including stale feeds or settlement/proxy disagreement;
- event window flag if scheduled or breaking news is active.

Implementation rule: the prior may use older historical data and live data observed up to time $`t`$. It may not use the realized future of the contract being replayed. Future BTC/ETH movement, final settlement, and later Polymarket prices are labels only.

In the first version, the engine should build prior buckets from historical path fragments. If the bucket is sufficiently populated, sampled fragments become the base Monte Carlo shocks. If the bucket is sparse, the engine should widen to a coarser bucket, increase the uncertainty buffer, or block the trade. This prevents the system from acting confident just because it found a tiny historical sample that happened to work.

The prior is then adapted to the current market state. `sigma_tau` scales the sampled path shocks to current expected remaining movement, and stress overlays can add final-window wick or event-risk scenarios. This gives the engine a practical compromise: historical path realism plus current live volatility.

This design is aligned with Monte Carlo simulation as the counting framework \[20\], dependent time-series resampling through bootstrap-style methods \[9\], and realized-volatility scaling through models such as HAR-RV \[21\].

Plain English: the prior is the simulation's starting belief. Monte Carlo is the counting machine. The prior decides what kinds of paths the machine is allowed to count.

## 6.2 Are we using multiple generations?

Yes, but the paper should call them **multiple path generators** rather than future generations. This avoids confusion. A path generator is a way of creating simulated remaining paths from the same as-of state. It does not look at the future of the current contract.

Using multiple generators is useful because each generator fails differently:

- empirical conditional priors preserve real wicks but may have sparse comparable buckets;
- block bootstrap preserves short-term dependence but can create unnatural joins;
- filtered historical simulation handles volatility scaling but can smooth path shape too much;
- stress overlays expose final-window and news-window risk but should not dominate the central estimate.

The point is not to average random models blindly. The point is to measure whether the trade only works under one fragile path assumption. If the edge disappears under reasonable path generators, the system should demand more edge or block.

## 6.3 Generator set

| Generator | Purpose | First-pass use |
|----|----|----|
| G1: empirical conditional prior | Sample historical same-asset, same-horizon, similar-state path fragments from data available before the decision time. | Primary estimate because it preserves real crypto wicks, jumps, and path shape. |
| G2: moving or stationary block bootstrap | Resample blocks of short returns to preserve dependence in time-series data. | Challenger and uncertainty source. |
| G3: filtered historical simulation | Normalize historical residuals by realized volatility, then rescale to current `sigma_tau`. | Useful when current volatility differs from the historical prior bucket. |
| G4: stress overlays | Add final-window wicks, source-disagreement, or news-window shocks. | Risk overlay, not central fair value unless validated. |

Block and stationary bootstrap methods are standard ways to resample dependent time-series data without pretending every return is independent. The stationary bootstrap is a classic method for weakly dependent time series. \[9\]

## 6.4 Ensemble probability and dispersion

Each generator $`g`$ produces a terminal probability and path-survival probability:

``` math
{\widehat{p}}_{\text{finish}}^{(g)},\quad{\widehat{p}}_{\text{no touch}}^{(g)}
```

The ensemble probability can start as a weighted average:

``` math
{\widehat{p}}_{\text{finish,ens}} = \sum_{g = 1}^{G}w_{g}{\widehat{p}}_{\text{finish}}^{(g)}
```

``` math
{\widehat{p}}_{\text{no touch,ens}} = \sum_{g = 1}^{G}w_{g}{\widehat{p}}_{\text{no touch}}^{(g)}
```

Variables:

- $`G`$ = number of path generators.
- $`g`$ = generator index.
- $`w_{g}`$ = preset generator weight, with $`\sum_{g}^{}w_{g} = 1`$.
- $`{\widehat{p}}_{\text{finish}}^{(g)}`$ = terminal win probability from generator $`g`$.
- $`{\widehat{p}}_{\text{no touch}}^{(g)}`$ = path-survival probability from generator $`g`$.
- $`{\widehat{p}}_{\text{finish,ens}}`$ = ensemble terminal win probability.
- $`{\widehat{p}}_{\text{no touch,ens}}`$ = ensemble path-survival probability.

Generator disagreement becomes part of uncertainty:

``` math
u_{\text{gen}} = \sqrt{\sum_{g = 1}^{G}w_{g}\left( {\widehat{p}}_{\text{finish}}^{(g)} - {\widehat{p}}_{\text{finish,ens}} \right)^{2}}
```

Variables:

- $`u_{\text{gen}}`$ = uncertainty from disagreement across path generators.
- Higher $`u_{\text{gen}}`$ means the result depends heavily on modeling choice.

## 6.5 Conditioning variables

Monte Carlo does not need every feature. It should condition on a small, stable set:

| Group | Variables | Reason |
|----|----|----|
| Contract state | asset, side, seconds left, horizon type | Defines payoff and time window. |
| Distance state | $`S_{t}`$, $`K`$, $`d_{side}`$, `z_path` | Defines current cushion. |
| Volatility state | `sigma_tau`, vol regime, vol trend | Scales path width. |
| Path-shape state | recent wick frequency, recent threshold crosses, adverse excursion | Captures unstable tape. |
| Source-quality state | source age, feed disagreement, data granularity | Prevents false precision. |
| Event state | macro/news window flag, minutes to event, minutes since event | Adjusts risk around scheduled and breaking events. |

Execution variables such as bid-ask spread, depth, target-size VWAP, and quote age should usually remain decision gates rather than path-generation variables. That keeps the path engine focused on price behavior and the decision engine focused on tradability.

## 6.6 Monte Carlo test cases and unanswered questions

The research plan should explicitly test the following:

| Question | Test |
|----|----|
| How many paths are enough near a trade? | Compare 1,000, 5,000, 10,000, and cached estimates against live shadow calibration. |
| How should fragments be selected? | Compare same asset only, same horizon, same volatility regime, and same wick regime. |
| How should the prior be built? | Compare strict conditional buckets, coarser fallback buckets, and sigma-scaled fragments. |
| Should fragments be scaled? | Compare raw fragments vs `sigma_tau`-scaled fragments. |
| How should final seconds be handled? | Test final-window overlays and separate final-30-second buckets. |
| What happens during macro news? | Compare event-window vs non-event-window path libraries. |
| Does ETH need a separate path library? | Yes by default; test cross-asset pooling only as future research. |
| What if the bucket is sparse? | Increase uncertainty, fall back to coarser bucket, or block. |
| How should Chainlink/proxy disagreement be handled? | Add source buffer or block, then validate against final labels. |
| Do multiple generators agree? | Track generator dispersion and edge sensitivity. |

## 6.7 Cached grids and refresh rules

A full Monte Carlo run on every tick is unnecessary. The live path should use cached probability grids and refresh only when the state changes enough to matter.

| Trigger | Action |
|----|----|
| New contract appears | Initialize relevant asset/side/horizon grid. |
| Time bucket changes | Move to nearest cached bucket or refresh if missing. |
| `z_path` bucket changes | Interpolate or refresh near entry boundary. |
| Volatility regime changes | Refresh because path distribution changed. |
| News/event flag changes | Demand more edge; refresh if event-window generator is enabled. |
| Source disagreement spike | Block or refresh with source-risk overlay. |
| Near-entry state | Use higher path count before real trade eligibility. |
| Cache stale | Refresh or block. |

# 7. Polymarket Order Book and Execution Model

Purpose of this section: the market price used in the decision must be executable. The model should not compare fair value against a midpoint if the real entry would require crossing the ask or accepting depth and latency risk.

The probability engine estimates value. The execution model decides whether that value is tradable. Polymarket describes its trading system as a central limit order book, and its docs state that all orders are technically limit orders; a market order is a limit order priced to execute immediately against resting orders. Live order-book data should use the WebSocket market channel rather than only polling, and the REST order-book endpoint can provide a book summary with bids, asks, market details, and last trade price. \[3\]\[4\]

This means the document should avoid saying “send a market order” as if execution were free. The correct execution assumption is:

> Submit a marketable limit order with a maximum acceptable price, and simulate whether visible depth can fill the target size after latency stress.


Figure 5. Execution model. Shadow fills use visible depth, VWAP, impact, quote-age checks, and latency stress rather than midpoint prices.

## 7.1 Executable price

For buying a YES-like outcome, the executable price should use the ask side of the book. For target size $`Q^{*}`$:

``` math
Q_{\text{fillable}}\left( P_{\max} \right) = \sum_{j:p_{j} \leq P_{\max}}^{}q_{j}
```

``` math
\text{VWAP}_{\text{entry}} = \frac{\sum_{j \in J^{*}}^{}p_{j}q_{j}}{\sum_{j \in J^{*}}^{}q_{j}}
```

Variables:

- $`Q^{*}`$ = target order size.
- $`P_{\max}`$ = maximum acceptable price for a marketable limit order.
- $`p_{j}`$ = price at order-book level $`j`$.
- $`q_{j}`$ = quantity available at level $`j`$.
- $`Q_{\text{fillable}}`$ = quantity available up to $`P_{\max}`$.
- $`J^{*}`$ = set of price levels needed to fill the target size.
- $`\text{VWAP}_{\text{entry}}`$ = depth-weighted entry price for the target size.

Execution is valid only if:

``` math
Q_{\text{fillable}}\left( P_{\max} \right) \geq Q^{*}
```

and quote age, bid-ask spread, latency, visible depth, and source-quality gates all pass.

## 7.2 Execution modes

| Mode | Description | Stage |
|----|----|----|
| Shadow only | Log hypothetical fills, no orders. | Required first stage. |
| Marketable-limit simulation | Simulate fills using order-book depth and latency stress. | Research and shadow validation. |
| Real limit with timeout | Place limit, cancel if not filled quickly. | Later live pilot only. |
| Hybrid execution | Marketable only for large edge; otherwise passive limits. | Future execution research. |

## 7.3 Historical data tiers

If historical Tier 1 data is unavailable, the system should begin collecting it immediately.

| Tier | Data quality | Allowed use |
|----|----|----|
| Tier 1 | WebSocket book events, periodic snapshots, trades, Chainlink/RTDS, exchange proxies, on-chain fills where possible | Real backtest candidate and live shadow validation. |
| Tier 2 | Best bid/ask snapshots with quote timestamps and some depth | Early research only; execution claims limited. |
| Tier 3 | Trades, charts, midpoint, or OHLC-only data | Not valid for executable EV claims. Useful only for rough falsification. |

## 7.4 Market impact and crowding

Thin books can erase the edge. The system should treat its own order as part of the execution problem.

A first impact metric is:

``` math
\text{ImpactRatio}_{i} = \frac{Q_{i}^{*}}{D_{\epsilon,i}}
```

Variables:

- $`\text{ImpactRatio}_{i}`$ = proposed order size divided by visible depth near the executable price.
- $`Q_{i}^{*}`$ = target size for contract $`i`$.
- $`D_{\epsilon,i}`$ = visible depth within an acceptable price band $`\epsilon`$.

First rule: block or reduce size when the target order is too large relative to visible depth. The system should also track crowding risk: quote update bursts, vanishing depth, high bid-ask spread volatility, repeated small fills just before expiry, and sudden order-book imbalance changes.

# 8. Exit Strategy and Position Management

Feature	Definition	Use
dist_to_K_bps	Distance from S_t to K in basis points.	Basic cushion and danger-line proximity.
cross_count_K_L	Number of times price crossed K in lookback window L.	Detects chop around the settlement line.
touch_count_epsilon_L	Count of touches within ϵ of K.	Detects repeated danger-line pressure.
congestion_K_L	Fraction of time price spent within ϵ of K.	Flags thresholds inside noisy zones.
local_level_distance	Distance to recent local high/low or volume/time concentration level.	Flags nearby barriers or magnets.
adverse_wick_ratio	Recent adverse wick size divided by sigma_tau.	Detects sudden path-risk behavior.
trend_alignment	Whether short-horizon trend supports the contract side.	Weak filter only; not a standalone signal.
reversal_speed	How quickly price moved away from or back to K after crossings.	Helps separate clean breaks from chop.


Purpose of this section: entry and exit are not the same problem. Entry asks whether a new position has edge. Exit asks whether an existing position is still worth holding after updated probability, path risk, order-book friction, and remaining time.

The hybrid system needs explicit exit logic because an entry filter and an exit rule do not have the same job. Before entry, the system can be strict because rejecting a trade has no transaction cost. After entry, the system must avoid overreacting to noise because selling pays the bid/ask, loses optionality, and can turn a correct terminal probability estimate into a realized loss.

External exit-strategy research is useful as a starting point. One large systematic-exit study found that Stop & Reverse performed best across its test set, while also warning that traders must verify exit rules in their own markets and systems. For Polymarket binaries, the translation should be conservative: stop-and-exit is the primary candidate, while stop-and-reverse is a challenger because reversing pays more book-crossing cost and requires the opposite contract to have its own edge. \[17\]

Figure 6. Exit strategy state machine. The system separates raw exit warnings from confirmed exit decisions so noisy updates do not force premature exits.

## 8.1 Why exit logic is separate from entry logic

The entry decision asks whether a new position should be opened. The exit decision asks whether an existing position should be closed before expiry. Those are different because an open position already paid entry cost, already has an executable mark-to-market price, and may still have positive terminal value even if the path becomes temporarily noisy.

For a terminally settled binary, a brief touch or noisy deterioration in p_no_touch does not automatically mean the contract will lose. p_no_touch should therefore be treated as a path-risk state, not an automatic liquidation command. The exit layer should compare continuing value against executable exit value.

## 8.2 Hold-to-expiry baseline

Every exit policy must be compared against a hold-to-expiry baseline. If a contract is bought because p_finish indicates positive terminal edge, the simplest benchmark is to hold until resolution and score the final payoff. A proposed early-exit rule is useful only if it improves after-cost EV, drawdown, or tail loss relative to that baseline without cutting too many eventual winners.

## 8.3 Noise-aware stop-and-exit

The primary candidate should be a noise-aware stop-and-exit rule. It exits the held contract only when the value of selling now is clearly better than the value of continuing to hold, after bid/ask crossing, target-size VWAP, latency, slippage, and a hysteresis buffer.

``` math
V\_ exit,t\  = \ P\_ exec,exit,t\  - \ C\_ exit,t
```

``` math
V\_ hold,t\  = \ p\_ finish,adj,t\  - \ B\_ hold,t
```

``` math
Exit\ only\ if\ \ \ V\_ exit,t\  > \ V\_ hold,t\  + \ h\_ exit
```

Variables:

V_exit,t = cost-adjusted value of exiting the held contract at time t.

P_exec,exit,t = executable exit price, using bid-side target-size VWAP for a long held outcome.

C_exit,t = exit execution cost, including bid/ask crossing, slippage, latency, and uncertain fill.

V_hold,t = model value of continuing to expiry after hold-risk adjustment.

p_finish,adj,t = current terminal win probability after calibration and risk adjustments.

B_hold,t = buffer for continuing risk: path instability, event risk, source disagreement, and model uncertainty.

h_exit = hysteresis buffer that prevents small noisy changes from triggering exits.

This rule makes the exit decision economic. The model does not exit because p_no_touch falls, z_path flickers, or a single tick touches K. It exits only when the confirmed, executable value of exiting is better than holding by enough margin to overcome noise and costs.

## 8.4 Stop-and-reverse as a challenger

Stop-and-reverse should be tested, but not treated as the default. In a futures-style strategy, reversing may be simple because the opposite position is part of the same instrument workflow. In Polymarket binaries, reversing may mean selling one outcome into the bid and buying the opposite outcome through the ask, or otherwise paying a new round of execution cost. That friction matters.

``` math
Reverse\ only\ if\ \ \ edge\_ opposite\  > \ e\_ enter\  + \ C\_ roundtrip
```

Variables:

edge_opposite = after-cost edge of the opposite contract or outcome.

e_enter = required edge for a fresh entry.

C_roundtrip = cost of exiting the held side plus entering the opposite side, including book crossing and latency.

Plain rule: do not reverse merely because the current trade becomes uncomfortable. Reverse only when the opposite side independently qualifies as a valid trade after costs, depth, quote age, source quality, and portfolio limits.

## 8.5 Confirmation, hysteresis, and cooldown controls

Exit rules need confirmation controls so the hybrid model does not churn around K. The default behavior should be to move from OPEN_NORMAL to OPEN_WATCH on weak or noisy evidence, then to EXIT_PENDING only after confirmation.

| **Control** | **Purpose** | **Initial design** |
|----|----|----|
| Hysteresis band | Prevents one-tick touches from becoming exits. | Use a band at least as large as source disagreement, recent micro-noise, and minimum price increment. |
| Dwell time | Requires adverse condition to persist. | Require 1-3 seconds or several approved source updates for 5m/15m markets, then validate. |
| Source consensus | Prevents one feed from forcing exit. | Primary settlement source plus approved proxy agreement when close to K. |
| Fresh executable exit price | Avoids acting on stale bids or vanished depth. | Exit requires fresh book, target-size VWAP, quote age pass, and latency stress pass. |
| Cooldown after rejected exit | Prevents oscillation. | If raw exit signal fails confirmation, wait briefly before reconsidering unless a hard safety rule fires. |
| Exit EV buffer | Prevents exits with tiny theoretical improvement. | Require V_exit to beat V_hold by h_exit after costs. |

## 8.6 Exit state machine

| **State** | **Meaning** | **Allowed action** |
|----|----|----|
| FLAT | No position is open. | Evaluate entries only. |
| ENTRY_CANDIDATE | Edge exists, but gates are still being checked. | Trade only if all entry gates and portfolio caps pass. |
| OPEN_NORMAL | Position is open and model state is acceptable. | Hold and keep logging. |
| OPEN_WATCH | Raw exit warning exists, but confirmation is incomplete. | Do not exit automatically; require confirmation and EV comparison. |
| EXIT_PENDING | Exit condition is confirmed and executable exit price is fresh. | Exit in shadow or later live pilot if target-size VWAP passes. |
| EXITED | Position has been closed before expiry. | Append exit telemetry and later post-expiry regret labels. |
| KILL_SWITCH_EXIT | Hard operational or risk failure. | Cancel open orders and stop automation if live trading is ever enabled. |

## 8.7 Exit strategy test matrix

| **Exit family** | **Role in this project** | **Promotion requirement** |
|----|----|----|
| Hold to expiry | Baseline for every valid entry. | Always reported; all exits must beat it after costs. |
| Noise-aware stop-and-exit | Primary exit candidate. | Improves EV, drawdown, or tail loss without excessive premature exits. |
| Stop-and-reverse | Challenger inspired by systematic-exit research. | Opposite side must have independent after-cost edge and survive round-trip cost. |
| Breakeven stop | Secondary challenger. | Must beat simpler stop-and-exit after bid/ask and latency costs. |
| Profit target | Secondary challenger for contracts that become overpriced before expiry. | Must improve realized EV without truncating too many large winners. |
| Time stop | Benchmark only. | Promote only if it beats hold-to-expiry and stop-and-exit out of sample. |
| Trailing stop | Later research. | Requires enough live data to avoid overfitting and noise churn. |

The shadow logger should record early_exit_triggered, early_exit_would_have_won, early_exit_saved_loss, noise_exit, exit_regret, hold_to_expiry_pnl, and exit_pnl. Those labels decide whether exit logic belongs in production or remains a research layer.

# 9. Decision Features, News Context, and Risk Gates

Purpose of this section: risk gates prevent a theoretically attractive probability from becoming a bad trade. The model should be allowed to say wait, block, or demand more edge even when p_finish looks favorable.

Section 9 turns model outputs into entry, wait, block, demand-more-edge, and size-adjustment decisions. The key improvement is to make structure, news, execution, and data-quality features mechanical. They should not become discretionary chart commentary.

## 9.1 Decision outputs

| Decision | Meaning |
|----|----|
| Trade | Edge after costs exceeds required edge, path and execution gates pass, portfolio size is allowed, and blockers do not reject. |
| Wait | Direction is interesting, but timing, path stability, quote quality, news window, or price level is not clean enough yet. |
| Block | A hard gate fails: stale data, source mismatch, thin depth, high latency, bad rule parse, excessive uncertainty, kill switch, or high false-positive risk. |
| Demand more edge | Trade might be valid only at a better price because uncertainty, book-crossing cost, path risk, news risk, or execution risk is elevated. |

## 9.2 Structure and support/resistance features

Support and resistance should be quantified as structure risk around the contract’s threshold.

| Feature | Definition | Use |
|----|----|----|
| `dist_to_K_bps` | Distance from $`S_{t}`$ to $`K`$ in basis points. | Basic cushion and danger-line proximity. |
| `cross_count_K_L` | Number of times price crossed $`K`$ in lookback window $`L`$. | Detects chop around the settlement line. |
| `touch_count_epsilon_L` | Count of touches within $`\epsilon`$ of $`K`$. | Detects repeated danger-line pressure. |
| `congestion_K_L` | Fraction of time price spent within $`\epsilon`$ of $`K`$. | Flags thresholds inside noisy zones. |
| `local_level_distance` | Distance to recent local high/low or volume/time concentration level. | Flags nearby barriers or magnets. |
| `adverse_wick_ratio` | Recent adverse wick size divided by `sigma_tau`. | Detects sudden path-risk behavior. |
| `trend_alignment` | Whether short-horizon trend supports the contract side. | Weak filter only; not a standalone signal. |
| `reversal_speed` | How quickly price moved away from or back to K after crossings. | Helps separate clean breaks from chop. |

One simple congestion formula is:

``` math
\text{Congestion}_{K,L} = \frac{\text{time spent with }\left| S_{u} - K \right| \leq \epsilon}{L}
```

Variables:

- $`\text{Congestion}_{K,L}`$ = fraction of lookback window spent near threshold $`K`$.
- $`S_{u}`$ = settlement-source or robust price at time $`u`$.
- $`\epsilon`$ = near-threshold band.
- $`L`$ = lookback window.

Decision rule: high congestion, high crossing count, or large adverse wick ratio should increase required edge or block if the threshold is too unstable.

## 9.3 Order-book and market microstructure features

| Feature | Use |
|----|----|
| bid_ask_spread | Blocks small theoretical edges that disappear after crossing the bid/ask or paying target-size VWAP. |
| `available_depth_target` | Confirms enough size exists for target position. |
| `target_vwap` | Replaces midpoint with executable price estimate. |
| `quote_age_ms` | Blocks stale order-book states. |
| `book_update_rate` | Detects fast-moving or unstable book conditions. |
| `depth_decay_1s` / `depth_decay_3s` | Measures whether depth disappears under latency stress. |
| `order_book_imbalance` | Research feature; may indicate one-sided pressure or fragile liquidity. |
| `last_trade_recency` | Confirms whether the market is active or stale. |
| `fillability_score` | Combined score from depth, bid-ask spread, target-size VWAP, quote age, and latency stress. |

## 9.4 Source-quality features

| Feature | Use |
|----|----|
| `chainlink_age_ms` | Blocks stale settlement-source price. |
| `rtds_age_ms` | Blocks stale venue-supported stream. |
| `feed_disagreement_bps` | Demands more edge or blocks when Chainlink and proxies diverge. |
| `bad_tick_flag` | Prevents one corrupt price from controlling `z_path`. |
| `source_switch_flag` | Marks when fallback source is used. |
| `data_granularity` | Separates tick/1s data from lower-confidence OHLC replay. |

## 9.5 News and event-risk features

News should be included, but as a risk context layer first. The system should not enter trades because a headline sounds bullish or bearish unless that feature later passes ablation. In v1, news and event features may adjust uncertainty, `p_no_touch`, or required edge. They must not directly recalculate `sigma_tau`; volatility remains Chainlink-only.

| Feature group | Examples | First use |
|----|----|----|
| Scheduled macro | CPI, PCE, FOMC decision, FOMC minutes, NFP/jobs, unemployment, GDP, major Fed speeches | Block or demand more edge around high-impact windows. |
| Crypto-specific news | ETF approval/rejection headlines, regulatory actions, major exchange outages, chain halts, liquidation cascades | Event-risk flag and stress overlay. |
| ETF-related context | Spot ETF flow release windows, ETF market hours, ETF option IV/skew changes | Later uncertainty and risk-appetite context. |
| Breaking-news state | `headline_time`, source reliability, asset relevance, duplicate-news filter | Log first; use only after timestamped validation. |
| Post-release surprise | Actual minus consensus after release | Allowed only after release timestamp; never use revised or future data. |

A simple event-risk buffer can be:

``` math
b_{\text{event}} = f\left( \text{event\_importance},\mspace{6mu}\text{minutes\_to\_event},\mspace{6mu}\text{asset\_relevance},\mspace{6mu}\text{recent\_vol\_response} \right)
```

Variables:

- $`b_{\text{event}}`$ = added required-edge buffer from event risk.
- `event_importance` = high, medium, or low expected market impact.
- `minutes_to_event` = time until scheduled event, or negative after release.
- `asset_relevance` = whether the event is broad macro, BTC-specific, ETH-specific, or crypto-wide.
- `recent_vol_response` = observed volatility reaction after the event is known.

## 9.6 Noise control and residual uncertainty policy

Noise is not a standalone alpha signal, and it should not be treated as something the model can simply pay for with a larger buffer. The first job is to remove or avoid it: reject bad ticks, require fresh timestamps, reconcile feeds, wait for confirmation, and block when the state cannot be trusted. Only after those controls pass should the engine price a contract.

| Noise class | Examples | Primary control |
|----|----|----|
| Source/feed noise | stale Chainlink or RTDS, proxy disagreement, bad ticks, source switch, coarse OHLC replay | Reject bad ticks, require source freshness, compare against approved proxies, and block near K when feeds disagree. |
| Market microstructure noise | wide spread, stale quote, fast book update rate, depth decay, vanishing liquidity | Use fresh target-size VWAP, quote-age gates, latency stress, and depth checks; block if executable price is not reliable. |
| Threshold/path noise | repeated crosses around K, one-tick touches, congestion near K, adverse wicks | Require dwell/confirmation, avoid threshold chop, and wait or block when the danger line sits inside noisy movement. |
| Model/data sparsity noise | sparse historical bucket, high generator dispersion, low path count, weak calibration bucket | Widen to a coarser validated bucket, collect more data, or block instead of faking precision. |
| Event noise | scheduled macro release, exchange incident, oracle disruption, breaking crypto news | Switch to research-only logging or hard-block during severe windows until the event state is observable again. |

After these controls, any remaining measured uncertainty is residual model or execution uncertainty, not raw noise. That residual can increase existing terms such as `b_source`, `b_execution`, `b_MC`, `b_path`, or `b_event`, but the engine should not use a generic noise fee to justify trading through dirty data.

``` math
e_{\text{req}} = e_{0} + b_{\text{execution}} + b_{\text{source}} + b_{\text{MC}} + b_{\text{path}} + b_{\text{event}}
```

Plain rule: clean the input first. If it cannot be cleaned, do not trade. Ambiguous states become wait or research-only logging; stale source, stale book, threshold-overlapping source disagreement, severe depth failure, or too-sparse path evidence becomes block. Every decision row should log the active noise flags and the action taken so validation can test whether the controls reduced false positives or merely deleted good trades.

## 9.7 Mandatory gates

A trade candidate must pass:

1.  `p_finish` edge exceeds required edge after costs.
2.  `p_no_touch` is strong enough for the side and horizon.
3.  `z_path` shows enough cushion from the danger line.
4.  Monte Carlo uncertainty and generator dispersion are below threshold or priced into edge.
5.  Chainlink/source quality is fresh and consistent enough.
6.  Polymarket bid-ask spread, depth, quote age, and target-size VWAP pass.
7.  Rule parser has no unresolved edge case.
8.  Structure risk around K does not show severe congestion or repeated threshold chopping.
9.  News/event flags do not require a hard block.
10. Portfolio sizing allows the exposure.
11. XGBoost blocker, once promoted, does not reject the setup.
12. Kill switch is not active.

# 10. Portfolio Management and Position Sizing

The probability engine answers whether a contract looks mispriced. The portfolio layer answers how much size, if any, the system can take.

BTC and ETH signals should be estimated independently. The portfolio layer should not use correlation as a directional trade trigger. It should use exposure, common event windows, drawdown, and liquidity risk to prevent a set of individually reasonable trades from becoming one concentrated portfolio bet.

Figure 7. Portfolio layer. BTC and ETH edges are independent contract signals, while the allocator controls shared risk, caps, exposure, and kill-switch state.

## 10.1 Contract-level expected value

For candidate contract $`i`$:

``` math
\mu_{i} = {\widehat{p}}_{\text{finish},i} - P_{\text{exec},i} - C_{i} - U_{i} - R_{i}
```

Variables:

- $`\mu_{i}`$ = expected edge per one-dollar contract after costs and buffers.
- $`{\widehat{p}}_{\text{finish},i}`$ = terminal win probability.
- $`P_{\text{exec},i}`$ = executable entry price.
- $`C_{i}`$ = execution cost buffer.
- $`U_{i}`$ = model uncertainty buffer.
- $`R_{i}`$ = path, structure, and event-risk buffer.

## 10.2 Binary payoff variance

For a one-dollar binary bought at price $`P_{i}`$ with model win probability $`p_{i}`$:

``` math
\text{Var}_{i} = p_{i}\left( 1 - P_{i} \right)^{2} + \left( 1 - p_{i} \right)\left( 0 - P_{i} \right)^{2} - \left( p_{i} - P_{i} \right)^{2}
```

Variables:

- $`\text{Var}_{i}`$ = model-implied variance of the position payoff.
- $`p_{i}`$ = model probability of payout.
- $`P_{i}`$ = executable entry price.

## 10.3 Fractional Kelly with hard caps

A simplified Kelly fraction for a binary contract is:

``` math
f_{i}^{*} = \frac{p_{i} - P_{i}}{1 - P_{i}}
```

The actual allowed fraction should be much smaller:

``` math
f_{i,\text{trade}} = min\left( f_{\max},\mspace{6mu}\alpha f_{i}^{*},\mspace{6mu} f_{\text{liquidity},i},\mspace{6mu} f_{\text{portfolio},i} \right)
```

Variables:

- $`f_{i}^{*}`$ = theoretical Kelly fraction for contract $`i`$.
- $`f_{i,\text{trade}}`$ = final allowed fraction of bankroll or risk budget.
- $`f_{\max}`$ = absolute maximum fraction per trade.
- $`\alpha`$ = fractional Kelly multiplier, such as 0.10 to 0.25 for research settings.
- $`f_{\text{liquidity},i}`$ = cap from available depth and impact.
- $`f_{\text{portfolio},i}`$ = cap from current portfolio exposure and drawdown.

The system should start conservative. Sizing should be reduced when `p_no_touch` is weak, `z_path` is small, source disagreement is high, news risk is active, generator dispersion is high, or the order book is thin.

## 10.4 Portfolio constraints

| Constraint | Purpose |
|----|----|
| Per-trade max size | Prevents one overconfident estimate from dominating. |
| Per-asset exposure cap | Limits BTC or ETH concentration. |
| Same-window exposure cap | Limits stacked trades expiring in the same few minutes. |
| Event-window exposure cap | Reduces risk around CPI, FOMC, ETF events, or breaking crypto news. |
| Liquidity cap | Prevents target size from exceeding a safe share of visible depth. |
| Daily loss limit | Stops trading after a bad day. |
| Drawdown limit | Stops trading after cumulative deterioration. |
| Model-uncertainty haircut | Reduces size when MC, calibration, or XGBoost disagreement is high. |

A portfolio optimization objective can be researched later:

``` math
\max_{w}\left( w^{\top}\mu - \lambda w^{\top}\Sigma w \right)
```

subject to:

``` math
0 \leq w_{i} \leq w_{i,max},\quad\sum_{i}^{}w_{i} \leq W_{\max}
```

Variables:

- $`w`$ = vector of position sizes.
- $`\mu`$ = vector of expected edges.
- $`\lambda`$ = risk-aversion parameter.
- $`\Sigma`$ = covariance or risk matrix used for portfolio risk diagnostics.
- $`w_{i,max}`$ = maximum allowed size for position $`i`$.
- $`W_{\max}`$ = maximum total exposure.

Important clarification: $`\Sigma`$ is a risk-management object. It should not be used in v1 to generate directional BTC/ETH trade signals.

# 11. Validation, Pass/Fail Standards, and Ablation Plan

Purpose of this section: validation decides whether the idea is real. A backtest is not enough unless it is as-of, avoids future leakage, includes executable prices, and shows which model components actually improve the result.

The system should not be judged by whether a few trades look smart. It should be judged by as-of, executable, after-cost, out-of-sample performance.

Scikit-learn documents Brier score as a strictly proper scoring rule for probabilistic binary predictions, log loss as a probability-based loss, calibration curves as reliability diagrams, and TimeSeriesSplit as appropriate for time-ordered validation where training on future data would be invalid. Those concepts fit this project because the model outputs probabilities over binary outcomes and must be tested chronologically. \[10\]\[11\]\[12\]\[13\]

## 11.1 Validation rules

1.  Use chronological walk-forward splits, never random train/test splits.
2.  Keep a final untouched holdout period.
3.  Evaluate executable expected value, not midpoint edge.
4.  Include bid/ask crossing, target-size VWAP, fees, slippage, latency stress, quote-age stress, fill assumptions, and uncertainty buffers.
5.  Evaluate by asset, side, horizon, time left, volatility regime, event regime, and liquidity bucket.
6.  Compare live shadow logs against historical replay assumptions.
7.  Report every blocked reason and missed opportunity.
8.  Require ablation support before adding complexity to production rules.

## 11.2 Initial pass/fail standards

These are starting research standards. They should be revised after the first shadow dataset is collected.

| Area | First pass/fail standard |
|----|----|
| Data quality | No real trading unless settlement source, order book, local clock, and proxy feeds are fresh and aligned. |
| Shadow sample size | Require thousands of decision rows and hundreds of candidate trades per major asset/horizon before making strong claims. |
| Executable EV | Positive after all costs and buffers on walk-forward and final holdout. |
| Confidence | Bootstrapped lower confidence bound on EV should remain positive before promotion. |
| Calibration | Probability buckets should not show severe overconfidence; reliability curves should be stable by asset/horizon. |
| Brier score | Must beat the market-implied or naive baseline on final holdout. |
| Log loss | Must beat baseline without hiding bad tails. |
| Drawdown | Shadow drawdown must stay below the preset research limit. |
| Edge decay | Signal must survive latency and worse-fill stress. |
| Concentration | No single asset, time window, event regime, or bucket should explain nearly all profit. |
| Opportunity deletion | Gates and XGBoost blockers must not delete most profitable opportunities. |
| Operational safety | Kill switch, stale-feed blocks, and error logging must work before live execution. |

## 11.3 Ablation plan

| Ablation | Question answered |
|----|----|
| Core BTC Monte Carlo only | Does the original remaining-path engine have standalone signal? |
| Core ETH Monte Carlo only | Does ETH have its own signal after costs? |
| Core BTC/ETH plus structure gates | Do crossing, congestion, wick, and level features improve outcomes? |
| Core plus order-book execution model | Does the edge survive VWAP, depth, quote age, and latency stress? |
| Core plus event/news risk | Do macro and crypto event flags improve required-edge and block decisions? |
| Core plus portfolio sizing | Does risk-adjusted sizing improve return per drawdown? |
| Core plus XGBoost blocker | Does XGBoost reduce false positives out of sample without deleting too many winners? |
| Core plus ETF/GEX context | Does timestamped IV, skew, flow, or GEX improve volatility, no-touch, uncertainty, or required-edge decisions? |
| Live shadow vs historical replay | Do live as-of logs disagree with reconstructed historical assumptions? |
| Core plus stop-and-exit policy | Does a noise-aware early exit improve after-cost EV, drawdown, or tail loss versus holding to expiry? |
| Core plus stop-and-reverse challenger | Does reversing into the opposite outcome add value after round-trip book-crossing cost and independent edge checks? |

## 11.4 Exit-strategy validation labels

Exit policies should be promoted only through ablation, not intuition. The hold-to-expiry baseline, stop-and-exit, stop-and-reverse, breakeven stop, profit target, time stop, and trailing stop should be evaluated on the same candidate entries with identical execution assumptions. The key question is whether the exit rule improves realized after-cost results or simply exits noisy winners too early.

| **Exit metric** | **Definition** | **Use** |
|----|----|----|
| premature_exit_rate | Share of exits where holding to expiry would have been profitable. | Detects noise-driven over-exiting. |
| saved_loss_rate | Share of exits that reduced or avoided a later loss after costs. | Measures real protection. |
| missed_recovery_rate | Share of exits caused by a temporary adverse move that recovered before expiry. | Identifies chop around K. |
| exit_slippage_cost | Difference between model exit value and executable bid-side VWAP after latency. | Keeps exits cost-aware. |
| exit_vs_hold_delta | Exit PnL minus hold-to-expiry PnL for the same entry. | Main promotion metric. |
| noise_exit_cluster_count | Number of exits clustered during feed disagreement, book gaps, or high-noise intervals. | Flags operational failure rather than alpha. |

# 12. Live Shadow Logger, Database Schema, and Dashboard

The live shadow logger should be built as soon as possible. If historical Tier 1 order-book data cannot be obtained, the best solution is to begin collecting it now.

The logger should store raw data first and derived features second. Raw event tables should be immutable. Derived state and model outputs can be rebuilt with new feature versions.

Figure 8. Live shadow logger and validation database. Raw events stay immutable, decision snapshots are versioned, and labels are appended only after expiry.

## 12.1 Database tables

| Table | Purpose |
|----|----|
| `raw_polymarket_book_events` | WebSocket order-book snapshots, price changes, and book deltas. |
| `raw_polymarket_trades` | Public trade prints and activity. |
| `raw_chainlink_prices` | BTC/USD, ETH/USD, and later SOL/USD settlement-source values. |
| `raw_exchange_prices` | Binance, Coinbase, Kraken, or other proxy feeds. |
| `raw_news_events` | Timestamped macro, crypto, ETF, exchange, and regulatory event records. |
| `contract_rules` | Rule text, rule hash, asset, side, settlement source, start/end times. |
| `decision_snapshots` | One row per as-of model decision. |
| `post_expiry_labels` | Labels appended after resolution. |
| `model_versions` | Code version, feature version, gate version, path-generator version, config version. |
| `kill_switch_events` | Any automatic or manual stop event. |

## 12.2 Decision snapshot fields

| Group | Fields |
|----|----|
| Contract state | timestamp, `market_id`, asset, side, K, start reference, expiry, seconds left, rule hash. |
| Market quote | best bid, best ask, bid-ask spread, depth, target-size VWAP, quote age, book update rate. |
| Price state | $`S_{t}`$, robust $`S_{t}`$, Chainlink age, proxy prices, feed disagreement, source-quality flag. |
| Model outputs | `p_finish`, `p_no_touch`, `z_path`, generator outputs, ensemble dispersion, MC uncertainty. |
| Decision state | decision, edge before/after costs, required edge, block reason, demand-more-edge reason. |
| Portfolio state | proposed size, active exposure, asset exposure, event-window exposure, drawdown, daily PnL. |
| Versioning | model version, feature version, gate version, data version, code commit, cache bucket ID. |
| Operations | latency, API status, kill-switch state, dashboard alert state. |

## 12.3 Labels added after expiry

| Label | Meaning |
|----|----|
| `final_settlement_price` | Settlement-source price used to resolve the contract. |
| `finish_win` | Whether the contract resolved on the winning side. |
| `danger_line_touch` | Whether price crossed the danger line after the decision and before expiry. |
| `profitable_after_costs` | Whether the hypothetical trade made money after costs and fill assumptions. |
| `false_positive` | Model showed attractive edge but realized trade failed after costs or path risk. |
| `missed_winner` | Model blocked or ignored a setup that later would have been profitable. |
| `resolution_delay` | Time between expiry and final resolution. |
| `rule_exception` | Any rule ambiguity, tie, dispute, cancellation, or data problem. |
| early_exit_triggered | Whether the exit policy would have closed the contract before expiry. |
| early_exit_would_have_won | Whether the model exited a contract that later would have resolved profitably if held. |
| early_exit_saved_loss | Whether the early exit avoided or reduced a later loss after executable costs. |
| noise_exit | Whether exit was caused by a short-lived noisy move, feed issue, or book flicker that later reversed. |
| exit_regret | Difference between exit PnL and hold-to-expiry PnL. |

## 12.4 Operator dashboard

A dashboard is not cosmetic. It is how the operator sees whether the system is healthy.

The dashboard should show:

- active BTC and ETH markets;
- current rule parse and settlement source;
- Chainlink/source freshness;
- Polymarket order-book freshness and depth;
- current $`S_{t}`$, $`K`$, `z_path`, `p_finish`, and `p_no_touch`;
- executable price, target-size VWAP, bid-ask spread, and edge after costs;
- block reason or demand-more-edge reason;
- current news/event state;
- active portfolio exposure by asset, horizon, and expiry window;
- MC cache status and generator dispersion;
- XGBoost blocker result after promotion;
- live shadow performance metrics;
- kill-switch state and recent errors.

# 13. Failure Modes, Kill Switch, and Operational Safety

The system should fail closed. If the state is unknown, stale, inconsistent, or not reproducible, the decision should be `BLOCK`.

Figure 9. Failure modes and kill-switch logic. The safe default is to block new entries when a critical system state is unknown.

## 13.1 Hard failure modes

| Failure mode | Action |
|----|----|
| Settlement source stale | Block affected asset/market. |
| Polymarket WebSocket gap | Block affected market until snapshot reconciliation succeeds. |
| Order-book snapshot mismatch | Block market and log reconciliation failure. |
| Local clock drift | Block all new decisions until time sync is restored. |
| Quote age above threshold | Block affected market. |
| Depth disappears | Reduce size or block. |
| Latency spike | Block marketable execution. |
| Source disagreement spike | Demand more edge or block. |
| Model output NaN/missing | Block. |
| Monte Carlo cache stale | Refresh or block. |
| Sparse bucket | Demand more edge or block. |
| Rule parser ambiguity | Block contract. |
| News/event hard block | Block until event window clears. |
| Daily loss limit hit | Soft kill: stop new entries. |
| Drawdown limit hit | Hard kill or manual review required. |
| API auth/signing failure | Cancel open orders and block. |
| Unexpected exception loop | Hard kill and alert operator. |

## 13.2 Soft kill vs hard kill

``` math
\text{SoftKill} = \text{stop new entries but keep collecting, logging, and monitoring}
```

``` math
\text{HardKill} = \text{cancel open orders, stop all automation, and require manual reset}
```

Variables:

- `SoftKill` = a non-destructive safety state used when new risk should stop but observation can continue.
- `HardKill` = full stop state used when execution, data integrity, or risk safety is compromised.

## 13.3 Manual kill switch

There should be a manual kill switch that can be triggered outside the model. The manual kill should:

1.  cancel open orders if live trading is ever enabled;
2.  block all new entries;
3.  continue safe logging if possible;
4.  record operator, timestamp, reason, exposure, and system state;
5.  require explicit manual reset.

## 13.4 Wallet, key, and treasury safety for future live trading

This belongs in the architecture before live execution, not after.

| Control | Purpose |
|----|----|
| Hot-wallet capital limit | Limits maximum loss from automation or key compromise. |
| API key permissions | Prevents unnecessary privileges. |
| Withdrawal separation | Keeps trading automation from being able to drain funds. |
| Max open exposure | Prevents runaway position growth. |
| Manual approval for size increase | Keeps v1 conservative. |
| Audit log | Records every decision, order, cancellation, and kill event. |

## 13.5 Exit-storm and premature-exit freeze rule

An exit storm occurs when many raw exit signals appear at once because of source disagreement, WebSocket gaps, book flicker, a macro/news shock, or threshold chop. The safe response is not to mechanically exit everything from an unreliable state. The system should block new entries, freeze model-driven exits that lack confirmation, continue logging, and require source/book reconciliation before resuming normal logic.

| **Trigger** | **Response** |
|----|----|
| Many raw_exit_signal events without source consensus. | Freeze model-driven exits, block new entries, continue logging. |
| Order-book gap or snapshot mismatch during exit window. | Do not rely on the stale exit price; reconcile book first. |
| Primary settlement source stale while proxies move. | Block new entries and keep open-position decisions in watch mode unless hard kill applies. |
| Exit signals cluster around scheduled macro/news event. | Apply event-risk mode and require larger exit EV improvement. |
| Dashboard cannot show exit reasons. | Treat as operational failure and soft kill new entries. |

## 13.6 Docker/VPS migration requirements

The engine should be portable to a London or Dublin VPS through Docker, but portability is not enough. A containerized deployment must preserve security, state, clock integrity, network recovery, and kill-switch behavior. Docker should make migration repeatable, not hide operational risk.

The first Docker deployment remains read-only. It may collect markets, prices, order books, latency measurements, and shadow decisions. Trading endpoints stay disabled until the same checklist passes in paper mode and then receives explicit manual approval.

| Requirement | Docker/VPS treatment | Failure behavior |
|----|----|----|
| Secrets | Load secrets through Docker secrets, a mounted secret file, or server-side environment injection. Do not bake `.env`, API keys, wallet keys, or auth tokens into the image. | Container refuses to start if required secret names are missing. |
| Persistent data | Mount `data/raw`, `data/db`, logs, model artifacts, and config snapshots as named volumes or host bind mounts. The image is disposable; the data volume is not. | Collector exits before writing if the archive sentinel or data volume is missing. |
| Clock sync | The VPS host must run NTP/chrony/systemd-timesyncd. The app logs host UTC time, source event time, observed time, and clock-drift checks. | Block decisions and alert if local clock drift exceeds tolerance. |
| Network reconnects | WebSocket collectors use capped reconnect backoff with source-specific error state. One feed failure must not stop unrelated feeds. | Mark affected source unhealthy; continue collecting healthy feeds; block markets depending on the failed source. |
| Process restarts | Docker Compose or systemd should use `restart: unless-stopped` for read-only collectors and explicit health checks. Startup must clean orphaned `.tmp` files before writing. | Restart collector after crash, but enter soft-kill/read-only state after repeated crash loops. |
| Order kill switch | Any future trading container must read a shared kill-switch file or control row outside the container image. Kill state must survive container restart. | Cancel open orders if live trading is enabled; block new orders until manual reset. |
| Disk durability | Raw writes must remain atomic: write `.parquet.tmp`, fsync or durable link where available, then publish final Parquet. DuckDB and Parquet volumes need backup/snapshot policy. | Stop writing if disk is near full, volume is read-only unexpectedly, or durability check fails. |
| Server monitoring | Export health state for collector liveness, source freshness, disk usage, restart count, clock drift, queue/backlog, and latest successful write. | Alert operator and soft-kill decisions when critical health checks fail. |
| Latency measurement | Record receive latency by source, API round-trip latency, WebSocket message age, order-book quote age, and server-to-venue ping where available. | Increase required edge, reduce size, or block execution when latency exceeds configured threshold. |
| API auth | Separate read-only market-data credentials from trading credentials. Trading auth is not mounted into read-only containers. | If auth refresh/signing fails, block trading, keep read-only collection if safe, and alert. |
| Private key handling | Private keys must never be committed, copied into images, printed in logs, or exposed to notebooks. Use hot-wallet limits, scoped keys, file permissions, and optional hardware or remote signer design. | Disable live trading if key file permissions, wallet limit, or signer health check fails. |

Minimum Docker layout:

```text
docker/
  Dockerfile.collector
  compose.readonly.yml
  compose.paper.yml
  compose.live.yml
config/
  production.example.toml
secrets/
  README.md
data/
  raw/
  db/
  logs/
```

Deployment rule: `compose.readonly.yml` must work first. `compose.paper.yml` can add simulated orders only after read-only collection survives restart, network drop, and disk-full tests. `compose.live.yml` must not exist with real credentials until kill-switch, auth, private-key, monitoring, and rollback controls are verified.

# 14. XGBoost Challenger and Calibration Protocol

Purpose of this section: XGBoost should not be the first authority. It should begin as a challenger or calibration layer after enough clean labeled decisions exist. Its job is to improve or block signals, not to hide the core logic inside a black box.

XGBoost should be included, but not as the first authority. The first authority is still Monte Carlo because it directly estimates terminal and path events. XGBoost should initially identify when the Monte Carlo engine is overconfident, when a setup resembles historical false positives, or when a trade should demand more edge.

XGBoost documentation supports probability-producing binary classification objectives such as `binary:logistic`, and it also supports constraints that can restrict feature interactions or impose monotonicity. Those tools are useful because this project needs probability outputs, not just class labels, and because some feature relationships should be constrained by domain logic. \[14\]\[15\]\[16\]

## 14.1 First XGBoost targets

| Target | Use |
|----|----|
| `false_positive_risk` | Probability that a Monte Carlo edge fails after costs or path risk. |
| `profitable_after_costs_probability` | Probability that a candidate trade is profitable after execution assumptions. |
| `touch_risk_calibration` | Calibration adjustment for `p_no_touch`. |
| `edge_decay_risk` | Probability that edge disappears before execution. |
| `calibration_adjustment` | Cap or adjust overconfident Monte Carlo probabilities. |

## 14.2 Allowed features

| Feature group | Examples |
|----|----|
| Monte Carlo outputs | `p_finish`, `p_no_touch`, generator dispersion, path count, sparse bucket flag. |
| Distance/time | `z_path`, seconds left, horizon, side, asset, distance to K. |
| Volatility | short/medium/long vol, vol trend, vol regime, volatility floor status. |
| Structure | congestion, crossing count, wick risk, local-level distance. |
| Execution | bid-ask spread, depth, target VWAP, quote age, latency stress, fillability score. |
| Source quality | Chainlink age, feed disagreement, source switch flag, bad tick flag. |
| News/event | event window, minutes to event, event importance, asset relevance, surprise after release. |
| Portfolio | current exposure, same-window exposure, drawdown state. |

## 14.3 Banned features

The following features create leakage and should be banned:

- future settlement price;
- future candle high, low, close, or return;
- later Polymarket quote or trade data;
- post-resolution activity;
- final outcome label;
- revised economic data that was not known at time $`t`$;
- any feature created from the realized future path of the same contract.

## 14.4 Promotion rule

XGBoost should be promoted in stages:

| Stage | Permission |
|----|----|
| Research only | Train and evaluate, no effect on decisions. |
| Advisory | Show dashboard warning only. |
| Blocker | May block trades with high false-positive risk. |
| Demand-more-edge | May increase required edge. |
| Sizing haircut | May reduce size through uncertainty adjustment. |
| Trade creator | Not allowed in v1. Consider only after extensive out-of-sample proof. |

Promotion requires:

1.  out-of-sample EV improvement after costs;
2.  improved or stable calibration;
3.  lower false-positive rate;
4.  drawdown improvement;
5.  no excessive deletion of profitable opportunities;
6.  stable results across BTC and ETH or clearly asset-specific models;
7.  final untouched holdout success.

# 15. Implementation Roadmap and Future Research

## 15.1 Immediate build order

| Phase | Deliverable |
|----|----|
| 1\. Rule and source parser | Parse BTC/ETH Polymarket rules, store Chainlink/source mapping, operators, rule hash. |
| 2\. Live raw logger | Start collecting Polymarket order book, trades, Chainlink/RTDS, exchange proxies, and news flags. |
| 3\. As-of state builder | Emit reproducible state snapshots with no future leakage. |
| 4\. Core Monte Carlo | Build empirical path-fragment generator and cached probability grids. |
| 5\. Execution simulator | Add target-size VWAP, depth, quote age, latency, slippage, and impact stress. |
| 6\. Hybrid decision layer | Add `p_finish` fair value, `p_no_touch`/`z_path` risk controls, required edge, gates. |
| 7\. Portfolio layer | Add sizing caps, exposure limits, drawdown limits, and kill-switch state. |
| 8\. Shadow validation | Run live read-only decisions and append labels after resolution. |
| 9\. XGBoost challenger | Train blocker/calibrator only after enough clean labels exist. |
| 10\. Future layers | ETF/GEX, SOL, advanced noise estimators, real execution pilot. |

## 15.2 Future research kept outside v1 authority

| Future layer | Why later |
|----|----|
| SOL trading | Needs separate liquidity, data-quality, and calibration proof. |
| ETF/GEX context | Useful but should pass prospective ablation before it changes decisions. |
| Advanced microstructure estimators | Realized kernels, pre-averaging, and two-scale estimators should challenge the simple first version later. |
| XGBoost trade creation | Too easy to overfit; first use should be blocker/calibrator only. |
| Passive limit execution | Requires fill-probability model and queue-position assumptions. |
| Cross-asset alpha | Not part of v1; BTC and ETH trade signals remain independent. |
| Full portfolio optimizer | Start with caps and fractional Kelly; optimize only after enough live shadow rows exist. |

## 15.3 Open research questions

1.  Which Chainlink timestamp rule exactly matches Polymarket resolution for each short-dated crypto family?
2.  Can historical Polymarket Tier 1 order-book data be obtained, or must the system rely on prospective collection?
3.  What path generator mix is best for BTC and ETH separately?
4.  How stable are `p_finish` and `p_no_touch` calibration by horizon and volatility regime?
5.  What minimum `p_no_touch` and `z_path` thresholds survive live shadow validation?
6.  Which news/event flags improve required-edge decisions without overblocking?
7.  How much edge decay occurs between signal and realistic executable fill?
8.  What size is safe relative to visible depth without creating market impact?
9.  Does XGBoost reduce false positives without deleting too many true opportunities?
10. When, if ever, should SOL or ETF/GEX context be promoted?

# 16. Remaining Implementation Specifications Before Build

The architecture is now complete enough for research, but the build still needs a specification layer. This section converts the remaining open items into testable defaults, schemas, runbooks, and governance rules. The numbers below are initial research defaults. They should be frozen before a validation pass, logged with each model version, and changed only through a documented experiment.

## 16.1 Initial configuration defaults

The first implementation should not optimize these values until the shadow logger has collected enough data. They are meant to make the system conservative, reproducible, and easy to falsify.

| **Parameter** | **Initial research default** | **Action if violated** |
|----|----|----|
| max_polymarket_quote_age | 1-2 seconds for live shadow; tighter if WebSocket latency is reliably lower. | Block the market or mark the decision as stale. |
| max_settlement_source_age | 5 seconds for Chainlink/RTDS-style source updates unless the specific market cadence proves slower. | Block new entries for that asset. |
| source_disagreement_tolerance | Demand more edge above 2-5 bps; block above 10 bps or when the threshold is within the disagreement band. | Raise source-risk buffer or block. |
| max_bid_ask_spread | 3 cents preferred; 5 cents maximum for research candidates unless edge is unusually large. | Demand more edge or block. |
| min_visible_depth | Target size must be fillable inside the max acceptable price using visible depth. | Reject the simulated fill. |
| max_order_share_of_visible_depth | 10-20 percent of visible depth at eligible price levels. | Reduce size or block to avoid impact and crowding. |
| minimum p_no_touch | Start at 0.65-0.75 depending on horizon, asset, volatility regime, and event risk. | Demand more edge or wait. |
| minimum z_path | Start near 0.5 in quiet regimes and closer to 1.0 in high-volatility or event windows. | Wait or block near the danger line. |
| generator_dispersion | Demand more edge if generator probabilities differ by more than 5 percentage points; block above 10 points. | Treat model disagreement as uncertainty. |
| event_hard_block_window | For CPI, FOMC, NFP, PCE, major Fed events: start with 5-15 minutes before and after release. | Block or switch to research-only logging. |
| daily_loss_limit | Soft kill at 1-2 percent of allocated research bankroll; hard/manual review at 3-5 percent. | Stop new entries, then escalate. |
| same_window_exposure_cap | Cap exposure sharing the same expiry window, asset, or macro event state. | Allocator reduces or rejects size. |
| exit_hysteresis_band | At least the larger of source-disagreement band, recent micro-noise band, and minimum price increment. | Do not confirm exit until price moves beyond the band. |
| exit_dwell_time | Start with 1-3 seconds or several approved source updates for 5m/15m markets. | Stay in OPEN_WATCH until confirmed. |
| minimum_exit_EV_improvement | Exit only if V_exit exceeds V_hold by the configured exit buffer, unless hard kill applies. | Hold or remain in OPEN_WATCH. |
| premature_exit_tolerance | Pre-set tolerance before validation; tighten if exits cut too many later winners. | Keep exit policy in research mode. |

Default rule: these are not final trading thresholds. They are starting gates for shadow collection. A threshold can be loosened only if the live shadow record shows better executable EV, calibration, and drawdown after costs.

## 16.2 Rule-parser test suite

The rule parser is a high-risk component because one wording mistake can flip the label. Every market family should have fixed fixtures before its data is used for training or validation.

| **Test case** | **Expected behavior** | **Reason** |
|----|----|----|
| BTC Up/Down fixture | Extract asset, start time, end time, threshold/start reference, settlement source, comparison operator, and market id. | BTC is the baseline market family. |
| ETH Up/Down fixture | Run the same extraction independently for ETH. | ETH is traded separately, not as a BTC-derived signal. |
| Tie behavior | Store \>, \>=, \<, or \<= exactly as written. | A tie can change payout classification. |
| Start-reference versus fixed K | Store start price/reference separately from a fixed threshold K. | Some markets compare end price to start price. |
| UTC and timestamp normalization | Convert all start, decision, expiry, and receive times to UTC internally. | Avoid time-zone and daylight-saving bugs. |
| Rule text hash | Hash rule text at collection time and invalidate assumptions if it changes. | Prevents silent rule drift. |
| Cancellation or dispute | Do not score as normal win/loss until final resolution is confirmed. | Avoids poisoning labels. |
| Unexpected wording | Fail closed and require manual review. | Unknown rules should never become trades. |

## 16.3 Settlement timestamp logic

The unresolved settlement question is not only which source resolves the contract, but which timestamp from that source is used. Before production labels are trusted, the system must compare resolved market outcomes against the named Chainlink or venue feed around expiry and infer the timestamp-selection rule for each market family.

| **Candidate rule** | **Meaning** | **How to validate** |
|----|----|----|
| Last update at or before expiry | Use the most recent source price with timestamp \<= T. | Compare historical resolutions to source ticks immediately before expiry. |
| First update after expiry | Use the first source price with timestamp \> T. | Check whether official outcomes match the first post-expiry update. |
| Nearest update to expiry | Use the closest source tick by absolute time distance. | Test both sides of expiry across many resolved contracts. |
| Venue-posted final value | Use a final value published by the market or resolution process. | Prefer when the venue provides explicit final reference values. |
| Oracle-proposed value | Use the value proposed through the resolution process if available. | Needed if the source tick alone does not reproduce outcomes. |

Labeling rule: if the timestamp rule is ambiguous near the threshold, mark the label as low confidence and exclude it from model promotion. Ambiguous labels may still be useful for operational diagnostics.

## 16.4 Database DDL and ingestion rules

Raw data should be immutable. Derived features can be rebuilt, but the raw feed, receive timestamp, parser version, and source metadata must survive unchanged.

| **Table** | **Required keys and fields** | **Purpose** |
|----|----|----|
| contract_rules | market_id, token_id, asset, side, rule_text, rule_text_hash, source_name, start_time, expiry_time, comparison_operator, parser_version | Defines the contract object and prevents rule drift. |
| raw_polymarket_book_events | event_id, market_id, token_id, exchange_timestamp, receive_timestamp, bids_json, asks_json, book_hash, source, ingest_version | Stores order-book snapshots and deltas. |
| raw_polymarket_trades | trade_id, market_id, token_id, price, size, side_if_known, exchange_timestamp, receive_timestamp, onchain_hash_if_available | Stores public trade history and later fill validation. |
| raw_settlement_prices | asset, source, source_timestamp, receive_timestamp, price, sequence_id, quality_flag | Stores Chainlink/RTDS-style settlement-source prices. |
| raw_exchange_prices | asset, exchange, symbol, source_timestamp, receive_timestamp, bid, ask, last, mid, quality_flag | Builds proxy and source-disagreement checks. |
| raw_news_events | event_id, event_type, scheduled_time, release_time, asset_scope, severity, source, surprise_value, ingest_version | Stores scheduled and later validated event context. |
| decision_snapshots | decision_id, market_id, timestamp, feature_version, model_version, gate_version, state_json, outputs_json, decision, block_reason | One row per as-of model decision. |
| post_expiry_labels | market_id, decision_id, final_price, finish_win, touch_after_decision, profitable_after_costs, label_confidence | Labels appended only after expiry. |
| kill_switch_events | event_id, timestamp, trigger, scope, action, operator, reset_time, notes | Audit trail for safety actions. |

| **Ingestion issue** | **Required handling** |
|----|----|
| Duplicate WebSocket messages | Use deterministic dedupe keys and keep duplicate counts for diagnostics. |
| Reconnect gaps | Mark the gap, reconcile with a fresh snapshot, and block decisions until state is clean. |
| Clock drift | Store source timestamp and local receive timestamp; alert if drift exceeds tolerance. |
| Late-arriving data | Store it, but never allow it to revise a prior as-of decision row. |
| Schema or parser change | Version the parser and keep old decisions tied to the old version. |
| Missing depth | Do not infer executable size from best price alone. |

## 16.5 Execution lifecycle rules

Version 1 should simulate marketable limit execution, not vague market orders. The engine chooses a maximum acceptable price, checks visible depth, applies latency stress, computes target-size VWAP, and rejects trades that cannot execute inside the limit.

``` math
{VWAP}_{target} = \frac{\sum_{j \in L}^{}p_{j}q_{j}}{\sum_{j \in L}^{}q_{j}}
```

Variables: L = eligible order-book levels at or below the maximum acceptable price for a buy; p_j = price at level j; q_j = visible quantity at level j; VWAP_target = depth-weighted simulated entry price for the target size.

| **Lifecycle step** | **v1 rule** |
|----|----|
| Signal creation | Monte Carlo edge must exist before execution is considered. |
| Pre-trade gates | Check source freshness, quote age, bid-ask spread, target-size VWAP, depth, event state, portfolio caps, and kill-switch state. |
| Max acceptable price | Set from model probability minus required edge, costs, uncertainty, and path-risk buffers. |
| Latency stress | Reprice the book or haircut edge using a configured latency assumption before accepting a fill. |
| Partial fill | For shadow mode, count only the fillable portion and log the unfilled remainder; for live mode later, cancel remainder by default. |
| Order TTL | Use a short timeout for later real orders; stale unfilled orders should cancel automatically. |
| Passive limits | Research later. Do not rely on queue position until queue/fill data is collected. |
| Cancel/replace | Future execution research only unless live data proves it adds value after latency and fees. |
| Exit evaluation | Compare cost-adjusted exit value against cost-adjusted hold value; do not exit on p_no_touch alone. |
| Exit execution price | Use bid-side target-size VWAP for selling a held long outcome, after latency stress and depth checks. |
| Stop-and-reverse check | Allow only if the opposite outcome independently passes entry gates after round-trip book-crossing cost. |

## 16.6 Monte Carlo generator-weight policy

Multiple generators are useful because they expose model risk. The first build should not let one optimistic generator create a trade. Empirical fragments should anchor the fair value, while bootstrap, filtered historical simulation, and stress overlays measure fragility and uncertainty.

``` math
p_{finish,ensemble} = \sum_{g}^{}w_{g}p_{finish,g}
```

Variables: g = path generator; w_g = pre-set generator weight; p_finish,g = terminal probability from generator g; p_finish,ensemble = combined terminal estimate before calibration and gates.

``` math
generator\_ dispersion = \max_{g}\left| p_{finish,g} - {median}_{g}\left( p_{finish,g} \right) \right|
```

Variables: generator_dispersion = largest disagreement between a generator and the median generator estimate. High dispersion means model risk, not a stronger signal.

| **Generator** | **v1 role** | **Weighting rule** |
|----|----|----|
| Empirical path fragments | Primary fair-value estimate because it preserves real short-horizon wicks and jumps. | Highest weight at launch, frozen before validation. |
| Block or stationary bootstrap | Checks dependence assumptions by resampling dependent return blocks. | Secondary estimate and uncertainty input. |
| Filtered historical simulation | Normalizes historical shocks by volatility and rescales to the current sigma_tau. | Secondary estimate, useful in changing volatility regimes. |
| Stress overlays | Tests adverse wicks, source disagreement, and sudden volatility expansion. | Should raise required edge or block; it should not improve fair value. |

Promotion rule: if a candidate trade only looks attractive under one generator, the system should block it or demand more edge. Generator weights can be learned later only after enough live shadow rows exist for each asset and horizon.

## 16.7 Event and news source taxonomy

Event features should be mechanical. A scheduled release can block or demand more edge immediately because its timestamp is known. Breaking news should be logged at first and promoted only after timestamped validation proves it improves decisions.

| **Event family** | **Examples** | **First implementation action** |
|----|----|----|
| Scheduled macro | CPI, PPI, PCE, NFP, FOMC decision, FOMC minutes, major Fed speeches. | Hard-block or demand more edge inside the configured event window. |
| Crypto-specific scheduled events | Major protocol upgrades, ETF decision deadlines, known unlocks, exchange maintenance. | Log by asset scope; block only if historically disruptive. |
| ETF/options context | IBIT/ETHA option IV, skew, risk reversal, volume, open interest, quote age. | Adjust uncertainty or required edge after ablation support; do not directly adjust Chainlink-only `sigma_tau`. |
| GEX and structure context | Gamma flip zone, large option strike concentration, nearby high-gamma levels. | Treat as structure-risk input, not a direct trade trigger. |
| Breaking news | Regulatory headline, exchange outage, chain halt, liquidation cascade, custody/security incident. | Log first; future blocker only after timestamped validation. |
| Exchange/chain status | Binance/Coinbase/Kraken status, Solana/Ethereum chain disruption, oracle interruption. | Immediate data-quality or asset-level block if source integrity is affected. |

Design rule: news and GEX may adjust volatility, uncertainty, or required edge. They should not create trades by themselves until they pass ablation tests against the core engine.

## 16.8 Portfolio stress tests

The allocator should be tested against bad clusters, not only average trades. Binary contracts can lose together when they share expiry, event state, liquidity, or crypto beta even if BTC and ETH signals are generated independently.

| **Stress scenario** | **What to test** | **Pass condition** |
|----|----|----|
| All open contracts lose | Worst case across current active positions. | Loss stays below hard exposure and drawdown caps. |
| BTC and ETH gap together | Common crypto beta shock during high-volatility tape. | Portfolio exposure is capped even if signals are independent. |
| Book disappears after entry | Liquidity vanishes or the bid-ask spread widens sharply. | Sizing and kill switch prevent runaway exposure. |
| Source disagreement near expiry | Chainlink/RTDS/proxy prices diverge near threshold. | Ambiguous labels and trades are blocked or low confidence. |
| Macro event wick through K | Scheduled release creates a fast threshold touch. | Event-risk gates reduce or block exposure. |
| Model overconfidence shock | Assume probabilities are overstated by 5-10 percentage points. | Expected drawdown remains acceptable under fractional Kelly caps. |
| Crowded same-window signals | Multiple trades share the same expiry or threshold region. | Same-window cap prevents concentration. |

## 16.9 Validation governance

The research process needs governance so the system does not accidentally search until something looks good. Exploration is allowed, but promotion to a live rule requires frozen data, frozen parameters, and a final untouched validation pass.

| **Governance rule** | **Implementation** |
|----|----|
| Pre-register first validation config | Freeze thresholds, generator weights, costs, and pass/fail rules before the first serious validation run. |
| Final holdout stays untouched | No model, gate, or threshold should be tuned on the final holdout. |
| Version every result | Record data snapshot, code commit, model version, feature version, gate version, and parameter file. |
| Report failed experiments | Keep a research registry so survivorship bias is visible. |
| Track number of tested variants | Prevent multiple-testing illusion by counting how many configurations were tried. |
| Separate exploration from promotion | Use exploration data for ideas and promotion data for decisions. |
| Ablation before complexity | Each new layer must improve out-of-sample EV, calibration, drawdown, or robustness. |

## 16.10 Model drift and rollback

Any promoted calibration or XGBoost layer should have monitoring and rollback rules. A model can pass once and still drift when volatility regimes, liquidity, market participants, or data feeds change.

| **Monitoring trigger** | **Action** |
|----|----|
| Calibration drift | Return ML layer to advisory mode or increase required edge until recalibrated. |
| False-positive spike | Disable trade approval, keep blocker-only mode, and inspect recent regime. |
| Opportunity deletion spike | Check whether XGBoost is overblocking valid Monte Carlo opportunities. |
| Asset-specific failure | Split BTC and ETH calibration or disable the failing asset scope. |
| Feature distribution shift | Block ML influence if live features move outside training distribution. |
| Model version regression | Rollback to the last validated model version and preserve the incident log. |
| Unexpected missing features | Fail closed rather than imputing blindly near live decisions. |

## 16.11 Incident runbook and alert thresholds

The dashboard is part of the safety system. If it is stale, disconnected, or unable to show block reasons, the system should treat that as an operational issue rather than a cosmetic problem.

| **Alert** | **Operator action** |
|----|----|
| Settlement source stale | Block affected asset, inspect feed health, and resume only after fresh source and proxy agreement. |
| Polymarket WebSocket gap | Reconnect, pull a fresh book snapshot, reconcile hash/state, and mark the gap in raw logs. |
| Order-book mismatch | Block the market until snapshot and stream state agree. |
| Model output missing or NaN | Disable decisions for that model version and inspect state builder/config. |
| Database lag | Continue raw logging if possible; block decisions if the as-of state cannot be written reliably. |
| Dashboard stale | Soft kill new entries until operator visibility is restored. |
| Kill switch fired | Record trigger, scope, open exposure, outstanding orders, logs, and require manual reset. |
| Unexpected market rule | Block the market and add a parser fixture before any future use. |
| API/auth/signing failure | Cancel outstanding orders if live mode exists, then hard-kill automation. |
| Raw exit signals cluster without source consensus | Freeze model-driven exits, block new entries, keep logging, and wait for source/book reconciliation. |
| Premature-exit rate breaches tolerance in shadow | Demote exit policy to research mode and compare against hold-to-expiry baseline. |

Reset rule: a hard kill should not auto-reset. Resuming live execution requires a logged operator review, confirmed data freshness, and a clean dashboard state.

## 16.12 Legal, platform, tax, and data-rights checklist

This architecture can be researched in read-only mode without making execution decisions, but a real-money version requires non-model checks. These items should be completed before any live trading system is connected to capital.

| **Area** | **Required check before live execution** |
|----|----|
| Platform eligibility | Confirm the user, jurisdiction, venue, and account are allowed to trade the target contracts. |
| Terms and API usage | Confirm collection, storage, automation, and order placement comply with platform terms and API rules. |
| Data rights | Confirm which live and historical feeds may be stored, replayed, used for research, and used for live decisions. |
| Tax and accounting | Define recordkeeping, realized/unrealized PnL treatment, and exportable transaction logs. |
| Wallet and key security | Use hot-wallet limits, scoped API keys, no plaintext secrets, withdrawal controls, and separation between research and live funds. |
| Audit trail | Keep order, signal, rule, model, kill-switch, and operator-action logs. |
| Capital limits | Set max capital at risk, per-asset exposure, per-window exposure, and loss limits before enabling execution. |
| Review requirement | Treat this as a separate compliance and operational review, not a modeling decision. |

## 16.13 Exit strategy implementation defaults

The first build should treat early exits as a research layer until the live shadow record proves they add value. The system should always report hold-to-expiry results next to exit-policy results so the operator can see whether early exits are helping or merely reacting to noise.

| **Exit-control item** | **Initial rule** | **Why** |
|----|----|----|
| Primary exit candidate | Noise-aware stop-and-exit. | Simpler and cheaper than reversing; easier to validate. |
| Challenger exit | Stop-and-reverse only when the opposite side has independent edge. | Prevents unnecessary round-trip book-crossing cost. |
| p_no_touch role after entry | Cannot force exit by itself. | Path instability matters, but terminal payoff is still determined at expiry. |
| Hysteresis and dwell | Required before confirmed exit. | Reduces one-tick and source-noise exits. |
| Executable exit price | Use bid-side target-size VWAP for held long outcomes. | Midpoint exits are not tradable. |
| Promotion standard | Must beat hold-to-expiry after costs and keep premature_exit_rate within tolerance. | Keeps the rule empirical. |

# 17. Final Architecture and Conclusion

Figure 10. Final architecture. The system starts as read-only infrastructure, then becomes a validated decision engine only after shadow results pass.

The updated architecture is no longer just a BTC probability model. It is a multi-asset, hybrid decision and portfolio framework for short-dated Polymarket crypto binaries. BTC and ETH contracts are priced independently. The system estimates whether each contract is mispriced, then applies execution, exit-strategy, path-risk, source-quality, news-risk, portfolio, XGBoost, and kill-switch controls before any trade can exist.

The most important design choice is the hybrid rule. p_finish remains the fair-value anchor because the contract settles to a binary payoff. p_no_touch, z_path, structure risk, event risk, source quality, and execution quality decide whether that fair value is tradable. This keeps the model mathematically clean while still respecting the real reason the original idea matters: short-dated binaries are path-sensitive in practice even when the final payoff is terminal. The exit-strategy layer extends this principle: p_no_touch and z_path can move an open position into watch mode, but a real exit requires confirmation, fresh executable bid/ask pricing, and a cost-adjusted comparison between holding and selling.

The next step is not adding more indicators. The next step is collecting the right data while locking down the implementation specifications above. The project should start logging BTC and ETH contracts, Chainlink and proxy prices, Polymarket order books, raw trades, rule text, shadow decisions, event flags, and post-expiry labels as soon as possible. Without that live as-of dataset, the system cannot know whether the apparent edge survives executable prices, path instability, and operational friction.

A production version should only come after shadow validation passes explicit standards for executable EV, calibration, drawdown, edge decay, data quality, portfolio concentration, failure modes, and kill-switch behavior. If those standards are met, the architecture becomes a disciplined research-to-execution pipeline. If they are not met, the system still produces valuable research by showing exactly where the edge disappears.

# References and Data Documentation

\[1\] Polymarket Documentation, “Resolution.” Notes UMA Optimistic Oracle resolution mechanics. https://docs.polymarket.com/concepts/resolution

\[2\] Polymarket Documentation, “Real-Time Data Socket.” Describes RTDS streaming and supported crypto price symbols. https://docs.polymarket.com/market-data/websocket/rtds

\[3\] Polymarket Documentation, “Prices & Orderbook.” Describes order-book concepts and limit-order treatment. https://docs.polymarket.com/concepts/prices-orderbook

\[4\] Polymarket Documentation, “Market Channel” and “Orderbook.” Describes real-time order-book updates and REST order-book access. https://docs.polymarket.com/market-data/websocket/market-channel and https://docs.polymarket.com/trading/orderbook

\[5\] Polymarket BTC Up/Down market example. Rule text identifies Chainlink BTC/USD as the resolution source. https://polymarket.com/event/btc-updown-5m-1774121700

\[6\] Polymarket ETH Up/Down market example. Rule text identifies Chainlink ETH/USD as the resolution source. https://polymarket.com/event/eth-updown-5m-1780065900

\[7\] Polymarket SOL Up/Down market example. Rule text identifies Chainlink SOL/USD as the resolution source. https://polymarket.com/event/sol-updown-5m-1780060800

\[8\] Polymarket Documentation, “Resolution” and current crypto market pages. Practical note: rule text remains the authority for each contract.

\[9\] Politis, D. N., and Romano, J. P. (1994). “The Stationary Bootstrap.” Journal of the American Statistical Association. https://www.ssc.wisc.edu/~bhansen/718/Politis%20Romano.pdf

\[10\] scikit-learn documentation, `brier_score_loss`. Defines Brier score for probabilistic binary outcomes. https://scikit-learn.org/stable/modules/generated/sklearn.metrics.brier_score_loss.html

\[11\] scikit-learn documentation, `TimeSeriesSplit`. Describes validation for time-ordered data. https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html

\[12\] scikit-learn documentation, `log_loss`. Describes probabilistic log loss. https://scikit-learn.org/stable/modules/generated/sklearn.metrics.log_loss.html

\[13\] scikit-learn documentation, `calibration_curve`. Describes reliability diagrams for probability calibration. https://scikit-learn.org/stable/modules/generated/sklearn.calibration.calibration_curve.html

\[14\] XGBoost documentation, “Parameters.” Notes probability-producing binary classification objectives such as `binary:logistic`. https://xgboost.readthedocs.io/en/stable/parameter.html

\[15\] XGBoost documentation, “Monotonic Constraints.” Describes constraints for enforcing directional feature relationships. https://xgboost.readthedocs.io/en/latest/tutorials/monotonic.html

\[16\] XGBoost documentation, “Feature Interaction Constraints.” Describes restricting which variables may interact. https://xgboost.readthedocs.io/en/stable/tutorials/feature_interaction_constraint.html

\[17\] Kevin Davey, KJ Trading Systems, “What 567,000 Backtests Taught Me About Algo Trading Exits.” Used as a research prompt for testing stop-and-exit and stop-and-reverse policies; the paper still requires Polymarket-specific validation. https://kjtradingsystems.com/algo-trading-exits.html

---

## Operational Material Moved Out of the Research Paper on 2026-05-31

The main paper was tightened so it reads as a research idea rather than an implementation manual.
The material below was moved out of the paper, not deleted. It should be used when building the
engine, replay harness, execution simulator, dashboard, and validation workflow.

#### 2.2 Rule parser requirements

| Field | Required treatment |
| --- | --- |
| market_id | Unique Polymarket identifier. |
| asset | BTC, ETH, or later SOL. |
| contract_type | Up/Down, above/below threshold, range, or other rule family. |
| side | UP, DOWN, YES, NO, or venue-specific outcome token. |
| start_time | Required for Up/Down contracts that compare end price to start price. |
| end_time  /  expiry | Exact timestamp used to score the final price. |
| K | Venue-defined threshold or start reference price. |
| comparison_operator | ,  ,  , or   exactly as written. |
| settlement_source_id | Chainlink stream, official venue source, or validated proxy if no direct stream is available. |
| rule_text_hash | Hash of the rule text at collection time. |
| resolution_status | Pending, proposed, disputed, resolved, canceled, or invalid. |

#### 2.4 Rule edge cases that must be explicitly tested

| Edge case | Why it matters | First treatment |
| --- | --- | --- |
| Greater-than vs greater-than-or-equal | Ties can flip the outcome near the boundary. | Store operator exactly from rule text. |
| Start price vs fixed threshold | Up/Down markets may compare end price to start price, not a static external K. | Capture  start_reference_price  separately from fixed K. |
| Missing final tick | The final Chainlink update may not align perfectly with the contract end second. | Store source timestamp and selection rule. |
| Delayed source update | A stale price may produce a false state. | Use source freshness gate. |
| Disputed resolution | Outcome may not finalize immediately. | Store  resolution_status  and delay final label. |
| Market cancellation | Invalid markets should not be treated as normal losses or wins. | Exclude or label separately. |
| Time-zone ambiguity | Contract title and source timestamp may use different zones. | Normalize all timestamps to UTC. |
| Rule text change | Backtests become invalid if old rules are overwritten. | Store rule text and hash at collection time. |

#### 4.1 Required data lanes

| Data lane | Required fields | Purpose |
| --- | --- | --- |
| Contract rules | market_id , asset, side, expiry, rule text, rule hash, settlement source | Defines the object being priced. |
| Polymarket order book | bids, asks, depth, timestamp, quote age, order-book hash if available | Defines executable entry and liquidity. |
| Polymarket trades | trade price, size, side if available, timestamp | Helps validate activity and reconstruct fills. |
| Settlement source | Chainlink stream value, timestamp, source status | Defines   and  . |
| Spot proxies | Binance, Coinbase, Kraken, robust basket, feed disagreement | Quality checks only; not volatility inputs. |
| Chainlink live ticks | 1-second or tick-level Chainlink price data | Needed for  p_no_touch , wick risk, and volatility. |
| News/event calendar | macro releases, Fed events, ETF events, exchange incidents, regulatory events | Risk context, gate, or required-edge buffer. |
| Future ETF/GEX context | IV, skew, flow, GEX level proximity, quote age | Later ablation layer, not v1 authority. |

#### 4.2 State object emitted at each decision time

The state builder should emit:

| Group | Fields |
| --- | --- |
| Contract state | timestamp,  market_id , asset, side,  , start reference, expiry, seconds left, rule hash, settlement source. |
| Price state | , robust  , source timestamp, feed disagreement, source quality flag. |
| Distance state | side-specific log distance,  z_path , distance to K in dollars and basis points. |
| Volatility state | short/medium/long realized-vol windows, volatility trend, volatility regime, volatility floor status. |
| Path-shape state | recent wick frequency, adverse wick size, threshold crossing count, congestion around K. |
| Order-book state | best bid, best ask, bid-ask spread, target-size VWAP, available depth, quote age, book update rate. |
| Event state | minutes to scheduled event, minutes since release, event importance, asset relevance, surprise if already released. |
| Model state | cache bucket, path generator outputs, ensemble probability, uncertainty, XGBoost blocker state. |
| Risk state | portfolio exposure, asset exposure, same-window exposure, daily PnL, drawdown, kill-switch state. |

#### 4.3 Data-quality principle

Data-quality problems should become explicit features, gates, or uncertainty buffers. They should never be silently ignored.

| Data problem | First treatment |
| --- | --- |
| Bad tick | Use robust median and feed agreement checks. |
| Stale Chainlink or RTDS price | Block if freshness fails. |
| Missing order-book depth | Block real trade simulation; log as lower-quality research row. |
| OHLC-only replay | Mark as lower confidence because intraperiod path ordering is unknown. |
| WebSocket gap | Block affected markets until snapshot reconciliation passes. |
| Source disagreement | Demand more edge or block. |
| Sparse path bucket | Increase  mc_uncertainty  or block. |

#### 5.6 Polymarket book-crossing clarification

The cost language in the edge equation should be read as book-crossing cost, not as a vague market-order assumption. Polymarket should be modeled from the order book: a buy pays the ask side or the target-size ask VWAP, while a sell or exit receives the bid side or the target-size bid VWAP. A user-facing instant trade is best represented as a marketable limit order that still depends on visible depth, quote freshness, latency, and maximum acceptable price.

Therefore, spread remains important, but the cleaner term for the formula is execution cost or book-crossing cost. The bid-ask spread is one component of that cost; depth, partial-fill risk, slippage, fees, latency, and order-book movement are the rest.

#### 6.4 Conditioning variables

Monte Carlo does not need every feature. It should condition on a small, stable set:

| Group | Variables | Reason |
| --- | --- | --- |
| Contract state | asset, side, seconds left, horizon type | Defines payoff and time window. |
| Distance state | ,  ,  ,  z_path | Defines current cushion. |
| Volatility state | sigma_tau , vol regime, vol trend | Scales path width. |
| Path-shape state | recent wick frequency, recent threshold crosses, adverse excursion | Captures unstable tape. |
| Source-quality state | source age, feed disagreement, data granularity | Prevents false precision. |
| Event state | macro/news window flag, minutes to event, minutes since event | Adjusts risk around scheduled and breaking events. |

Execution variables such as bid-ask spread, depth, target-size VWAP, and quote age should usually remain decision gates rather than path-generation variables. That keeps the path engine focused on price behavior and the decision engine focused on tradability.

#### 7.2 Execution modes

| Mode | Description | Stage |
| --- | --- | --- |
| Shadow only | Log hypothetical fills, no orders. | Required first stage. |
| Marketable-limit simulation | Simulate fills using order-book depth and latency stress. | Research and shadow validation. |
| Real limit with timeout | Place limit, cancel if not filled quickly. | Later live pilot only. |
| Hybrid execution | Marketable only for large edge; otherwise passive limits. | Future execution research. |

#### 7.3 Historical data tiers

If historical Tier 1 data is unavailable, the system should begin collecting it immediately.

| Tier | Data quality | Allowed use |
| --- | --- | --- |
| Tier 1 | WebSocket book events, periodic snapshots, trades, Chainlink/RTDS, exchange proxies, on-chain fills where possible | Real backtest candidate and live shadow validation. |
| Tier 2 | Best bid/ask snapshots with quote timestamps and some depth | Early research only; execution claims limited. |
| Tier 3 | Trades, charts, midpoint, or OHLC-only data | Not valid for executable EV claims. Useful only for rough falsification. |

#### 8.3 Order-book and market microstructure features

| Feature | Use |
| --- | --- |
| bid_ask_spread | Blocks small theoretical edges that disappear after crossing the bid/ask or paying target-size VWAP. |
| available_depth_target | Confirms enough size exists for target position. |
| target_vwap | Replaces midpoint with executable price estimate. |
| quote_age_ms | Blocks stale order-book states. |
| book_update_rate | Detects fast-moving or unstable book conditions. |
| depth_decay_1s  /  depth_decay_3s | Measures whether depth disappears under latency stress. |
| order_book_imbalance | Research feature; may indicate one-sided pressure or fragile liquidity. |
| last_trade_recency | Confirms whether the market is active or stale. |
| fillability_score | Combined score from depth, bid-ask spread, target-size VWAP, quote age, and latency stress. |

#### 8.4 Source-quality features

| Feature | Use |
| --- | --- |
| chainlink_age_ms | Blocks stale settlement-source price. |
| rtds_age_ms | Blocks stale venue-supported stream. |
| feed_disagreement_bps | Demands more edge or blocks when Chainlink and proxies diverge. |
| bad_tick_flag | Prevents one corrupt price from controlling  z_path . |
| source_switch_flag | Marks when fallback source is used. |
| data_granularity | Separates tick/1s data from lower-confidence OHLC replay. |

#### 8.5 News and event-risk features

News should be included, but as a risk context layer first. The system should not enter trades because a headline sounds bullish or bearish unless that feature later passes ablation. In v1, news and event features may adjust uncertainty, p_no_touch, or required edge. They must not directly recalculate sigma_tau; volatility remains Chainlink-only.

| Feature group | Examples | First use |
| --- | --- | --- |
| Scheduled macro | CPI, PCE, FOMC decision, FOMC minutes, NFP/jobs, unemployment, GDP, major Fed speeches | Block or demand more edge around high-impact windows. |
| Crypto-specific news | ETF approval/rejection headlines, regulatory actions, major exchange outages, chain halts, liquidation cascades | Event-risk flag and stress overlay. |
| ETF-related context | Spot ETF flow release windows, ETF market hours, ETF option IV/skew changes | Later uncertainty and risk-appetite context. |
| Breaking-news state | headline_time , source reliability, asset relevance, duplicate-news filter | Log first; use only after timestamped validation. |
| Post-release surprise | Actual minus consensus after release | Allowed only after release timestamp; never use revised or future data. |

A simple event-risk buffer can be:

Variables:

= added required-edge buffer from event risk.

event_importance = high, medium, or low expected market impact.

minutes_to_event = time until scheduled event, or negative after release.

asset_relevance = whether the event is broad macro, BTC-specific, ETH-specific, or crypto-wide.

recent_vol_response = observed volatility reaction after the event is known.

#### 8.6 Mandatory gates

A trade candidate must pass:

p_finish edge exceeds required edge after costs.

p_no_touch is strong enough for the side and horizon.

z_path shows enough cushion from the danger line.

Monte Carlo uncertainty and generator dispersion are below threshold or priced into edge.

Chainlink/source quality is fresh and consistent enough.

Polymarket bid-ask spread, depth, quote age, and target-size VWAP pass.

Rule parser has no unresolved edge case.

Structure risk around K does not show severe congestion or repeated threshold chopping.

News/event flags do not require a hard block.

Portfolio sizing allows the exposure.

XGBoost blocker, once promoted, does not reject the setup.

Kill switch is not active.

### 9. Validation, Pass/Fail Standards, and Ablation Plan

Purpose of this section: validation decides whether the idea is real. A backtest is not enough unless it is as-of, avoids future leakage, includes executable prices, and shows which model components actually improve the result.

The system should not be judged by whether a few trades look smart. It should be judged by as-of, executable, after-cost, out-of-sample performance.

Scikit-learn documents Brier score as a strictly proper scoring rule for probabilistic binary predictions, log loss as a probability-based loss, calibration curves as reliability diagrams, and TimeSeriesSplit as appropriate for time-ordered validation where training on future data would be invalid. Those concepts fit this project because the model outputs probabilities over binary outcomes and must be tested chronologically. [10][11][12][13]


#### 9.1 Validation rules

Use chronological walk-forward splits, never random train/test splits.

Keep a final untouched holdout period.

Evaluate executable expected value, not midpoint edge.

Include bid/ask crossing, target-size VWAP, fees, slippage, latency stress, quote-age stress, fill assumptions, and uncertainty buffers.

Evaluate by asset, side, horizon, time left, volatility regime, event regime, and liquidity bucket.

Compare live shadow logs against historical replay assumptions.

Report every blocked reason and missed opportunity.

Require ablation support before adding complexity to production rules.


#### 9.2 Initial pass/fail standards

These are starting research standards. They should be revised after the first shadow dataset is collected.

| Area | First pass/fail standard |
| --- | --- |
| Data quality | No real trading unless settlement source, order book, local clock, and proxy feeds are fresh and aligned. |
| Shadow sample size | Require thousands of decision rows and hundreds of candidate trades per major asset/horizon before making strong claims. |
| Executable EV | Positive after all costs and buffers on walk-forward and final holdout. |
| Confidence | Bootstrapped lower confidence bound on EV should remain positive before promotion. |
| Calibration | Probability buckets should not show severe overconfidence; reliability curves should be stable by asset/horizon. |
| Brier score | Must beat the market-implied or naive baseline on final holdout. |
| Log loss | Must beat baseline without hiding bad tails. |
| Drawdown | Shadow drawdown must stay below the preset research limit. |
| Edge decay | Signal must survive latency and worse-fill stress. |
| Concentration | No single asset, time window, event regime, or bucket should explain nearly all profit. |
| Opportunity deletion | Gates and XGBoost blockers must not delete most profitable opportunities. |
| Operational safety | Kill switch, stale-feed blocks, and error logging must work before live execution. |


#### 9.3 Ablation plan

| Ablation | Question answered |
| --- | --- |
| Core BTC Monte Carlo only | Does the original remaining-path engine have standalone signal? |
| Core ETH Monte Carlo only | Does ETH have its own signal after costs? |
| Core BTC/ETH plus structure gates | Do crossing, congestion, wick, and level features improve outcomes? |
| Core plus order-book execution model | Does the edge survive VWAP, depth, quote age, and latency stress? |
| Core plus event/news risk | Do macro and crypto event flags improve required-edge and block decisions? |
| Core plus portfolio sizing | Does risk-adjusted sizing improve return per drawdown? |
| Core plus XGBoost blocker | Does XGBoost reduce false positives out of sample without deleting too many winners? |
| Core plus ETF/GEX context | Does timestamped IV, skew, flow, or GEX improve volatility, no-touch, uncertainty, or required-edge decisions? |
| Live shadow vs historical replay | Do live as-of logs disagree with reconstructed historical assumptions? |
| Core plus stop-and-exit policy | Does a noise-aware early exit improve after-cost EV, drawdown, or tail loss versus holding to expiry? |
| Core plus stop-and-reverse challenger | Does reversing into the opposite outcome add value after round-trip book-crossing cost and independent edge checks? |
