# Ensemble Execution Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only/paper ensemble decision layer that combines four Monte Carlo generator outputs with execution, crowding, support/resistance, validation gates, and explicit skip reasons while keeping supervised-live disabled behind a typed interface.

**Architecture:** Add pure Python contracts and reducers under `src/polymarket_engine/probability/` and `src/polymarket_engine/execution/`, persist compact decision summaries in DuckDB, and expose optional diagnostics through the existing runtime API/TUI fields. The live hot path reads cached summaries only; generators, validation, and paper labels run through offline or sidecar flows.

**Tech Stack:** Python 3.11 via `uv`, DuckDB, Polars where storage batching already uses it, pytest, Ruff, mypy, existing FastAPI runtime API, existing Rust TUI serde models.

---

## Scope Notes

- Keep `supervised_live` as a disabled output interface only.
- Do not add signing, credentials, private keys, funded-account config, or live order submission.
- Keep the current seeded lognormal MC as a control/fallback while the four main generators are introduced.
- Treat `p_finish` as fair value; treat `p_no_touch`, `z_path`, generator disagreement, support/resistance, and execution liquidity as gates or required-edge components.
- The implementation should follow `docs/superpowers/specs/2026-06-06-ensemble-execution-gates-design.md`.

## File Structure

- Create `src/polymarket_engine/probability/generator_contracts.py`
  - Generator ids, generator run records, dynamic-weight scope records, and strict JSON serialization.
- Create `src/polymarket_engine/probability/ensemble_outputs.py`
  - Weighted ensemble reducer, stress-overlay cap, dispersion, uncertainty buffer, and path diagnosis.
- Create `src/polymarket_engine/execution/book.py`
  - Target-size VWAP, depth, spread, quote age, and exit-liquidity scoring from order-book levels.
- Create `src/polymarket_engine/probability/decision_gates.py`
  - Pure decision evaluator producing hints, edge components, skip reasons, and disabled supervised-live summary.
- Create `src/polymarket_engine/research/generator_validation.py`
  - Chronological generator scoring and dynamic-weight candidate rows.
- Modify `src/polymarket_engine/storage/schema.sql`
  - Add `features.ensemble_decisions`, `features.generator_runs`, and `research.generator_weight_candidates`.
- Modify `src/polymarket_engine/storage/duckdb_store.py`
  - Add batched insert/upsert methods for ensemble decisions and generator runs.
- Modify `src/polymarket_engine/probability/runtime.py`
  - Merge latest persisted ensemble decision diagnostics into probability runtime rows.
- Modify `src/polymarket_engine/runtime_api.py`
  - Preserve backward compatibility and pass optional new fields through JSON.
- Modify `rust/crates/polymarket-cockpit-tui/src/status.rs`
  - Add optional fields to `RuntimeProbabilityRow`.
- Modify `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`
  - Show compact edge/hint/reason columns without expanding generator JSON.

## Task 1: Add Generator Contracts

**Files:**
- Create: `src/polymarket_engine/probability/generator_contracts.py`
- Test: `tests/probability/test_generator_contracts.py`

- [ ] **Step 1: Write failing tests for generator contracts**

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from polymarket_engine.probability.generator_contracts import (
    DynamicWeightScope,
    GeneratorId,
    GeneratorRun,
    generator_runs_to_json,
)


def test_generator_run_requires_probability_values() -> None:
    with pytest.raises(ValueError, match="p_finish"):
        GeneratorRun(
            generator_id=GeneratorId.EMPIRICAL_CONDITIONAL,
            p_finish=1.2,
            p_no_touch=0.4,
            path_count=1024,
            effective_path_count=900,
            seed=1,
            asof_ts=datetime(2026, 6, 6, tzinfo=UTC),
            runtime_ms=12.5,
            sparse=False,
            diagnostics={},
        )


def test_generator_scope_is_hashable() -> None:
    scope = DynamicWeightScope(
        asset="BTC",
        horizon_seconds=300,
        seconds_left_bucket="120-180",
        z_path_bucket="0.50-1.00",
        vol_regime="normal",
        vol_trend="flat",
        wick_regime="quiet",
        source_quality_state="ok",
    )

    assert {scope: "weights"}[scope] == "weights"


def test_generator_runs_to_json_is_stable_and_strict() -> None:
    run = GeneratorRun(
        generator_id=GeneratorId.BLOCK_BOOTSTRAP,
        p_finish=0.61,
        p_no_touch=0.58,
        path_count=2048,
        effective_path_count=1900,
        seed=17,
        asof_ts=datetime(2026, 6, 6, tzinfo=UTC),
        runtime_ms=8.25,
        sparse=False,
        diagnostics={"bucket": "btc-5m"},
    )

    payload = generator_runs_to_json((run,))

    assert payload[0]["generator_id"] == "block_bootstrap"
    assert payload[0]["asof_ts"] == "2026-06-06T00:00:00+00:00"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest tests/probability/test_generator_contracts.py -q
```

Expected: fails with `ModuleNotFoundError: No module named 'polymarket_engine.probability.generator_contracts'`.

- [ ] **Step 3: Add the generator contract module**

Write `src/polymarket_engine/probability/generator_contracts.py`:

```python
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Sequence, cast


class GeneratorId(StrEnum):
    EMPIRICAL_CONDITIONAL = "empirical_conditional"
    BLOCK_BOOTSTRAP = "block_bootstrap"
    FILTERED_HISTORICAL = "filtered_historical"
    STRESS_OVERLAY = "stress_overlay"
    LOGNORMAL_CONTROL = "lognormal_control"


@dataclass(frozen=True)
class DynamicWeightScope:
    asset: str
    horizon_seconds: int
    seconds_left_bucket: str
    z_path_bucket: str
    vol_regime: str
    vol_trend: str
    wick_regime: str
    source_quality_state: str

    def __post_init__(self) -> None:
        if self.asset not in {"BTC", "ETH"}:
            raise ValueError("asset must be BTC or ETH")
        if self.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        for field_name in (
            "seconds_left_bucket",
            "z_path_bucket",
            "vol_regime",
            "vol_trend",
            "wick_regime",
            "source_quality_state",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must be non-empty")


@dataclass(frozen=True)
class GeneratorRun:
    generator_id: GeneratorId
    p_finish: float
    p_no_touch: float
    path_count: int
    effective_path_count: int
    seed: int | None
    asof_ts: datetime
    runtime_ms: float
    sparse: bool
    diagnostics: dict[str, Any]

    def __post_init__(self) -> None:
        _require_probability(self.p_finish, "p_finish")
        _require_probability(self.p_no_touch, "p_no_touch")
        _require_positive_int(self.path_count, "path_count")
        _require_nonnegative_int(self.effective_path_count, "effective_path_count")
        if self.effective_path_count > self.path_count:
            raise ValueError("effective_path_count must not exceed path_count")
        if self.seed is not None and (isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise ValueError("seed must be int or None")
        _require_utc(self.asof_ts, "asof_ts")
        if not isinstance(self.runtime_ms, (int, float)) or not math.isfinite(self.runtime_ms) or self.runtime_ms < 0:
            raise ValueError("runtime_ms must be nonnegative and finite")
        _validate_json_object(self.diagnostics, "diagnostics")


def generator_runs_to_json(runs: Sequence[GeneratorRun]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run in runs:
        row = asdict(run)
        row["generator_id"] = run.generator_id.value
        row["asof_ts"] = run.asof_ts.isoformat()
        json.dumps(row, allow_nan=False, sort_keys=True)
        rows.append(cast(dict[str, Any], row))
    return rows


def _require_probability(value: float, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or value > 1:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")


def _require_positive_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _require_nonnegative_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a nonnegative integer")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must be normalized to UTC")


def _validate_json_object(value: dict[str, Any], field_name: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    json.dumps(value, allow_nan=False, sort_keys=True)
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/probability/test_generator_contracts.py -q
uv run ruff check src/polymarket_engine/probability/generator_contracts.py tests/probability/test_generator_contracts.py
```

Expected: all tests pass and Ruff reports no issues.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/polymarket_engine/probability/generator_contracts.py tests/probability/test_generator_contracts.py
git commit -m "Add Monte Carlo generator contracts"
```

## Task 2: Add Ensemble Reducer

**Files:**
- Create: `src/polymarket_engine/probability/ensemble_outputs.py`
- Test: `tests/probability/test_ensemble_outputs.py`

- [ ] **Step 1: Write failing tests for weighted ensemble outputs**

```python
from __future__ import annotations

from datetime import UTC, datetime

from polymarket_engine.probability.ensemble_outputs import (
    GeneratorWeight,
    PathDiagnosis,
    reduce_ensemble,
)
from polymarket_engine.probability.generator_contracts import GeneratorId, GeneratorRun


def _run(generator_id: GeneratorId, p_finish: float, p_no_touch: float, sparse: bool = False) -> GeneratorRun:
    return GeneratorRun(
        generator_id=generator_id,
        p_finish=p_finish,
        p_no_touch=p_no_touch,
        path_count=1000,
        effective_path_count=900,
        seed=7,
        asof_ts=datetime(2026, 6, 6, tzinfo=UTC),
        runtime_ms=3.0,
        sparse=sparse,
        diagnostics={},
    )


def test_reduce_ensemble_caps_stress_overlay_so_it_cannot_improve_probability() -> None:
    result = reduce_ensemble(
        runs=(
            _run(GeneratorId.EMPIRICAL_CONDITIONAL, 0.60, 0.55),
            _run(GeneratorId.BLOCK_BOOTSTRAP, 0.58, 0.52),
            _run(GeneratorId.FILTERED_HISTORICAL, 0.62, 0.57),
            _run(GeneratorId.STRESS_OVERLAY, 0.90, 0.88),
        ),
        weights=(
            GeneratorWeight(GeneratorId.EMPIRICAL_CONDITIONAL, 0.40),
            GeneratorWeight(GeneratorId.BLOCK_BOOTSTRAP, 0.25),
            GeneratorWeight(GeneratorId.FILTERED_HISTORICAL, 0.25),
            GeneratorWeight(GeneratorId.STRESS_OVERLAY, 0.10),
        ),
        base_model_buffer=0.01,
    )

    assert round(result.p_finish, 4) == 0.6000
    assert result.effective_generator_values["stress_overlay"]["p_finish"] == 0.60
    assert result.path_diagnosis == PathDiagnosis.CLEAN


def test_reduce_ensemble_marks_sparse_output() -> None:
    result = reduce_ensemble(
        runs=(
            _run(GeneratorId.EMPIRICAL_CONDITIONAL, 0.60, 0.55, sparse=True),
            _run(GeneratorId.BLOCK_BOOTSTRAP, 0.58, 0.52),
            _run(GeneratorId.FILTERED_HISTORICAL, 0.62, 0.57),
            _run(GeneratorId.STRESS_OVERLAY, 0.50, 0.44),
        ),
        weights=(
            GeneratorWeight(GeneratorId.EMPIRICAL_CONDITIONAL, 0.40),
            GeneratorWeight(GeneratorId.BLOCK_BOOTSTRAP, 0.25),
            GeneratorWeight(GeneratorId.FILTERED_HISTORICAL, 0.25),
            GeneratorWeight(GeneratorId.STRESS_OVERLAY, 0.10),
        ),
        base_model_buffer=0.01,
    )

    assert result.path_diagnosis == PathDiagnosis.SPARSE
    assert result.uncertainty_buffer > 0.01
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest tests/probability/test_ensemble_outputs.py -q
```

Expected: fails because `ensemble_outputs.py` does not exist.

- [ ] **Step 3: Add the ensemble reducer**

Write `src/polymarket_engine/probability/ensemble_outputs.py`:

```python
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from polymarket_engine.probability.generator_contracts import GeneratorId, GeneratorRun


class PathDiagnosis(StrEnum):
    CLEAN = "CLEAN"
    FRAGILE = "FRAGILE"
    SPARSE = "SPARSE"


@dataclass(frozen=True)
class GeneratorWeight:
    generator_id: GeneratorId
    weight: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.weight) or self.weight < 0:
            raise ValueError("weight must be nonnegative and finite")


@dataclass(frozen=True)
class EnsembleProbability:
    p_finish: float
    p_no_touch: float
    u_gen_finish: float
    u_gen_touch: float
    u_gen: float
    mc_dispersion: float
    uncertainty_buffer: float
    path_diagnosis: PathDiagnosis
    effective_generator_values: dict[str, dict[str, float]]


def reduce_ensemble(
    *,
    runs: tuple[GeneratorRun, ...],
    weights: tuple[GeneratorWeight, ...],
    base_model_buffer: float,
) -> EnsembleProbability:
    if not runs:
        raise ValueError("runs must be non-empty")
    if not math.isfinite(base_model_buffer) or base_model_buffer < 0:
        raise ValueError("base_model_buffer must be nonnegative and finite")
    runs_by_id = {run.generator_id: run for run in runs}
    weights_by_id = {weight.generator_id: weight.weight for weight in weights}
    if set(runs_by_id) != set(weights_by_id):
        raise ValueError("weights must match runs")
    total_weight = sum(weights_by_id.values())
    if total_weight <= 0:
        raise ValueError("weights must sum positive")

    effective_finish, effective_touch = _effective_values(runs_by_id)
    normalized = {generator_id: weight / total_weight for generator_id, weight in weights_by_id.items()}
    p_finish = sum(normalized[generator_id] * effective_finish[generator_id] for generator_id in normalized)
    p_no_touch = sum(normalized[generator_id] * effective_touch[generator_id] for generator_id in normalized)
    u_finish = _weighted_std(effective_finish, normalized, p_finish)
    u_touch = _weighted_std(effective_touch, normalized, p_no_touch)
    center_finish = p_finish
    center_touch = p_no_touch
    dispersion = max(
        max(abs(value - center_finish) for value in effective_finish.values()),
        max(abs(value - center_touch) for value in effective_touch.values()),
    )
    sparse_penalty = 0.03 if any(run.sparse for run in runs) else 0.0
    diagnosis = PathDiagnosis.SPARSE if sparse_penalty else PathDiagnosis.FRAGILE if dispersion >= 0.12 else PathDiagnosis.CLEAN
    uncertainty_buffer = base_model_buffer + 0.50 * max(u_finish, u_touch) + sparse_penalty
    values = {
        generator_id.value: {
            "p_finish": effective_finish[generator_id],
            "p_no_touch": effective_touch[generator_id],
            "weight": normalized[generator_id],
        }
        for generator_id in normalized
    }
    return EnsembleProbability(
        p_finish=p_finish,
        p_no_touch=p_no_touch,
        u_gen_finish=u_finish,
        u_gen_touch=u_touch,
        u_gen=max(u_finish, u_touch),
        mc_dispersion=dispersion,
        uncertainty_buffer=uncertainty_buffer,
        path_diagnosis=diagnosis,
        effective_generator_values=values,
    )


def _effective_values(
    runs_by_id: dict[GeneratorId, GeneratorRun],
) -> tuple[dict[GeneratorId, float], dict[GeneratorId, float]]:
    finish = {generator_id: run.p_finish for generator_id, run in runs_by_id.items()}
    touch = {generator_id: run.p_no_touch for generator_id, run in runs_by_id.items()}
    stress = runs_by_id.get(GeneratorId.STRESS_OVERLAY)
    if stress is not None:
        non_stress_finish = [
            run.p_finish for generator_id, run in runs_by_id.items() if generator_id != GeneratorId.STRESS_OVERLAY
        ]
        non_stress_touch = [
            run.p_no_touch for generator_id, run in runs_by_id.items() if generator_id != GeneratorId.STRESS_OVERLAY
        ]
        finish[GeneratorId.STRESS_OVERLAY] = min(stress.p_finish, _median(non_stress_finish))
        touch[GeneratorId.STRESS_OVERLAY] = min(stress.p_no_touch, _median(non_stress_touch))
    return finish, touch


def _weighted_std(
    values: dict[GeneratorId, float],
    weights: dict[GeneratorId, float],
    center: float,
) -> float:
    return math.sqrt(sum(weights[generator_id] * (value - center) ** 2 for generator_id, value in values.items()))


def _median(values: list[float]) -> float:
    if not values:
        raise ValueError("median requires at least one value")
    sorted_values = sorted(values)
    mid = len(sorted_values) // 2
    if len(sorted_values) % 2:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/probability/test_ensemble_outputs.py tests/probability/test_generator_contracts.py -q
uv run ruff check src/polymarket_engine/probability/ensemble_outputs.py tests/probability/test_ensemble_outputs.py
```

Expected: all tests pass and Ruff reports no issues.

- [ ] **Step 5: Commit Task 2**

```bash
git add src/polymarket_engine/probability/ensemble_outputs.py tests/probability/test_ensemble_outputs.py
git commit -m "Add ensemble probability reducer"
```

## Task 3: Add Execution Book Metrics

**Files:**
- Create: `src/polymarket_engine/execution/__init__.py`
- Create: `src/polymarket_engine/execution/book.py`
- Test: `tests/execution/test_book.py`

- [ ] **Step 1: Write failing tests for entry and exit VWAP**

```python
from __future__ import annotations

from polymarket_engine.execution.book import BookLevel, evaluate_execution_book


def test_execution_book_scores_entry_and_exit_liquidity() -> None:
    result = evaluate_execution_book(
        side="UP",
        target_size=20.0,
        best_bid=0.60,
        best_ask=0.62,
        bids=(BookLevel(price=0.60, size=10.0), BookLevel(price=0.59, size=20.0)),
        asks=(BookLevel(price=0.62, size=12.0), BookLevel(price=0.63, size=20.0)),
        quote_age_ms=120,
        max_quote_age_ms=500,
    )

    assert round(result.entry_vwap, 4) == 0.624
    assert round(result.exit_vwap, 4) == 0.595
    assert result.entry_depth_available is True
    assert result.exit_depth_available is True
    assert result.skip_reasons == ()


def test_execution_book_blocks_when_exit_depth_is_missing() -> None:
    result = evaluate_execution_book(
        side="DOWN",
        target_size=20.0,
        best_bid=0.48,
        best_ask=0.50,
        bids=(BookLevel(price=0.48, size=5.0),),
        asks=(BookLevel(price=0.50, size=50.0),),
        quote_age_ms=100,
        max_quote_age_ms=500,
    )

    assert result.exit_depth_available is False
    assert "insufficient_exit_depth" in result.skip_reasons
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest tests/execution/test_book.py -q
```

Expected: import failure because `polymarket_engine.execution.book` does not exist.

- [ ] **Step 3: Add execution book evaluation**

Create `src/polymarket_engine/execution/__init__.py` as an empty package marker.

Write `src/polymarket_engine/execution/book.py`:

```python
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.price) or self.price < 0 or self.price > 1:
            raise ValueError("price must be finite and between 0 and 1")
        if not math.isfinite(self.size) or self.size < 0:
            raise ValueError("size must be nonnegative and finite")


@dataclass(frozen=True)
class ExecutionBookMetrics:
    entry_vwap: float
    exit_vwap: float
    entry_slippage: float
    exit_slippage: float
    spread: float
    entry_depth_available: bool
    exit_depth_available: bool
    quote_age_ms: int
    skip_reasons: tuple[str, ...]


def evaluate_execution_book(
    *,
    side: str,
    target_size: float,
    best_bid: float,
    best_ask: float,
    bids: tuple[BookLevel, ...],
    asks: tuple[BookLevel, ...],
    quote_age_ms: int,
    max_quote_age_ms: int,
) -> ExecutionBookMetrics:
    if side not in {"UP", "DOWN"}:
        raise ValueError("side must be UP or DOWN")
    if not math.isfinite(target_size) or target_size <= 0:
        raise ValueError("target_size must be positive and finite")
    if quote_age_ms < 0 or max_quote_age_ms < 0:
        raise ValueError("quote ages must be nonnegative")
    entry_vwap, entry_complete = _target_vwap(asks, target_size)
    exit_vwap, exit_complete = _target_vwap(bids, target_size)
    spread = max(0.0, best_ask - best_bid)
    reasons: list[str] = []
    if quote_age_ms > max_quote_age_ms:
        reasons.append("stale_orderbook")
    if not entry_complete:
        reasons.append("insufficient_entry_depth")
    if not exit_complete:
        reasons.append("insufficient_exit_depth")
    return ExecutionBookMetrics(
        entry_vwap=entry_vwap,
        exit_vwap=exit_vwap,
        entry_slippage=max(0.0, entry_vwap - best_ask),
        exit_slippage=max(0.0, best_bid - exit_vwap),
        spread=spread,
        entry_depth_available=entry_complete,
        exit_depth_available=exit_complete,
        quote_age_ms=quote_age_ms,
        skip_reasons=tuple(reasons),
    )


def _target_vwap(levels: tuple[BookLevel, ...], target_size: float) -> tuple[float, bool]:
    remaining = target_size
    notional = 0.0
    filled = 0.0
    for level in levels:
        take = min(level.size, remaining)
        if take <= 0:
            continue
        notional += take * level.price
        filled += take
        remaining -= take
        if remaining <= 1e-12:
            break
    if filled <= 0:
        return 0.0, False
    return notional / filled, remaining <= 1e-12
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/execution/test_book.py -q
uv run ruff check src/polymarket_engine/execution tests/execution/test_book.py
```

Expected: all tests pass and Ruff reports no issues.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/polymarket_engine/execution tests/execution/test_book.py
git commit -m "Add execution book metrics"
```

## Task 4: Add Decision Gate Evaluator

**Files:**
- Create: `src/polymarket_engine/probability/decision_gates.py`
- Test: `tests/probability/test_decision_gates.py`

- [ ] **Step 1: Write failing tests for decision hints and disabled supervised-live**

```python
from __future__ import annotations

from polymarket_engine.execution.book import ExecutionBookMetrics
from polymarket_engine.probability.decision_gates import (
    DecisionInputs,
    DecisionMode,
    evaluate_decision,
)
from polymarket_engine.probability.ensemble_outputs import EnsembleProbability, PathDiagnosis


def _ensemble() -> EnsembleProbability:
    return EnsembleProbability(
        p_finish=0.72,
        p_no_touch=0.68,
        u_gen_finish=0.02,
        u_gen_touch=0.03,
        u_gen=0.03,
        mc_dispersion=0.04,
        uncertainty_buffer=0.025,
        path_diagnosis=PathDiagnosis.CLEAN,
        effective_generator_values={},
    )


def _execution(exit_depth: bool = True) -> ExecutionBookMetrics:
    return ExecutionBookMetrics(
        entry_vwap=0.62,
        exit_vwap=0.59,
        entry_slippage=0.004,
        exit_slippage=0.005,
        spread=0.02,
        entry_depth_available=True,
        exit_depth_available=exit_depth,
        quote_age_ms=100,
        skip_reasons=() if exit_depth else ("insufficient_exit_depth",),
    )


def test_decision_promotes_to_paper_trade_when_edge_and_path_are_clean() -> None:
    decision = evaluate_decision(
        DecisionInputs(
            execution_mode=DecisionMode.PAPER,
            ensemble=_ensemble(),
            execution=_execution(),
            z_path=1.2,
            min_z_path=0.4,
            min_p_no_touch=0.55,
            base_edge=0.02,
            latency_buffer=0.005,
            source_buffer=0.005,
            crowding_buffer=0.0,
            support_resistance_buffer=0.0,
            support_resistance_reasons=(),
            crowding_reasons=(),
            quality_reasons=(),
        )
    )

    assert decision.decision_hint == "PAPER_TRADE"
    assert decision.supervised_live_action == "DISABLED"
    assert decision.live_order_intent is None
    assert decision.skip_reasons == ()


def test_decision_blocks_when_exit_liquidity_is_missing() -> None:
    decision = evaluate_decision(
        DecisionInputs(
            execution_mode=DecisionMode.PAPER,
            ensemble=_ensemble(),
            execution=_execution(exit_depth=False),
            z_path=1.2,
            min_z_path=0.4,
            min_p_no_touch=0.55,
            base_edge=0.02,
            latency_buffer=0.005,
            source_buffer=0.005,
            crowding_buffer=0.0,
            support_resistance_buffer=0.0,
            support_resistance_reasons=(),
            crowding_reasons=(),
            quality_reasons=(),
        )
    )

    assert decision.decision_hint == "BLOCK"
    assert "insufficient_exit_depth" in decision.skip_reasons


def test_supervised_live_requires_manual_approval_and_no_order_intent() -> None:
    decision = evaluate_decision(
        DecisionInputs(
            execution_mode=DecisionMode.SUPERVISED_LIVE,
            ensemble=_ensemble(),
            execution=_execution(),
            z_path=1.2,
            min_z_path=0.4,
            min_p_no_touch=0.55,
            base_edge=0.02,
            latency_buffer=0.005,
            source_buffer=0.005,
            crowding_buffer=0.0,
            support_resistance_buffer=0.0,
            support_resistance_reasons=(),
            crowding_reasons=(),
            quality_reasons=(),
        )
    )

    assert decision.decision_hint == "REQUIRE_MANUAL_APPROVAL"
    assert decision.supervised_live_action == "REQUIRE_MANUAL_APPROVAL"
    assert decision.live_order_intent is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest tests/probability/test_decision_gates.py -q
```

Expected: fails because `decision_gates.py` does not exist.

- [ ] **Step 3: Add decision gate evaluator**

Write `src/polymarket_engine/probability/decision_gates.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from polymarket_engine.execution.book import ExecutionBookMetrics
from polymarket_engine.probability.ensemble_outputs import EnsembleProbability, PathDiagnosis


class DecisionMode(StrEnum):
    READ_ONLY = "read_only"
    PAPER = "paper"
    SUPERVISED_LIVE = "supervised_live"


@dataclass(frozen=True)
class DecisionInputs:
    execution_mode: DecisionMode
    ensemble: EnsembleProbability
    execution: ExecutionBookMetrics
    z_path: float
    min_z_path: float
    min_p_no_touch: float
    base_edge: float
    latency_buffer: float
    source_buffer: float
    crowding_buffer: float
    support_resistance_buffer: float
    support_resistance_reasons: tuple[str, ...]
    crowding_reasons: tuple[str, ...]
    quality_reasons: tuple[str, ...]


@dataclass(frozen=True)
class DecisionOutput:
    decision_hint: str
    edge_after_costs: float
    required_edge: float
    skip_reasons: tuple[str, ...]
    edge_components: dict[str, float]
    supervised_live_action: str
    live_order_intent: None


def evaluate_decision(inputs: DecisionInputs) -> DecisionOutput:
    edge_after_costs = inputs.ensemble.p_finish - inputs.execution.entry_vwap
    edge_components = {
        "base_edge": inputs.base_edge,
        "entry_slippage_buffer": inputs.execution.entry_slippage,
        "exit_slippage_buffer": inputs.execution.exit_slippage,
        "latency_buffer": inputs.latency_buffer,
        "source_buffer": inputs.source_buffer,
        "uncertainty_buffer": inputs.ensemble.uncertainty_buffer,
        "crowding_buffer": inputs.crowding_buffer,
        "support_resistance_buffer": inputs.support_resistance_buffer,
    }
    required_edge = sum(edge_components.values())
    reasons: list[str] = []
    reasons.extend(inputs.quality_reasons)
    reasons.extend(inputs.execution.skip_reasons)
    reasons.extend(inputs.crowding_reasons)
    reasons.extend(inputs.support_resistance_reasons)
    if inputs.z_path < inputs.min_z_path:
        reasons.append("not_enough_distance")
    if inputs.ensemble.p_no_touch < inputs.min_p_no_touch:
        reasons.append("weak_path_survival")
    if inputs.ensemble.path_diagnosis == PathDiagnosis.SPARSE:
        reasons.append("sparse_generator_scope")
    if inputs.ensemble.u_gen >= 0.12:
        reasons.append("generator_disagreement")

    hard_block = any(
        reason
        in {
            "stale_orderbook",
            "insufficient_entry_depth",
            "insufficient_exit_depth",
            "not_enough_distance",
            "weak_path_survival",
            "near_resistance",
            "near_support",
            "threshold_on_structure",
            "crowded_order_flow",
        }
        for reason in reasons
    )
    if hard_block:
        hint = "BLOCK"
    elif inputs.ensemble.path_diagnosis == PathDiagnosis.SPARSE:
        hint = "WAIT"
    elif edge_after_costs < required_edge:
        reasons.append("insufficient_edge")
        hint = "DEMAND_MORE_EDGE"
    elif inputs.execution_mode == DecisionMode.READ_ONLY:
        hint = "TRADE_CANDIDATE"
    elif inputs.execution_mode == DecisionMode.PAPER:
        hint = "PAPER_TRADE"
    else:
        reasons.append("manual_approval_required")
        hint = "REQUIRE_MANUAL_APPROVAL"

    supervised_live_action = (
        "REQUIRE_MANUAL_APPROVAL"
        if hint == "REQUIRE_MANUAL_APPROVAL"
        else "DISABLED"
    )
    return DecisionOutput(
        decision_hint=hint,
        edge_after_costs=edge_after_costs,
        required_edge=required_edge,
        skip_reasons=tuple(dict.fromkeys(reasons)),
        edge_components=edge_components,
        supervised_live_action=supervised_live_action,
        live_order_intent=None,
    )
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/probability/test_decision_gates.py tests/probability/test_ensemble_outputs.py tests/execution/test_book.py -q
uv run ruff check src/polymarket_engine/probability/decision_gates.py tests/probability/test_decision_gates.py
```

Expected: all tests pass and Ruff reports no issues.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/polymarket_engine/probability/decision_gates.py tests/probability/test_decision_gates.py
git commit -m "Add ensemble decision gates"
```

## Task 5: Add DuckDB Persistence

**Files:**
- Modify: `src/polymarket_engine/storage/schema.sql`
- Modify: `src/polymarket_engine/storage/duckdb_store.py`
- Test: `tests/storage/test_schema.py`
- Test: `tests/storage/test_normalized_writes.py`

- [ ] **Step 1: Add schema tests for ensemble tables**

Append to `tests/storage/test_schema.py`:

```python
def test_schema_creates_ensemble_decision_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    schema_path = Path("src/polymarket_engine/storage/schema.sql")

    with duckdb.connect(str(db_path)) as conn:
        conn.sql(schema_path.read_text())
        tables = {
            row[0]
            for row in conn.execute(
                """
                select table_schema || '.' || table_name
                from information_schema.tables
                where table_schema in ('features', 'research')
                """
            ).fetchall()
        }

    assert "features.generator_runs" in tables
    assert "features.ensemble_decisions" in tables
    assert "research.generator_weight_candidates" in tables
```

- [ ] **Step 2: Run the schema test to verify it fails**

Run:

```bash
uv run pytest tests/storage/test_schema.py::test_schema_creates_ensemble_decision_tables -q
```

Expected: fails because the new tables do not exist.

- [ ] **Step 3: Add schema tables**

Append to `src/polymarket_engine/storage/schema.sql`:

```sql
CREATE SCHEMA IF NOT EXISTS research;

CREATE TABLE IF NOT EXISTS features.generator_runs (
    generator_run_id VARCHAR PRIMARY KEY,
    state_id VARCHAR NOT NULL,
    asof_ts TIMESTAMPTZ NOT NULL,
    generator_id VARCHAR NOT NULL,
    p_finish DOUBLE NOT NULL,
    p_no_touch DOUBLE NOT NULL,
    path_count BIGINT NOT NULL,
    effective_path_count BIGINT NOT NULL,
    seed BIGINT,
    runtime_ms DOUBLE NOT NULL,
    sparse BOOLEAN NOT NULL,
    diagnostics_json VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS features.ensemble_decisions (
    decision_id VARCHAR PRIMARY KEY,
    state_id VARCHAR NOT NULL,
    contract_id VARCHAR NOT NULL,
    asof_ts TIMESTAMPTZ NOT NULL,
    execution_mode VARCHAR NOT NULL,
    decision_hint VARCHAR NOT NULL,
    p_finish DOUBLE NOT NULL,
    p_no_touch DOUBLE NOT NULL,
    z_path DOUBLE NOT NULL,
    edge_after_costs DOUBLE NOT NULL,
    required_edge DOUBLE NOT NULL,
    skip_reasons_json VARCHAR NOT NULL,
    edge_components_json VARCHAR NOT NULL,
    generator_summary_json VARCHAR NOT NULL,
    execution_summary_json VARCHAR NOT NULL,
    supervised_live_json VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS research.generator_weight_candidates (
    weight_candidate_id VARCHAR PRIMARY KEY,
    scope_json VARCHAR NOT NULL,
    trained_through_ts TIMESTAMPTZ NOT NULL,
    generator_weights_json VARCHAR NOT NULL,
    label_count BIGINT NOT NULL,
    scoring_rule VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
```

- [ ] **Step 4: Add store methods and write tests**

Add this test to `tests/storage/test_normalized_writes.py`:

```python
def test_insert_ensemble_decision_rows_batches_json_payloads(tmp_path: Path) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    store = DuckDbIngestStore(db_path)
    now = datetime.now(timezone.utc)

    store.insert_ensemble_decisions(
        (
            {
                "decision_id": "decision-1",
                "state_id": "state-1",
                "contract_id": "contract-1",
                "asof_ts": now,
                "execution_mode": "paper",
                "decision_hint": "PAPER_TRADE",
                "p_finish": 0.72,
                "p_no_touch": 0.68,
                "z_path": 1.2,
                "edge_after_costs": 0.10,
                "required_edge": 0.06,
                "skip_reasons_json": "[]",
                "edge_components_json": '{"base_edge":0.02}',
                "generator_summary_json": "{}",
                "execution_summary_json": "{}",
                "supervised_live_json": '{"action":"DISABLED"}',
                "created_at": now,
            },
        )
    )

    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute(
            "select decision_hint, supervised_live_json from features.ensemble_decisions"
        ).fetchone()

    assert row == ("PAPER_TRADE", '{"action":"DISABLED"}')
```

Add this method to `DuckDbIngestStore`:

```python
    def insert_ensemble_decisions(self, rows: Sequence[dict[str, Any]]) -> None:
        if not rows:
            return
        frame = pl.DataFrame(list(rows))
        with self._connection() as conn:
            conn.register("ensemble_decision_rows", frame)
            conn.execute(
                """
                insert or replace into features.ensemble_decisions
                (decision_id, state_id, contract_id, asof_ts, execution_mode,
                 decision_hint, p_finish, p_no_touch, z_path, edge_after_costs,
                 required_edge, skip_reasons_json, edge_components_json,
                 generator_summary_json, execution_summary_json,
                 supervised_live_json, created_at)
                select decision_id, state_id, contract_id, asof_ts::TIMESTAMPTZ,
                       execution_mode, decision_hint, p_finish, p_no_touch,
                       z_path, edge_after_costs, required_edge, skip_reasons_json,
                       edge_components_json, generator_summary_json,
                       execution_summary_json, supervised_live_json,
                       created_at::TIMESTAMPTZ
                from ensemble_decision_rows
                """,
            )
```

- [ ] **Step 5: Run focused storage tests**

Run:

```bash
uv run pytest tests/storage/test_schema.py::test_schema_creates_ensemble_decision_tables tests/storage/test_normalized_writes.py::test_insert_ensemble_decision_rows_batches_json_payloads -q
uv run ruff check src/polymarket_engine/storage/duckdb_store.py tests/storage/test_schema.py tests/storage/test_normalized_writes.py
```

Expected: all tests pass and Ruff reports no issues.

- [ ] **Step 6: Commit Task 5**

```bash
git add src/polymarket_engine/storage/schema.sql src/polymarket_engine/storage/duckdb_store.py tests/storage/test_schema.py tests/storage/test_normalized_writes.py
git commit -m "Persist ensemble decision summaries"
```

## Task 6: Add Chronological Generator Validation

**Files:**
- Create: `src/polymarket_engine/research/generator_validation.py`
- Test: `tests/research/test_generator_validation.py`

- [ ] **Step 1: Write failing tests for as-of-safe weights**

```python
from __future__ import annotations

from datetime import UTC, datetime

from polymarket_engine.probability.generator_contracts import GeneratorId
from polymarket_engine.research.generator_validation import (
    GeneratorLabel,
    GeneratorPrediction,
    build_weight_candidate,
)


def test_weight_candidate_uses_only_labels_before_decision_time() -> None:
    candidate = build_weight_candidate(
        predictions=(
            GeneratorPrediction("state-1", datetime(2026, 6, 1, tzinfo=UTC), GeneratorId.EMPIRICAL_CONDITIONAL, 0.60, 0.55),
            GeneratorPrediction("state-2", datetime(2026, 6, 3, tzinfo=UTC), GeneratorId.EMPIRICAL_CONDITIONAL, 0.40, 0.45),
        ),
        labels=(
            GeneratorLabel("state-1", datetime(2026, 6, 2, tzinfo=UTC), True, True),
            GeneratorLabel("state-2", datetime(2026, 6, 7, tzinfo=UTC), False, False),
        ),
        decision_asof_ts=datetime(2026, 6, 6, tzinfo=UTC),
        min_labels=1,
        eta=3.0,
    )

    assert candidate.label_count == 1
    assert candidate.trained_through_ts == datetime(2026, 6, 2, tzinfo=UTC)
    assert candidate.sparse is False


def test_weight_candidate_marks_sparse_when_labels_are_insufficient() -> None:
    candidate = build_weight_candidate(
        predictions=(),
        labels=(),
        decision_asof_ts=datetime(2026, 6, 6, tzinfo=UTC),
        min_labels=100,
        eta=3.0,
    )

    assert candidate.label_count == 0
    assert candidate.sparse is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest tests/research/test_generator_validation.py -q
```

Expected: fails because `generator_validation.py` does not exist.

- [ ] **Step 3: Add generator validation module**

Write `src/polymarket_engine/research/generator_validation.py`:

```python
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from polymarket_engine.probability.generator_contracts import GeneratorId


@dataclass(frozen=True)
class GeneratorPrediction:
    state_id: str
    asof_ts: datetime
    generator_id: GeneratorId
    p_finish: float
    p_no_touch: float


@dataclass(frozen=True)
class GeneratorLabel:
    state_id: str
    label_ts: datetime
    did_finish_win: bool
    did_no_touch: bool


@dataclass(frozen=True)
class WeightCandidate:
    weights: dict[GeneratorId, float]
    label_count: int
    trained_through_ts: datetime | None
    sparse: bool


def build_weight_candidate(
    *,
    predictions: tuple[GeneratorPrediction, ...],
    labels: tuple[GeneratorLabel, ...],
    decision_asof_ts: datetime,
    min_labels: int,
    eta: float,
) -> WeightCandidate:
    labels_by_state = {
        label.state_id: label
        for label in labels
        if label.label_ts < decision_asof_ts
    }
    losses: dict[GeneratorId, list[float]] = {}
    trained_through: datetime | None = None
    for prediction in predictions:
        label = labels_by_state.get(prediction.state_id)
        if label is None or prediction.asof_ts >= decision_asof_ts:
            continue
        loss = 0.70 * _log_loss(prediction.p_finish, label.did_finish_win) + 0.30 * _log_loss(
            prediction.p_no_touch,
            label.did_no_touch,
        )
        losses.setdefault(prediction.generator_id, []).append(loss)
        trained_through = label.label_ts if trained_through is None else max(trained_through, label.label_ts)
    label_count = sum(len(values) for values in losses.values())
    if label_count < min_labels or not losses:
        return WeightCandidate(weights=_seed_weights(), label_count=label_count, trained_through_ts=trained_through, sparse=True)
    raw = {
        generator_id: math.exp(-eta * (sum(values) / len(values)))
        for generator_id, values in losses.items()
    }
    for generator_id, seed_weight in _seed_weights().items():
        raw.setdefault(generator_id, seed_weight)
    total = sum(raw.values())
    return WeightCandidate(
        weights={generator_id: weight / total for generator_id, weight in raw.items()},
        label_count=label_count,
        trained_through_ts=trained_through,
        sparse=False,
    )


def _log_loss(probability: float, label: bool) -> float:
    clipped = min(1.0 - 1e-9, max(1e-9, probability))
    return -math.log(clipped if label else 1.0 - clipped)


def _seed_weights() -> dict[GeneratorId, float]:
    return {
        GeneratorId.EMPIRICAL_CONDITIONAL: 0.40,
        GeneratorId.BLOCK_BOOTSTRAP: 0.25,
        GeneratorId.FILTERED_HISTORICAL: 0.25,
        GeneratorId.STRESS_OVERLAY: 0.10,
    }
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/research/test_generator_validation.py -q
uv run ruff check src/polymarket_engine/research/generator_validation.py tests/research/test_generator_validation.py
```

Expected: all tests pass and Ruff reports no issues.

- [ ] **Step 5: Commit Task 6**

```bash
git add src/polymarket_engine/research/generator_validation.py tests/research/test_generator_validation.py
git commit -m "Add chronological generator validation"
```

## Task 7: Expose Ensemble Diagnostics Through Runtime API

**Files:**
- Modify: `src/polymarket_engine/probability/runtime.py`
- Modify: `src/polymarket_engine/runtime_api.py`
- Test: `tests/probability/test_runtime.py`
- Test: `tests/test_runtime_api.py`

- [ ] **Step 1: Add runtime API test for optional decision fields**

Append to `tests/test_runtime_api.py`:

```python
def test_runtime_probabilities_include_optional_ensemble_decision_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    store = DuckDbIngestStore(db_path)
    now = datetime.now(UTC)
    probability_input = ProbabilityInput(
        state_id="state-1",
        asof_ts=now,
        asset="BTC",
        side="UP",
        comparison_operator=">",
        seconds_left=120,
        settlement_price=100_100,
        threshold=100_000,
        sigma_tau=0.001,
        executable_price=0.62,
        source_age_ms=50,
        book_age_ms=50,
        z_path=1.0,
    )
    output = ProbabilityOutput(
        state_id="state-1",
        asof_ts=now,
        p_finish=0.72,
        p_no_touch=0.68,
        z_path=1.0,
        model_version="ensemble-v1",
        seed=7,
        diagnostics={},
    )
    store.insert_probability_output(
        output_id="out-1",
        probability_input=probability_input,
        output=output,
    )
    store.insert_ensemble_decisions(
        (
            {
                "decision_id": "decision-1",
                "state_id": "state-1",
                "contract_id": "contract-1",
                "asof_ts": now,
                "execution_mode": "paper",
                "decision_hint": "PAPER_TRADE",
                "p_finish": 0.72,
                "p_no_touch": 0.68,
                "z_path": 1.0,
                "edge_after_costs": 0.10,
                "required_edge": 0.06,
                "skip_reasons_json": "[]",
                "edge_components_json": '{"base_edge":0.02}',
                "generator_summary_json": '{"u_gen":0.03}',
                "execution_summary_json": '{"entry_vwap":0.62}',
                "supervised_live_json": '{"action":"DISABLED"}',
                "created_at": now,
            },
        )
    )
    app = create_app(duckdb_path=db_path, enable_runtime_probabilities=True)

    response = TestClient(app).get("/api/runtime/probabilities?limit=1")

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["decision_hint"] == "PAPER_TRADE"
    assert row["edge_after_costs"] == 0.10
    assert row["required_edge"] == 0.06
    assert row["skip_reasons"] == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest tests/test_runtime_api.py::test_runtime_probabilities_include_optional_ensemble_decision_fields -q
```

Expected: fails because runtime rows do not join `features.ensemble_decisions`.

- [ ] **Step 3: Merge latest decision fields into runtime rows**

In `src/polymarket_engine/probability/runtime.py`, update `latest_probability_output_rows` so the query left joins the latest `features.ensemble_decisions` by `state_id`. Each row should include:

```python
row["decision_hint"] = decision_hint
row["edge_after_costs"] = edge_after_costs
row["required_edge"] = required_edge
row["skip_reasons"] = json.loads(skip_reasons_json) if skip_reasons_json else []
row["generator_summary"] = json.loads(generator_summary_json) if generator_summary_json else {}
row["execution_summary"] = json.loads(execution_summary_json) if execution_summary_json else {}
row["supervised_live"] = json.loads(supervised_live_json) if supervised_live_json else {"action": "DISABLED"}
```

Use a query shape like:

```sql
with latest_decisions as (
    select *
    from features.ensemble_decisions
    qualify row_number() over (partition by state_id order by asof_ts desc, created_at desc) = 1
)
select
    probability_outputs.*,
    latest_decisions.decision_hint,
    latest_decisions.edge_after_costs,
    latest_decisions.required_edge,
    latest_decisions.skip_reasons_json,
    latest_decisions.generator_summary_json,
    latest_decisions.execution_summary_json,
    latest_decisions.supervised_live_json
from features.probability_outputs as probability_outputs
left join latest_decisions using (state_id)
order by probability_outputs.asof_ts desc
limit ?
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/test_runtime_api.py::test_runtime_probabilities_include_optional_ensemble_decision_fields tests/probability/test_runtime.py -q
uv run ruff check src/polymarket_engine/probability/runtime.py src/polymarket_engine/runtime_api.py tests/test_runtime_api.py tests/probability/test_runtime.py
```

Expected: all tests pass and Ruff reports no issues.

- [ ] **Step 5: Commit Task 7**

```bash
git add src/polymarket_engine/probability/runtime.py src/polymarket_engine/runtime_api.py tests/test_runtime_api.py tests/probability/test_runtime.py
git commit -m "Expose ensemble decisions in probability runtime"
```

## Task 8: Render Compact TUI Decision Fields

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/status.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`

- [ ] **Step 1: Add Rust serde and render tests**

In `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`, update the existing test setup so `RuntimeProbabilityRow` includes:

```rust
decision_hint: Some("PAPER_TRADE".to_string()),
edge_after_costs: Some(0.10),
required_edge: Some(0.06),
skip_reasons: vec![],
```

Update the assertions:

```rust
assert_eq!(
    probability_header_labels(),
    [
        "Contract",
        "p_finish",
        "p_no_touch",
        "Edge",
        "Req",
        "Hint/Reasons"
    ]
);
assert_eq!(rows[0].edge, "0.100");
assert_eq!(rows[0].required_edge, "0.060");
assert_eq!(rows[0].hint_reasons, "PAPER_TRADE");
```

- [ ] **Step 2: Run the Rust test to verify it fails**

Run:

```bash
cargo test -p polymarket-cockpit-tui probability_rows_render_read_only_probability_outputs
```

Expected: fails because the Rust status model lacks the new optional fields.

- [ ] **Step 3: Add optional runtime fields**

In `rust/crates/polymarket-cockpit-tui/src/status.rs`, add fields to `RuntimeProbabilityRow`:

```rust
#[serde(default)]
pub decision_hint: Option<String>,
#[serde(default)]
pub edge_after_costs: Option<f64>,
#[serde(default)]
pub required_edge: Option<f64>,
#[serde(default)]
pub skip_reasons: Vec<String>,
```

In `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`, change `ProbabilityDisplayRow` to:

```rust
pub struct ProbabilityDisplayRow {
    pub contract: String,
    pub p_finish: String,
    pub p_no_touch: String,
    pub edge: String,
    pub required_edge: String,
    pub hint_reasons: String,
}
```

Use this formatter:

```rust
fn hint_reasons(row: &RuntimeProbabilityRow) -> String {
    let hint = row
        .decision_hint
        .clone()
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "READ_ONLY".to_string());
    if row.skip_reasons.is_empty() {
        return hint;
    }
    format!("{hint} {}", row.skip_reasons.join(","))
}
```

- [ ] **Step 4: Run focused Rust tests**

Run:

```bash
cargo test -p polymarket-cockpit-tui probability
```

Expected: tests pass.

- [ ] **Step 5: Commit Task 8**

```bash
git add rust/crates/polymarket-cockpit-tui/src/status.rs rust/crates/polymarket-cockpit-tui/src/render/probability.rs
git commit -m "Show ensemble decision hints in TUI"
```

## Task 9: Final Verification

**Files:**
- Verify all changed files from Tasks 1-8.

- [ ] **Step 1: Run focused Python suite**

Run:

```bash
uv run pytest \
  tests/probability/test_generator_contracts.py \
  tests/probability/test_ensemble_outputs.py \
  tests/execution/test_book.py \
  tests/probability/test_decision_gates.py \
  tests/research/test_generator_validation.py \
  tests/storage/test_schema.py \
  tests/storage/test_normalized_writes.py \
  tests/probability/test_runtime.py \
  tests/test_runtime_api.py \
  -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run Python static checks**

Run:

```bash
uv run ruff check src tests
uv run mypy src tests
```

Expected: Ruff reports no issues and mypy reports success.

- [ ] **Step 3: Run Rust TUI checks**

Run:

```bash
cargo test -p polymarket-cockpit-tui probability
```

Expected: Rust probability render/status tests pass.

- [ ] **Step 4: Confirm no live execution surface exists**

Run:

```bash
rg -n "PRIVATE_KEY|API_KEY|ApiCreds|postOrder|createAndPostOrder|createAndPostMarketOrder|signer|funder|live_order_intent" src rust tests
```

Expected: only tests and disabled supervised-live JSON mention `live_order_intent`; no credential, signing, or order-posting symbols are introduced by this plan.

- [ ] **Step 5: Commit verification notes if docs changed during execution**

If execution updates the plan or design spec, commit those doc edits:

```bash
git add docs/superpowers/specs/2026-06-06-ensemble-execution-gates-design.md docs/superpowers/plans/2026-06-06-ensemble-execution-gates.md
git commit -m "Document ensemble execution gates plan"
```

## Self-Review

- Spec coverage: tasks cover generator contracts, ensemble math, execution liquidity, decision gates, persistence, validation, runtime API, TUI display, and safety verification.
- Placeholder scan: no task relies on unspecified live credentials, signing, or real order placement.
- Type consistency: `GeneratorRun`, `EnsembleProbability`, `ExecutionBookMetrics`, `DecisionInputs`, and `DecisionOutput` are named consistently across task snippets.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-06-ensemble-execution-gates.md`. Two execution options:

1. Subagent-Driven (recommended) - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. Inline Execution - execute tasks in this session using executing-plans, batch execution with checkpoints.

Recommended choice: Subagent-Driven.
