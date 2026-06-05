# Section 5-6 Probability Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Section 5 decision-math outputs and Section 6 as-of Monte Carlo ensemble logic as a read-only probability layer, while keeping live collection, market display, and execution behavior unchanged by default.

**Architecture:** Keep hot Rust collection and current TUI reads fast. Build probability logic in Python under `src/polymarket_engine/probability/`, persist immutable read-only outputs in DuckDB, and expose only cached/status fields to the runtime API and TUI. Historical path generation and calibration stay in the research/offline lane. No signing, no order placement, no live trade authority.

**Tech Stack:** Python 3.11 project via `uv`, DuckDB schemas in `src/polymarket_engine/storage/schema.sql`, pytest, Ruff, existing Rust state-manager sidecar, Ratatui TUI in `crates/polymarket-engine-tui`.

---

## Sources Read

- `docs/BINARY_CONTRACT_ENGINE_PLAN.md`
  - Section 5: terminal probability, no-touch probability, `z_path`, `sigma_tau`, executable edge, and target-size book crossing.
  - Section 6: as-of Monte Carlo, empirical conditional prior, multiple path generators, ensemble probability, generator uncertainty, conditioning variables, and cached grids.
- `graphify-out/converted/BTC_Binary_Path_Probability_Incomplete_Research_Paper_d182482a.md`
  - The paper confirms MC is the counting framework, not a future-data shortcut.
  - The paper requires sparse buckets to widen uncertainty or block confidence.
  - `sigma_tau` scales sampled shocks and must be normalized to the remaining horizon.
- `docs/methodology-decisions-2026-05-29.md`
  - MC is the primary probability engine.
  - XGBoost is a later challenger/blocker/calibration layer, not live authority.
  - Live lane should use cached lookups and async logging.

## Current Sigma Verification

The current Section 4 sigma implementation is structurally sound enough to build on, with one important visibility gap.

- `src/polymarket_engine/features/volatility.py` uses Chainlink-only realized movement, normalized return intervals, lookback windows, finite-value checks, floors, and regime multipliers.
- `src/polymarket_engine/features/state_replay.py` restricts replay volatility source lookup to `polymarket_rtds_chainlink`.
- `src/polymarket_engine/probability/monte_carlo.py` applies per-step sigma as `sigma_tau / sqrt(steps)`, so the terminal horizon variance remains `sigma_tau^2`.
- Regression evidence from this planning pass:
  - `uv run pytest tests/features/test_volatility.py tests/storage/test_state_replay.py -q`
  - Result: `73 passed in 1.80s`
  - `uv run pytest tests/features/test_rust_decision_snapshots.py tests/ingestion/test_rust_normalizer_sidecar.py -q`
  - Result: `46 passed in 3.68s`

The gap: THEPC currently has runtime probabilities disabled, so live `sigma_tau` is not visible through `/api/runtime/probabilities`. Do not enable heavy MC on the hot loop just to inspect sigma. Add a low-contention diagnostic/status field in this plan.

## Non-Negotiable Boundaries

- [ ] Probability remains read-only.
- [ ] No live order placement, signing, key handling, or trade execution code.
- [ ] Replay and historical generation must enforce `event_ts <= asof_ts` and `observed_ts <= asof_ts`.
- [ ] Current-contract data after the as-of timestamp cannot be used to predict that same contract.
- [ ] Slow MC, calibration, path-library building, and grid refresh jobs must not run inside the TUI repaint loop.
- [ ] Runtime probabilities stay disabled by default unless explicitly enabled by env/config.
- [ ] If a bucket is sparse, output an explicit flag and increase uncertainty or block confidence.

## Data Contracts

### Existing Contracts To Keep

- `features.asof_state_inputs`: immutable as-of decision-state inputs.
- `features.probability_outputs`: persisted probability results.
- `ProbabilityInput`: request shape for scoring a contract at an as-of timestamp.
- `ProbabilityOutput`: result shape currently used by runtime cache.

### New Contracts

Add versioned Section 5/6 fields without breaking existing readers:

```python
# src/polymarket_engine/probability/schema.py
@dataclass(frozen=True)
class CostComponents:
    spread_cost: float
    fee_cost: float
    slippage_cost: float
    latency_cost: float
    total_cost: float


@dataclass(frozen=True)
class ExecutableEdge:
    executable_price: float
    edge_before_costs: float
    edge_after_costs: float
    required_edge: float
    cost_components: CostComponents
    decision_hint: str
    read_only: bool = True


@dataclass(frozen=True)
class GeneratorProbability:
    generator_id: str
    p_finish: float
    p_no_touch: float
    path_count: int
    weight: float
    sparse: bool
    diagnostics: Mapping[str, Any]


@dataclass(frozen=True)
class EnsembleProbability:
    p_finish: float
    p_no_touch: float
    u_gen: float
    generator_count: int
    generators: tuple[GeneratorProbability, ...]
```

Expected compatibility behavior:

- Existing code can continue reading `p_finish`, `p_no_touch`, `z_path`, `sigma_tau`, `model_version`, and `diagnostics`.
- New fields live in JSON columns first, then can be promoted to typed DuckDB columns after the shape stabilizes.

## Implementation Tasks

### Task 1: Add Section 5 Math And Exact Tests

Owner: core probability subagent.

Files:

- `src/polymarket_engine/probability/section5.py`
- `src/polymarket_engine/probability/schema.py`
- `tests/probability/test_section5.py`

Steps:

- [ ] Add pure functions for terminal probability inputs and edge accounting.
- [ ] Keep comparison operators exact: `>` and `<` are strict, not `>=` or `<=`.
- [ ] Return `p_no_touch = 0.0` when the path is already crossed or on the wrong side.
- [ ] Keep `z_path = distance_to_K / sigma_tau`, with explicit handling for missing, zero, or non-finite sigma.
- [ ] Add `ExecutableEdge` and `CostComponents` dataclasses.
- [ ] Add tests for all comparison operators and side mappings.

Concrete API:

```python
# src/polymarket_engine/probability/section5.py
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite


def distance_to_threshold(
    current_price: float,
    threshold: float,
    comparison_operator: str,
) -> float:
    if comparison_operator == ">":
        return threshold - current_price
    if comparison_operator == "<":
        return current_price - threshold
    raise ValueError(f"unsupported comparison_operator={comparison_operator!r}")


def z_path(distance: float, sigma_tau: float | None) -> float | None:
    if sigma_tau is None or sigma_tau <= 0.0 or not isfinite(sigma_tau):
        return None
    return distance / sigma_tau


def already_crossed(
    current_price: float,
    threshold: float,
    comparison_operator: str,
) -> bool:
    if comparison_operator == ">":
        return current_price > threshold
    if comparison_operator == "<":
        return current_price < threshold
    raise ValueError(f"unsupported comparison_operator={comparison_operator!r}")
```

Test command:

```bash
uv run pytest tests/probability/test_section5.py -q
uv run ruff check src/polymarket_engine/probability/section5.py tests/probability/test_section5.py
```

Commit after task:

```bash
git add src/polymarket_engine/probability/section5.py src/polymarket_engine/probability/schema.py tests/probability/test_section5.py
git commit -m "Add section 5 probability math"
```

### Task 2: Add Target-Size VWAP Book Crossing

Owner: execution/book subagent.

Files:

- `src/polymarket_engine/execution/__init__.py`
- `src/polymarket_engine/execution/book.py`
- `src/polymarket_engine/features/state_builder.py`
- `tests/execution/test_book.py`
- `tests/features/test_state_builder_execution.py`

Steps:

- [ ] Parse full `depth_json` bids and asks when available.
- [ ] Compute target-size VWAP for buy-side crossing using asks.
- [ ] Compute target-size VWAP for sell/exit accounting using bids.
- [ ] Emit depth flags: `full_depth_available`, `partial_fill`, `missing_depth`, `stale_quote`.
- [ ] Preserve current top-of-book behavior when full depth is unavailable, but mark it as incomplete.
- [ ] Store target-size executable price in `DecisionState.executable_price`.

Concrete API:

```python
# src/polymarket_engine/execution/book.py
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class VwapResult:
    vwap: float | None
    filled_size: float
    requested_size: float
    partial_fill: bool
    levels_used: int


def target_size_vwap(levels: tuple[BookLevel, ...], requested_size: float) -> VwapResult:
    if requested_size <= 0.0:
        raise ValueError("requested_size must be positive")
    remaining = requested_size
    notional = 0.0
    filled = 0.0
    used = 0
    for level in levels:
        if level.price <= 0.0 or level.size <= 0.0:
            continue
        take = min(remaining, level.size)
        notional += take * level.price
        filled += take
        remaining -= take
        used += 1
        if remaining <= 0.0:
            break
    if filled <= 0.0:
        return VwapResult(None, 0.0, requested_size, True, 0)
    return VwapResult(notional / filled, filled, requested_size, filled < requested_size, used)
```

Risk:

- Some raw orderbook rows only carry top-of-book, while state-manager rows carry full depth. If depth is missing, do not fabricate liquidity.

Test command:

```bash
uv run pytest tests/execution/test_book.py tests/features/test_state_builder_execution.py -q
uv run ruff check src/polymarket_engine/execution src/polymarket_engine/features/state_builder.py tests/execution/test_book.py tests/features/test_state_builder_execution.py
```

Commit after task:

```bash
git add src/polymarket_engine/execution src/polymarket_engine/features/state_builder.py tests/execution/test_book.py tests/features/test_state_builder_execution.py
git commit -m "Add target size orderbook vwap"
```

### Task 3: Extend Probability Persistence Without Breaking Readers

Owner: storage/API subagent.

Files:

- `src/polymarket_engine/storage/schema.sql`
- `src/polymarket_engine/storage/duckdb_store.py`
- `src/polymarket_engine/probability/runtime.py`
- `tests/storage/test_probability_outputs.py`
- `tests/probability/test_runtime_probability_persistence.py`

Steps:

- [ ] Add JSON columns for Section 5 and Section 6 details to `features.probability_outputs`.
- [ ] Keep old typed columns unchanged.
- [ ] Add `schema_version` to persisted diagnostics.
- [ ] Ensure older rows without the new JSON fields still deserialize.
- [ ] Keep runtime probability cache at no faster than 1 second.

Schema change:

```sql
ALTER TABLE features.probability_outputs ADD COLUMN IF NOT EXISTS section5_json JSON;
ALTER TABLE features.probability_outputs ADD COLUMN IF NOT EXISTS ensemble_json JSON;
ALTER TABLE features.probability_outputs ADD COLUMN IF NOT EXISTS generator_json JSON;
ALTER TABLE features.probability_outputs ADD COLUMN IF NOT EXISTS sparse_flags_json JSON;
```

Expected persisted diagnostic shape:

```json
{
  "schema_version": "polymarket-probability-v2",
  "section5": {
    "edge_after_costs": 0.021,
    "required_edge": 0.034,
    "decision_hint": "WAIT",
    "read_only": true
  },
  "ensemble": {
    "u_gen": 0.083,
    "generator_count": 4
  }
}
```

Test command:

```bash
uv run pytest tests/storage/test_probability_outputs.py tests/probability/test_runtime_probability_persistence.py -q
uv run ruff check src/polymarket_engine/storage src/polymarket_engine/probability/runtime.py tests/storage/test_probability_outputs.py tests/probability/test_runtime_probability_persistence.py
```

Commit after task:

```bash
git add src/polymarket_engine/storage src/polymarket_engine/probability/runtime.py tests/storage/test_probability_outputs.py tests/probability/test_runtime_probability_persistence.py
git commit -m "Persist section 5 and ensemble probability details"
```

### Task 4: Refactor Current MC Into A Generator Interface

Owner: Monte Carlo subagent.

Files:

- `src/polymarket_engine/probability/monte_carlo.py`
- `src/polymarket_engine/probability/generators.py`
- `src/polymarket_engine/probability/ensemble.py`
- `tests/probability/test_generators.py`
- `tests/probability/test_ensemble.py`

Steps:

- [ ] Keep existing seeded lognormal MC behavior exactly reproducible.
- [ ] Wrap it as `GeneratorId.LOGNORMAL_BASELINE`.
- [ ] Add `PathGenerator` protocol.
- [ ] Add `GeneratorProbability` and `EnsembleProbability` assembly.
- [ ] Compute `u_gen` as weighted standard deviation across generator `p_finish` values.
- [ ] Return explicit generator diagnostics.

Concrete API:

```python
# src/polymarket_engine/probability/generators.py
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from polymarket_engine.probability.schema import ProbabilityInput


class GeneratorId(StrEnum):
    LOGNORMAL_BASELINE = "lognormal_baseline"
    EMPIRICAL_CONDITIONAL = "empirical_conditional"
    BLOCK_BOOTSTRAP = "block_bootstrap"
    FILTERED_HISTORICAL = "filtered_historical"
    STRESS_OVERLAY = "stress_overlay"


@dataclass(frozen=True)
class GeneratorRunConfig:
    path_count: int
    seed: int
    step_seconds: float


class PathGenerator(Protocol):
    generator_id: GeneratorId

    def run(self, input: ProbabilityInput, config: GeneratorRunConfig):
        raise NotImplementedError
```

Ensemble rule:

```python
# src/polymarket_engine/probability/ensemble.py
def weighted_probability(values: tuple[float, ...], weights: tuple[float, ...]) -> float:
    total = sum(weights)
    if total <= 0.0:
        raise ValueError("weights must sum positive")
    return sum(value * weight for value, weight in zip(values, weights, strict=True)) / total
```

Test command:

```bash
uv run pytest tests/probability/test_generators.py tests/probability/test_ensemble.py tests/probability/test_monte_carlo.py -q
uv run ruff check src/polymarket_engine/probability tests/probability/test_generators.py tests/probability/test_ensemble.py
```

Commit after task:

```bash
git add src/polymarket_engine/probability tests/probability/test_generators.py tests/probability/test_ensemble.py
git commit -m "Add probability generator ensemble interface"
```

### Task 5: Build Historical Path Fragment Library Offline

Owner: research-data subagent.

Files:

- `src/polymarket_engine/research/path_fragments.py`
- `src/polymarket_engine/research/fragment_store.py`
- `src/polymarket_engine/cli.py`
- `src/polymarket_engine/storage/schema.sql`
- `tests/research/test_path_fragments.py`
- `tests/research/test_fragment_store.py`

Steps:

- [ ] Add a research table for immutable path fragments.
- [ ] Build fragments only from verified price ticks with both event and observed timestamps.
- [ ] Enforce as-of cutoffs during fragment selection.
- [ ] Bucket by `asset`, `horizon_seconds`, `seconds_left_bucket`, `z_path_bucket`, and `vol_regime`.
- [ ] Store sampled shock paths, not future labels for the active contract.
- [ ] Add a CLI command to build fragments from a maintenance snapshot.

Schema:

```sql
CREATE SCHEMA IF NOT EXISTS research;

CREATE TABLE IF NOT EXISTS research.path_fragments (
    fragment_id TEXT PRIMARY KEY,
    asset TEXT NOT NULL,
    source_key TEXT NOT NULL,
    fragment_start_event_ts TIMESTAMP NOT NULL,
    fragment_end_event_ts TIMESTAMP NOT NULL,
    max_observed_ts TIMESTAMP NOT NULL,
    horizon_seconds DOUBLE NOT NULL,
    step_seconds DOUBLE NOT NULL,
    seconds_left_bucket TEXT NOT NULL,
    z_path_bucket TEXT NOT NULL,
    vol_regime TEXT NOT NULL,
    shock_path_json JSON NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

CLI shape:

```bash
uv run polymarket-engine build-path-fragments \
  --db /home/ender/polymarket-data/db/polymarket.duckdb \
  --asset BTC \
  --source polymarket_rtds_chainlink \
  --horizon-seconds 300 \
  --step-seconds 1
```

Risk:

- The current clean dataset may be too small for robust empirical priors. Sparse buckets must be surfaced, not hidden.

Test command:

```bash
uv run pytest tests/research/test_path_fragments.py tests/research/test_fragment_store.py -q
uv run ruff check src/polymarket_engine/research src/polymarket_engine/cli.py tests/research
```

Commit after task:

```bash
git add src/polymarket_engine/research src/polymarket_engine/cli.py src/polymarket_engine/storage/schema.sql tests/research
git commit -m "Add offline historical path fragment library"
```

### Task 6: Implement Section 6 Generators

Owner: model-generator subagent.

Files:

- `src/polymarket_engine/probability/empirical.py`
- `src/polymarket_engine/probability/bootstrap.py`
- `src/polymarket_engine/probability/stress.py`
- `src/polymarket_engine/probability/generators.py`
- `tests/probability/test_empirical_generator.py`
- `tests/probability/test_bootstrap_generator.py`
- `tests/probability/test_stress_generator.py`

Steps:

- [ ] Implement `EMPIRICAL_CONDITIONAL` from path fragments matched by as-of-safe buckets.
- [ ] Implement `BLOCK_BOOTSTRAP` over recent Chainlink return blocks.
- [ ] Implement `FILTERED_HISTORICAL` using fragments filtered by volatility and distance buckets.
- [ ] Implement `STRESS_OVERLAY` that widens sigma or injects jump paths under explicit stress flags.
- [ ] Add deterministic tests using fixed path fixtures.
- [ ] Add sparse-bucket behavior: minimum fragment count, lower confidence, and `sparse_bucket=true`.

Sparse rule:

```python
MIN_FRAGMENT_COUNT = 50


def sparse_bucket(fragment_count: int) -> bool:
    return fragment_count < MIN_FRAGMENT_COUNT
```

Generator weighting v1:

```python
DEFAULT_GENERATOR_WEIGHTS = {
    "empirical_conditional": 0.40,
    "block_bootstrap": 0.25,
    "filtered_historical": 0.25,
    "stress_overlay": 0.10,
}
```

If empirical data is sparse:

- Keep the generator result.
- Mark `sparse=true`.
- Increase `u_gen` through ensemble dispersion.
- Set `decision_hint` no stronger than `WAIT`.

Test command:

```bash
uv run pytest tests/probability/test_empirical_generator.py tests/probability/test_bootstrap_generator.py tests/probability/test_stress_generator.py tests/probability/test_ensemble.py -q
uv run ruff check src/polymarket_engine/probability tests/probability/test_empirical_generator.py tests/probability/test_bootstrap_generator.py tests/probability/test_stress_generator.py
```

Commit after task:

```bash
git add src/polymarket_engine/probability tests/probability/test_empirical_generator.py tests/probability/test_bootstrap_generator.py tests/probability/test_stress_generator.py
git commit -m "Add section 6 probability generators"
```

### Task 7: Add Cached Grid Builder And Refresh Rules

Owner: runtime-cache subagent.

Files:

- `src/polymarket_engine/probability/grid_cache.py`
- `src/polymarket_engine/cli.py`
- `src/polymarket_engine/storage/schema.sql`
- `tests/probability/test_grid_cache.py`

Steps:

- [ ] Add a cache table keyed by asset, side, seconds-left bucket, z-path bucket, sigma bucket, vol regime, and generator version.
- [ ] Add cache reads for the live runtime path.
- [ ] Add a CLI command that builds or refreshes grids offline.
- [ ] Only refresh when inputs move enough: seconds-left bucket changes, z-path bucket changes, vol bucket changes, quote/entry changes materially, or cache expires.
- [ ] Keep runtime API returning cached probability rows, not running full grids per repaint.

Schema:

```sql
CREATE TABLE IF NOT EXISTS features.probability_grid_cache (
    cache_key TEXT PRIMARY KEY,
    asset TEXT NOT NULL,
    side TEXT NOT NULL,
    seconds_left_bucket TEXT NOT NULL,
    z_path_bucket TEXT NOT NULL,
    sigma_bucket TEXT NOT NULL,
    vol_regime TEXT NOT NULL,
    generator_version TEXT NOT NULL,
    p_finish DOUBLE NOT NULL,
    p_no_touch DOUBLE NOT NULL,
    u_gen DOUBLE NOT NULL,
    section5_json JSON NOT NULL,
    ensemble_json JSON NOT NULL,
    sparse_flags_json JSON NOT NULL,
    built_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    valid_until TIMESTAMP NOT NULL
);
```

CLI shape:

```bash
uv run polymarket-engine build-probability-grid \
  --db /home/ender/polymarket-data/db/polymarket.duckdb \
  --assets BTC,ETH \
  --horizons 300,600,900 \
  --path-count 10000 \
  --seed 20260604
```

Test command:

```bash
uv run pytest tests/probability/test_grid_cache.py -q
uv run ruff check src/polymarket_engine/probability/grid_cache.py src/polymarket_engine/cli.py tests/probability/test_grid_cache.py
```

Commit after task:

```bash
git add src/polymarket_engine/probability/grid_cache.py src/polymarket_engine/cli.py src/polymarket_engine/storage/schema.sql tests/probability/test_grid_cache.py
git commit -m "Add cached probability grid refresh rules"
```

### Task 8: Add Runtime API And TUI Read-Only Probability Surface

Owner: TUI/API subagent.

Files:

- `src/polymarket_engine/runtime_api.py`
- `src/polymarket_engine/probability/runtime.py`
- `crates/polymarket-engine-tui/src/api.rs`
- `crates/polymarket-engine-tui/src/app.rs`
- `crates/polymarket-engine-tui/src/render.rs`
- `crates/polymarket-engine-tui/tests/`
- `tests/runtime/test_probability_api.py`

Steps:

- [ ] Extend `/api/runtime/probabilities` response with Section 5 and Section 6 fields.
- [ ] Add a `/api/runtime/probability-status` lightweight endpoint if needed for sigma diagnostics and cache health.
- [ ] Keep the existing TUI Probability tab read-only.
- [ ] Add columns:
  - `Contract`
  - `p_finish`
  - `p_no_touch`
  - `u_gen`
  - `edge_after`
  - `required_edge`
  - `sigma_tau`
  - `age/flags`
- [ ] Display `DISABLED`, `SPARSE`, `STALE`, or `CACHE MISS` clearly.
- [ ] Do not trigger MC computation from the TUI.

API response shape:

```json
{
  "schema": "polymarket-probability-runtime-v2",
  "ok": true,
  "state": "OK",
  "rows": [
    {
      "contract": "BTC 5m UP",
      "p_finish": 0.54,
      "p_no_touch": 0.61,
      "u_gen": 0.08,
      "edge_after_costs": -0.02,
      "required_edge": 0.03,
      "sigma_tau": 36.4,
      "flags": ["read_only"]
    }
  ]
}
```

Test command:

```bash
uv run pytest tests/runtime/test_probability_api.py tests/probability/test_runtime_probability_persistence.py -q
cargo test -p polymarket-engine-tui
uv run ruff check src/polymarket_engine/runtime_api.py src/polymarket_engine/probability/runtime.py tests/runtime/test_probability_api.py
```

Commit after task:

```bash
git add src/polymarket_engine/runtime_api.py src/polymarket_engine/probability/runtime.py crates/polymarket-engine-tui tests/runtime/test_probability_api.py
git commit -m "Show read only ensemble probability in runtime and tui"
```

### Task 9: Add Calibration And Shadow Reports

Owner: research-report subagent.

Files:

- `src/polymarket_engine/research/calibration.py`
- `src/polymarket_engine/research/shadow_log.py`
- `src/polymarket_engine/cli.py`
- `tests/research/test_calibration.py`
- `docs/probability-section-5-6.md`

Steps:

- [ ] Log probability snapshots asynchronously with exact as-of inputs.
- [ ] Join labels only after official outcome resolution.
- [ ] Report Brier score, log loss, bucket calibration, `u_gen` dispersion, sparse bucket count, and edge sanity.
- [ ] Keep reports offline and read-only.
- [ ] Document how Section 5 and Section 6 map to the paper and engine plan.

CLI shape:

```bash
uv run polymarket-engine probability-calibration-report \
  --db /home/ender/polymarket-data/db/polymarket.duckdb \
  --since 2026-06-04T00:00:00-05:00 \
  --out reports/probability-calibration-2026-06-04.md
```

Test command:

```bash
uv run pytest tests/research/test_calibration.py -q
uv run ruff check src/polymarket_engine/research/calibration.py src/polymarket_engine/research/shadow_log.py src/polymarket_engine/cli.py tests/research/test_calibration.py
```

Commit after task:

```bash
git add src/polymarket_engine/research/calibration.py src/polymarket_engine/research/shadow_log.py src/polymarket_engine/cli.py tests/research/test_calibration.py docs/probability-section-5-6.md
git commit -m "Add probability calibration shadow reports"
```

### Task 10: Final Integration, Deploy Gate, And Live Checks

Owner: integration lead.

Steps:

- [ ] Run full Python probability/storage/runtime tests.
- [ ] Run TUI tests.
- [ ] Run Ruff on changed Python files.
- [ ] Verify runtime probabilities are still disabled by default on THEPC unless explicitly enabled.
- [ ] Deploy to THEPC only after tests pass and branch is clean.
- [ ] Check THEPC live endpoints:
  - `/api/runtime/live?limit=8`
  - `/api/runtime/probabilities?limit=8`
  - `/api/runtime/probability-status`
- [ ] Confirm TUI opens from the desktop icon and does not compute MC on repaint.
- [ ] Record whether live sigma diagnostics are visible.

Verification commands:

```bash
uv run pytest tests/features/test_volatility.py tests/storage/test_state_replay.py tests/probability tests/research tests/runtime -q
uv run pytest tests/features/test_rust_decision_snapshots.py tests/ingestion/test_rust_normalizer_sidecar.py -q
uv run ruff check src tests
cargo test -p polymarket-engine-tui
```

Remote checks:

```bash
ssh ender@100.72.104.49 "cd ~/polymarket && git status --short --branch"
curl -fsS "http://100.72.104.49:8000/api/runtime/live?limit=8" | python3 -m json.tool
curl -fsS "http://100.72.104.49:8000/api/runtime/probabilities?limit=8" | python3 -m json.tool
```

Commit after task:

```bash
git status --short
git commit --allow-empty -m "Verify section 5 and 6 probability integration"
```

## Subagent Deployment

Use subagents because the work splits cleanly and the risky files do not have to be edited by the same worker at once.

1. Core probability subagent:
   - Task 1
   - Task 4
   - Task 6
2. Execution/book subagent:
   - Task 2
   - State-builder integration tests
3. Storage/API subagent:
   - Task 3
   - Task 7
   - Task 8 API side
4. TUI subagent:
   - Task 8 Rust TUI side
5. Research/report subagent:
   - Task 5
   - Task 9
6. Integration lead:
   - Reviews after each commit.
   - Runs final verification.
   - Deploys to THEPC only after approval.

## Risk Register

- DuckDB live write lock: offline builders must run against snapshots or maintenance windows when needed.
- Sparse fresh dataset: empirical priors may be weak for several days after reset. The system must show `SPARSE` instead of pretending confidence.
- Book depth inconsistency: full depth is not guaranteed for every observation. Target-size VWAP must degrade to top-of-book with flags.
- Live latency: do not put MC in the TUI or hot API path. Use cached grids and status files.
- Sigma visibility: implementation tests pass, but live runtime needs a cheap diagnostic route before we can visually audit live `sigma_tau`.
- XGBoost temptation: defer. It is a challenger/calibrator later, not the Section 5-6 authority.

## Self-Review Checklist

- [ ] Every probability output can be traced to as-of inputs.
- [ ] No live write path can place or sign trades.
- [ ] Every new stochastic test uses a fixed seed.
- [ ] Sparse buckets produce explicit flags.
- [ ] `p_no_touch` is zero when already crossed or wrong-side.
- [ ] `>` and `<` semantics are exact.
- [ ] Runtime cache cannot recompute full MC faster than 1 second.
- [ ] TUI only renders API output.
- [ ] Docs explain which parts came from the engine plan and which came from the research paper.

