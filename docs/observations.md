# Polymarket Probability Engine: Observations, Bugs, Recovery System, Runtime Stability, and ML Roadmap
## Project Context
This project is a read-only research system for short-dated BTC/ETH Polymarket binary contracts. It uses live price feeds, Polymarket order-book data, settlement-source observations, volatility features, Monte Carlo path simulation, and executable edge calculations.
The system should remain research/paper-trading only until the following are proven:
1. As-of replay equivalence
2. Probability calibration by contract slice
3. Stable runtime recovery after restarts
4. Source-quality and data-freshness gates
5. Executable edge after order-book costs
6. No look-ahead bias
7. No stale or invalid probability output after restart/degradation
8. Stable logging, health checks, and bug reports
9. Reliable K/threshold parsing and immutability
10. Valid sigma/volatility calculations
The current focus is not only bug fixing. Bugs are the starting point because the system cannot safely move into serious ML, calibration, or paper trading until runtime reliability is proven.
---
# 1. Current Known Bugs and Runtime Issues
This section lists the current observed bugs that must be treated as active issues, not future enhancements. These should be used as the initial bug inventory for the recovery system, health checks, TUI diagnostics, and LLM-ready bug reports.
---
## BUG-001: TUI Freeze With CPU Spike
### Observed behavior
The TUI sometimes freezes during live operation. When this happens:
- BTC/ETH price stops updating.
- Chart stops updating.
- Order book stops updating.
- Probability display may become stale.
- CPU usage spikes before or during the freeze.
- The system may appear live even though the displayed state is no longer fresh.
### Severity

CRITICAL

### Suspected Causes

* Blocking API call inside the TUI event/render loop.
* Probability computation or heavy polling blocking UI updates.
* Unbounded queue growth.
* WebSocket update backlog.
* Lock contention between state update and render logic.
* Too many redraws per second.
* Runtime worker consuming CPU and starving the TUI.
* JSON/API decode error not handled cleanly.
* TUI waiting on a dead/stale endpoint.

### Required behavior

The TUI must remain responsive even when the API, probability worker, WebSocket feed, or order book feed fails.

If live data is stale, the TUI should show one of the following states instead of freezing:

STALE
DEGRADED
API_BLOCKED
OFFLOAD_BLOCKED
RECOVERING
BLOCKED

### Suggested files to inspect

rust/crates/polymarket-cockpit-tui/src/event_loop.rs
rust/crates/polymarket-cockpit-tui/src/client.rs
rust/crates/polymarket-cockpit-tui/src/render/*
src/polymarket_engine/runtime_api.py

⸻

## BUG-002: API BLOCKED / Response Body Decode Error

### Observed behavior

The live system sometimes reports:

API BLOCKED
error decoding response body
error decoding response body from API response

Stopping and restarting containers sometimes temporarily fixes the issue, but the system may degrade again later.

### Severity

ERROR / CRITICAL

### Suspected causes

* API returns non-JSON response but client tries to decode as JSON.
* API returns blocked/rate-limited/HTML/error body.
* HTTP status code is not checked before body decode.
* Content type is not checked before JSON parse.
* Runtime API is unavailable during restart.
* Container networking is not ready when client starts.
* Retry/backoff logic is missing or too aggressive.
* Circuit breaker is missing for blocked upstream APIs.

### Required behavior

The system should never crash or freeze because an API response cannot be decoded.

Before decoding any response:

1. Check HTTP status code.
2. Check content type.
3. Handle empty body.
4. Handle blocked/rate-limited body.
5. Log response metadata.
6. Mark source as API_BLOCKED, STALE, or DEGRADED.
7. Retry with exponential backoff.
8. Avoid tight retry loops.

### Suggested files to inspect

rust/crates/polymarket-cockpit-tui/src/client.rs
src/polymarket_engine/runtime_api.py
src/polymarket_engine/ingestion/*

⸻

## BUG-003: System Degrades After Restart

### Observed behavior

After a PC restart or container restart, the system may not come back cleanly. Some services appear to work at first, then degrade.

### Known symptoms:

* Runtime errors appear after restart.
* Containers need manual stop/start to recover.
* API may show blocked or stale state.
* TUI may show live but not receive trustworthy updates.
* Sigma, target, order book, or probability inputs may be stale.
* Expensive probability work may start before the system is ready.

### Severity

CRITICAL

### Suspected causes

* Services start in the wrong order.
* No recovery state machine.
* No warmup period after restart.
* Probability worker starts before feeds are fresh.
* No readiness gate for offloading.
* Cached state is trusted too early.
* Runtime health checks are incomplete.
* One failed dependency causes downstream failure.
* Containers recover individually but not as a coordinated system.

### Required behavior

After restart, the system should enter:

BOOTING -> WARMING -> READY

or:

BOOTING -> WARMING -> DEGRADED / BLOCKED

The system should not run full Monte Carlo, GPU offload, or high path-count probability jobs until readiness gates pass for several consecutive cycles.

### Suggested files to inspect

src/polymarket_engine/ops/runtime_keeper.py
src/polymarket_engine/runtime_api.py
src/polymarket_engine/runtime_gates.py
src/polymarket_engine/probability/gpu_worker.py
src/polymarket_engine/probability/cpu_budget.py

⸻

## BUG-004: Offloading Work Too Soon After Restart

### Observed behavior

After restart, the system may begin expensive probability work too early, before live feeds, sigma, K, order book, and API health are stable.

This may cause:

* CPU spike soon after startup.
* Monte Carlo path budget ramps too quickly.
* GPU/probability worker starts before inputs are trustworthy.
* Probability outputs appear valid even when upstream data is stale.
* TUI freezes or becomes delayed.

### Severity

CRITICAL

### Suspected causes

* CPU budget logic does not check runtime phase.
* GPU worker does not check readiness.
* Warmup period is missing or too short.
* Consecutive healthy-cycle requirement is missing.
* Offload decision is based on capacity, not system trustworthiness.
* Probability worker treats stale inputs as usable.

### Required behavior

Add an OffloadReadinessGate.

Expensive probability work should be blocked unless:

runtime_phase == READY
price feed fresh
order book fresh
sigma valid
K stable
API healthy
DuckDB healthy
CPU/memory below limits
queue length acceptable
no recent API_BLOCKED state
no recent decode errors
consecutive healthy cycles >= required threshold

### Suggested files to inspect

src/polymarket_engine/probability/offload_gate.py
src/polymarket_engine/probability/gpu_worker.py
src/polymarket_engine/probability/cpu_budget.py
src/polymarket_engine/ops/runtime_keeper.py
src/polymarket_engine/runtime_gates.py

⸻

## BUG-005: Sigma Calculation Instability

### Observed behavior

Sigma calculations appear to load and unload repeatedly. Some components may be having trouble communicating. This may cause probability instability or stale probability output.

### Severity

ERROR

### Suspected causes

* Sigma task restarting repeatedly.
* Volatility inputs are stale or missing.
* NaN or invalid values are not handled.
* Volatility window lacks enough samples.
* Cache invalidation happens too often.
* Regime update triggers unnecessary recomputation.
* Race condition between price feed and sigma calculation.
* Probability worker runs even when sigma is invalid.

### Required behavior

The sigma engine should expose diagnostics:

sigma_tau
sigma_valid
sigma_age_ms
last_sigma_update_ts
short_vol
medium_vol
long_vol
volatility_floor_applied
regime_multiplier_applied
failure_reason
input_sample_count

If sigma is missing, stale, NaN, or invalid:

probability_state = BLOCKED_OR_STALE
offload_allowed = false

The system should never produce confident probabilities from invalid sigma inputs.

### Suggested files to inspect

src/polymarket_engine/features/volatility.py
src/polymarket_engine/probability/hot_inputs.py
src/polymarket_engine/probability/runtime_inputs.py
src/polymarket_engine/probability/gpu_worker.py

⸻

## BUG-006: K / Threshold Alternates Between Two Prices

### Observed behavior

The contract threshold/reference price K appears to glitch and alternate between two prices.

This is dangerous because K determines the contract boundary. If K changes incorrectly, then distance_to_threshold, z_path, p_finish, p_no_touch, and executable edge may all become invalid.

### Severity

CRITICAL

### Suspected causes

* Confusion between current price, start price, threshold price, and settlement price.
* Market rule parser updates K repeatedly.
* K is re-parsed from different sources.
* Race condition between rule parser and price feed.
* UP/DOWN side interpretation changes K.
* Proxy feed is being used as threshold.
* Contract target state is not immutable after initialization.

### Required behavior

After contract rule parsing, K should be immutable unless the venue rule explicitly changes.

Log every K assignment:

contract_id
market_slug
asset
side
K
K_source
rule_hash
timestamp
previous_K
new_K
reason_for_change

If K changes unexpectedly, emit:

THRESHOLD_MUTATION_ERROR

and block probability output for that contract until reviewed.

### Suggested files to inspect

src/polymarket_engine/domain/contract_rules.py
src/polymarket_engine/domain/contracts.py
src/polymarket_engine/domain/market_state.py
src/polymarket_engine/features/state_builder.py
src/polymarket_engine/probability/runtime_inputs.py

⸻

## BUG-007: Probability Overconfidence / Underconfidence

### Observed behavior

The Monte Carlo probabilities are often either underestimated or overestimated. Sometimes the model is too confident even when TTE is not very low yet.

Symptoms:

* p_finish may be too high in unstable states.
* Model may underestimate wick/reversal risk.
* Model may be overconfident when price is near K.
* Model may be poorly calibrated by TTE, volatility regime, or threshold distance.
* Probability can feel directionally right but numerically wrong.

### Severity

MODEL_RISK / RESEARCH_BLOCKER

### Suspected causes

* sigma_tau too small.
* Monte Carlo paths too narrow.
* Fat tails and final-window wicks underrepresented.
* Volatility floor too low.
* Path generator too calm.
* Sparse historical buckets.
* Order-book pressure not included in probability model.
* Model not calibrated by TTE/z_path/regime.
* Polymarket price/order book not correctly separated from settlement probability.

### Required behavior

Do not replace Monte Carlo yet. Add calibration and diagnostics.

Required calibration reports:

Brier score
log loss
calibration curve
expected calibration error
bucket win rate
bucket sample count

Required calibration slices:

TTE bucket
z_path bucket
distance from threshold
volatility regime
UP vs DOWN
asset
spread/depth bucket
order-book imbalance bucket
final 30-60 second window
threshold congestion bucket

Model direction

Use:

Monte Carlo = base probability engine
ML = calibration/meta-model layer

Calibration model implementation is intentionally out of scope for this pass.
Keep this lane to replay-safe datasets and reports until a separate model plan
is approved.

### Suggested files to inspect

src/polymarket_engine/probability/*
src/polymarket_engine/features/*
src/polymarket_engine/storage/*
scripts/check_probability_latency.py
tests/test_probability*

⸻

## BUG-008: Services Having Trouble Communicating

### Observed behavior

Some components appear to have trouble talking to each other. This may happen after restart or during runtime degradation.

Symptoms:

* TUI shows stale state.
* API health may not match live data health.
* Probability worker may run on stale inputs.
* Sigma may update separately from probability input state.
* Containers may individually appear alive but the system is not coherent.

### Severity

ERROR / CRITICAL

### Suspected causes

* Missing service dependency checks.
* Missing heartbeat between services.
* Runtime API reports process health but not data freshness.
* State files or database writes are stale.
* TUI polls an endpoint that is technically alive but semantically stale.
* No shared boot_id or runtime generation ID after restart.

### Required behavior

Add coordinated runtime health using:

boot_id
service_id
last_heartbeat_ts
last_successful_update_ts
data_age_ms
runtime_phase
dependency_status

A service should not be considered healthy only because the process is alive. It must also prove that its data is fresh.

### Suggested files to inspect

src/polymarket_engine/ops/runtime_keeper.py
src/polymarket_engine/runtime_api.py
src/polymarket_engine/storage/*
rust/crates/polymarket-cockpit-tui/src/client.rs

⸻

## BUG-009: No Automatic Bug Report Pipeline

### Observed behavior

When bugs occur, they are difficult to trace immediately. Important runtime context may be lost unless manually observed.

### Severity

HIGH_PRIORITY_TOOLING_GAP

### Required behavior

Build a structured bug-report system that automatically captures:

bug_id
boot_id
timestamp
runtime_phase
service
severity
contract_id
market_slug
asset
side
TTE
K
current_price
price_age_ms
orderbook_age_ms
sigma_tau
sigma_valid
probability_state
offload_allowed
offload_block_reasons
api_status
websocket_status
duckdb_status
cpu_percent
memory_mb
queue_length
last_error
stack_trace
recent_logs
suspected_module
suggested_files_to_inspect
suggested_tests_to_run

The bug report should be LLM-ready and should generate a prompt such as:

A runtime bug occurred in the Polymarket probability engine. Diagnose the likely cause and propose a minimal safe patch. Use the bug report, stack trace, recent logs, and relevant source files. Do not change unrelated architecture. Add or update tests. Explain the root cause, the fix, and how to verify it.

⸻

Current Bug Priority Order

Fix in this order:

1. BUG-001: TUI freeze with CPU spike
2. BUG-004: Offloading work too soon after restart
3. BUG-003: System degradation after restart
4. BUG-002: API BLOCKED / decode errors
5. BUG-006: K threshold mutation
6. BUG-005: Sigma instability
7. BUG-008: Service communication/health mismatch
8. BUG-009: Automatic bug report pipeline
9. BUG-007: Probability calibration/model risk

Runtime stability comes before ML. The system should not train or trust ML until the data pipeline, replay state, K, sigma, and labels are reliable.

⸻

# 2. Runtime Recovery State Machine

Create a runtime phase system with explicit states:

BOOTING
WARMING
RECOVERING
DEGRADED
READY
BLOCKED

⸻

BOOTING

Used immediately after process/container/system startup.

Rules:

* Do not run full Monte Carlo.
* Do not increase path budget.
* Do not trust cached probability outputs unless marked as stale/last-good.
* Start collectors, normalizer, API, and storage checks.
* Write startup timestamp and boot ID.

⸻

WARMING

Used when services are alive but data has not proven itself fresh.

Requirements before leaving WARMING:

* Price feed fresh
* Order book fresh
* Target/K loaded and stable
* Volatility status fresh
* Sigma valid
* Probability inputs fresh
* DuckDB readable/writable
* API health OK
* Normalized health OK
* No decoding errors for N consecutive cycles
* No unexpected K mutation
* CPU and memory below thresholds
* TUI receives updates

⸻

RECOVERING

Used after a failure, blocked API response, stale feed, crash, or container restart.

Rules:

* Keep system alive but conservative.
* Preserve last-good state only as stale/reference output.
* Do not produce confident new probability outputs until freshness gates pass.
* Do not increase Monte Carlo path budget.
* Do not offload to GPU unless required inputs are stable.
* Generate a recovery report.

⸻

DEGRADED

Used when the system is partially alive but not fully trustworthy.

Examples:

* API reachable but probability inputs stale
* Price feed live but order book stale
* Sigma missing or invalid
* K uncertain
* High CPU usage but UI still responsive
* Probability worker failing but live feed working

Rules:

* Show DEGRADED in TUI/API.
* Log reason codes.
* Continue collecting data if safe.
* Block trade decisions.
* Allow only low-cost diagnostics and nowcast/last-good display.

⸻

READY

Used only when all readiness checks pass.

Requirements:

* All required services healthy
* Live data fresh
* Order books fresh
* K stable
* Sigma valid
* Probability inputs fresh
* No recent API_BLOCKED state
* No recent decode errors
* CPU/memory within limits
* Probability worker updated successfully
* TUI is receiving current state

Only READY allows full probability production.

⸻

BLOCKED

Used when hard gates fail.

Examples:

* K mutation
* Invalid rule parse
* Settlement source missing
* Source disagreement above tolerance
* Sigma invalid
* API repeatedly blocked
* Live feeds stale beyond limit
* Database unavailable
* Runtime cannot recover after max attempts

Rules:

* Do not compute or display confident probabilities.
* Do not paper-trade.
* Preserve logs and bug report.
* Require manual review or controlled automated repair.

⸻

# 3. Offload Readiness Gate

Problem

The current system has CPU budgeting and GPU/probability worker logic, but it needs a startup/recovery gate that prevents expensive offload from starting too early.

The offload decision should not be based only on available compute. It should be based on whether the system is trustworthy enough to run expensive probability work.

⸻

Add an OffloadReadinessGate

The offload gate should answer:

Is the system healthy enough to run expensive probability work now?

Inputs:

runtime_phase
boot_id
uptime_seconds
consecutive_healthy_cycles
last_restart_ts
last_error_ts
price_age_ms
orderbook_age_ms
probability_input_age_ms
volatility_age_ms
outcome_status_age_ms
target_status_age_ms
sigma_tau_valid
sigma_tau_age_ms
k_stable
k_last_changed_ts
api_status
normalized_health_status
duckdb_status
websocket_status
cpu_percent
memory_mb
queue_length
last_probability_success_ts
last_probability_error_ts
last_api_blocked_ts

Output:

offload_allowed: true/false
reason_codes: list[str]
recommended_worker_mode: "disabled" | "nowcast_only" | "min_mc" | "normal_mc" | "gpu_mc"
recommended_max_total_paths

⸻

Offload rules

Offload should be blocked if:

runtime_phase != READY
uptime_seconds < warmup_min_seconds
consecutive_healthy_cycles < required_healthy_cycles
price feed stale
order book stale
probability inputs stale
sigma invalid or stale
K changed unexpectedly
API is blocked
database unavailable
CPU above soft max
memory above soft max
queue length too high
recent decoding error exists
recent WebSocket reconnect storm exists

⸻

Path budget ramp after restart

After restart, do not immediately jump to the configured max path count.

Use staged ramping:

Stage 0: disabled
Stage 1: nowcast only
Stage 2: min Monte Carlo paths
Stage 3: medium Monte Carlo paths
Stage 4: normal Monte Carlo paths
Stage 5: GPU/ensemble if stable

Example:

0-30 sec after restart: nowcast only
30-90 sec: min_total_paths only
90-180 sec: 25% max_total_paths
180-300 sec: 50% max_total_paths
after 300 sec + healthy cycles: normal adaptive budget

The exact values should be configurable.

⸻

# 4. Recovery System Design

Recovery system goal

The recovery system should detect failures, classify them, preserve context, attempt safe recovery, and prevent invalid probability output.

The recovery system should not blindly restart everything or allow an LLM to modify code without tests.

⸻

Recovery levels

Level 0: Observe only

Write structured logs and health reports.

Level 1: Soft recovery

Retry failed API/WebSocket requests with backoff.

Level 2: Component restart

Restart only the failed service, not the whole stack.

Examples:

restart probability worker
restart normalizer
restart collector
restart API
restart TUI

Level 3: Full runtime restart

Restart the full stack only if component restart fails.

Level 4: LLM-assisted diagnosis

Generate a bug report and prompt for Codex/LLM.

Level 5: Controlled patch generation

LLM proposes a patch, but tests must pass before merge/restart.

⸻

Recovery safety rules

Do not restart repeatedly in a tight loop.

Add:

max_restarts_per_hour
cooldown_seconds_after_restart
max_consecutive_failures
manual_review_required_after_n_failures

If the system exceeds the restart limit:

runtime_phase = BLOCKED

⸻

# 5. Bug Report System

Every automatic bug report should include:

bug_id
boot_id
timestamp
runtime_phase
service
container_name
severity
contract_id
market_slug
asset
side
TTE
K
K_source
K_last_changed_ts
current_price
last_price_update_age_ms
last_orderbook_update_age_ms
last_probability_input_age_ms
last_probability_output_age_ms
sigma_tau
sigma_tau_age_ms
sigma_valid
cpu_percent
memory_mb
queue_length
api_status
normalized_health
duckdb_status
websocket_status
probability_worker_status
offload_allowed
offload_block_reasons
last_error
stack_trace
recent_logs
recent_runtime_events
last_successful_state_snapshot
suspected_module
suggested_files_to_inspect
suggested_tests_to_run

⸻

Bug report examples

TUI freeze

Trigger if:

tui_receive_lag_ms > threshold
no TUI state update for N seconds
CPU spike occurs before stale UI
runtime updates queue grows too large

Suggested files to inspect:

rust/crates/polymarket-cockpit-tui/src/event_loop.rs
rust/crates/polymarket-cockpit-tui/src/client.rs
rust/crates/polymarket-cockpit-tui/src/render/*
src/polymarket_engine/runtime_api.py

API decode error

Trigger if:

response body cannot be decoded
status code is non-200
content-type is not JSON when JSON is expected
API returns blocked/rate-limited/error page

Suggested files to inspect:

rust/crates/polymarket-cockpit-tui/src/client.rs
src/polymarket_engine/runtime_api.py
src/polymarket_engine/ingestion/*

K mutation

Trigger if:

K changes for same contract without explicit rule-level reason
K alternates between two values
threshold source changes unexpectedly

Suggested files to inspect:

src/polymarket_engine/domain/contract_rules.py
src/polymarket_engine/domain/contracts.py
src/polymarket_engine/domain/market_state.py
src/polymarket_engine/features/state_builder.py
src/polymarket_engine/probability/runtime_inputs.py

Sigma instability

Trigger if:

sigma_tau is missing
sigma_tau is NaN
sigma_tau flips between values too aggressively
sigma_tau is stale
volatility status is stale

Suggested files to inspect:

src/polymarket_engine/features/volatility.py
src/polymarket_engine/probability/hot_inputs.py
src/polymarket_engine/probability/runtime_inputs.py

Offload too soon

Trigger if:

probability worker runs expensive MC while runtime_phase != READY
path budget increases during WARMING or RECOVERING
GPU worker starts before freshness checks pass
CPU spikes before system reaches READY

Suggested files to inspect:

src/polymarket_engine/probability/cpu_budget.py
src/polymarket_engine/probability/gpu_worker.py
src/polymarket_engine/ops/runtime_keeper.py
src/polymarket_engine/runtime_gates.py
src/polymarket_engine/runtime_api.py
scripts/check_probability_latency.py

⸻

# 6. TUI Stability Requirements

Current concern

The TUI can freeze when live price, chart, and order book stop updating, while CPU usage spikes.

Required fixes

The TUI should never block on:

API request
JSON decode
Monte Carlo
disk write
database query
large render calculation
unbounded event backlog

Specific TUI changes to consider

1. Use bounded channels or cap drained updates per frame.
2. Add render timing metrics.
3. Add update queue length metrics.
4. Add last successful receive timestamp.
5. Add stale UI detection.
6. Show runtime phase visibly.
7. If API fails, keep UI responsive and show the error.
8. If live stream fails, fall back to polling with backoff.
9. If probability endpoint fails, keep live price/order book updating.
10. If price/order book live data is stale, display STALE instead of freezing.

TUI status panel should include

runtime_phase
api_state
live_feed_state
probability_state
orderbook_state
volatility_state
sigma_state
offload_state
last_update_age_ms
tui_receive_lag_ms
cpu_percent
memory_mb
queue_length

⸻

# 7. Probability and ML Plan

Do not replace Monte Carlo yet

The project should move toward:

Monte Carlo = base probability engine
ML = calibration/meta-model layer

The first ML system should not directly output buy/sell.

It should output:

calibrated probability of final win/loss

The decision layer should still compute:

edge = p_finish_final - executable_price - costs - uncertainty_buffer - path_risk_buffer

⸻

ML roadmap

Phase 0: Dataset first

Before training ML, log every as-of decision state and final label.

Required dataset fields:

state_id
contract_id
market_slug
asset
side
asof_ts
expiry_ts
TTE
K
current_price
distance_to_threshold
z_path
sigma_tau
p_finish_MC
p_no_touch_MC
MC_generator_dispersion
spread
best_bid
best_ask
midpoint
target_size_vwap
visible_depth
orderbook_imbalance
quote_age_ms
source_age_ms
source_disagreement
threshold_cross_count
near_threshold_congestion
recent_wick_size
volatility_regime
event_window_flag
probability_model_version
final_label
resolved_outcome
settlement_price_at_expiry
skip_or_block_reason

⸻

Phase 1: Calibration reports

Before training models, build calibration curves by:

TTE bucket
z_path bucket
distance bucket
volatility regime
asset
side
spread/depth bucket
order-book imbalance bucket
final 30-60 second window
threshold congestion bucket

Metrics:

Brier score
log loss
calibration curve
expected calibration error
bucket win rate
sample count per bucket

⸻

Calibration model training is deferred. Do not implement a baseline or tree
calibrator from this observations pass; continue only with replay-safe dataset
and report plumbing.

Future modeling validation, architecture, and baseline choices belong to a
separate approved plan. This observations pass does not define model families,
training order, or calibration-output behavior.

⸻

# 8. Immediate Implementation Priorities

Priority 1: Stabilize current bugs

Fix the currently observed runtime bugs before adding major new modeling features:

### BUG-001 TUI freeze with CPU spike
### BUG-002 API BLOCKED / response decode errors
### BUG-003 degraded runtime after restart
### BUG-004 offload starts too soon after restart
### BUG-005 sigma instability
### BUG-006 K threshold mutation
### BUG-008 service communication mismatch
### BUG-009 missing automatic bug-report pipeline

Do not start full ML development until these are logged, reproducible where possible, and covered by basic health checks or tests.

⸻

Priority 2: Build RuntimeRecoveryManager

Create:

src/polymarket_engine/ops/recovery_manager.py

Responsibilities:

track boot_id
track runtime_phase
track startup timestamp
track restart events
track service health
track readiness gates
track recovery attempts
write recovery_status.json
write bug reports

⸻

Priority 3: Add OffloadReadinessGate

Create:

src/polymarket_engine/probability/offload_gate.py

Responsibilities:

decide if GPU/MC work is allowed
enforce warmup after restart
enforce consecutive healthy cycles
cap path budget during recovery
block offload if state is stale
return reason codes

⸻

Priority 4: Integrate offload gate into probability worker

Modify:

src/polymarket_engine/probability/gpu_worker.py

Required behavior:

if offload not allowed:
    skip expensive MC
    write status with state="OFFLOAD_BLOCKED"
    preserve last_good_rows if available
    do not increase path budget

⸻

Priority 5: Add recovery and offload status to API

Modify:

src/polymarket_engine/runtime_api.py

Add endpoints:

/api/runtime/recovery
/api/runtime/offload
/api/runtime/bug-reports

Also include recovery/offload status in:

/api/runtime/live

⸻

Priority 6: Add TUI visibility

Modify:

rust/crates/polymarket-cockpit-tui/src/status.rs
rust/crates/polymarket-cockpit-tui/src/render/systems.rs
rust/crates/polymarket-cockpit-tui/src/event_loop.rs

Display:

runtime_phase
offload_allowed
offload_block_reason
recovery_attempts
boot_id
last_restart_age
last_bug_id

⸻

Priority 7: Add tests

Add tests for:

restart warmup blocks offload
consecutive healthy cycles allow offload
stale price blocks offload
stale order book blocks offload
invalid sigma blocks offload
K mutation blocks offload
API_BLOCKED blocks offload
CPU spike blocks budget increase
RECOVERING phase preserves last-good rows
TUI remains responsive when probability endpoint fails

⸻

Priority 8: Build model calibration dataset

After runtime stability is improved, begin logging replay-safe model data.

Required work:

log every as-of state
log final labels
log skip/block reasons
log model version
log feature version
log K source and rule hash
log sigma diagnostics
log order-book state
log executable price assumptions

⸻

Priority 9: Deferred calibration model work

No model implementation belongs to this pass. Revisit only under a separate
approved plan after the dataset and reports are stable.

⸻

# 9. Questions for Enoch

Before final implementation, answer these questions:

1. What exactly do you mean by “offloading work too soon”? Do you mean GPU Monte Carlo starts too early, CPU path budget ramps too fast, or work is being moved between Spoon/THEPC too early?
2. After a full PC restart, how long should the system wait before running full probability calculations? Example: 30 seconds, 2 minutes, 5 minutes.
3. What should count as READY? Should READY require price feed, order book, sigma, K, probability inputs, outcomes, and API all healthy?
4. Should the system ever auto-restart containers while you are asleep, or should it only write a bug report and wait?
5. What is the maximum number of automatic restarts allowed per hour?
6. Should the LLM be allowed to create patches automatically, or only generate a report/pull request for review?
7. What CPU usage is acceptable while the system is running? Example: target 15%, soft max 20%, hard max 35%.
8. Are we prioritizing low latency or stability right now?
9. Should the system keep showing last-good probabilities during recovery, or hide probabilities completely when stale?
10. Should the system continue collecting raw data even when the probability engine is blocked?
11. Which machine is supposed to be the CPU authority, and which is supposed to be the GPU/API authority?
12. Should the first ML model train only on BTC, or BTC and ETH together with asset as a feature?
13. How many days/weeks of clean shadow data should be collected before trusting ML outputs?
14. What should be the first ML target: final win/loss, p_no_touch/path crossing, or executable edge?
15. Should the system store every decision state, or only states near potential trades?
16. What is the acceptable max age for price, order book, sigma, and probability inputs before the system marks them stale?
17. Should K mutation always hard-block, or should there be a manual override for known rule changes?
18. Should the TUI display stale probabilities as gray/last-good, or remove them entirely?
19. Should bug reports be written locally only, or also sent to an external service/agent?
20. What is the smallest acceptable MVP for recovery: status display only, offload gating only, or full recovery manager?

⸻

# 10. Suggested Codex Instruction

Use this as the next high-level prompt:

You are working on the Polymarket BTC/ETH probability engine. Inspect the repository and implement a safe runtime recovery and offload readiness system.
The immediate goal is to prevent expensive probability work from starting too soon after restart, prevent stale/invalid inputs from producing confident probabilities, and make the TUI/API clearly show runtime phase, recovery state, and offload status.
Start by stabilizing the current known bugs:
- TUI freeze with CPU spike
- API_BLOCKED / response decode errors
- degraded runtime after restart
- offload starts too soon after restart
- sigma instability
- K threshold mutation
- service communication mismatch
- missing automatic bug-report pipeline
Then add a RuntimeRecoveryManager and OffloadReadinessGate with tests.
Do not change trading logic.
Do not enable live trading.
Preserve the read-only research workflow.
Do not begin full ML development until runtime stability and data logging are reliable.
Add structured bug reports for TUI freeze, API decode errors, K mutation, sigma instability, and offload-too-soon events.
Integrate recovery/offload state into the probability worker, runtime API, and TUI systems panel.
Add tests before behavior changes.
