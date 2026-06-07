# Dynamic Monte Carlo Generator Weights and Decision Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixed Monte Carlo generator weights with tested, regime-aware reliability weights and wire the resulting `p_finish`, `p_no_touch`, `z_path`, dispersion, uncertainty, and gate outputs into the read-only decision layer.

**Architecture:** Keep the live collector and TUI read-only. Add pure probability/validation modules that compute generator-level outputs, score those outputs against official labels in chronological replay, derive conservative dynamic weights by bucket, and expose only cached decision summaries to API/TUI. Generator weights may change by validated state bucket, but they must be frozen from past data only at decision time.

**Tech Stack:** Python 3.11 via `uv`, DuckDB feature tables, pytest, Ruff, existing `src/polymarket_engine/probability/`, existing runtime API, existing Rust TUI consumers.

---

## Research Basis

- Local engine plan: `p_finish` prices the terminal one-dollar binary payoff; `p_no_touch` and `z_path` are path-risk/gate variables, not alternate payoff values.
- Local research paper: Monte Carlo calculates `p_finish` by terminal win indicators and `p_no_touch` by full-path survival indicators; `z_path = d_side / sigma_tau` is the volatility-normalized cushion used for conditioning, cache lookup, and gates.
- Gneiting and Raftery, "Strictly Proper Scoring Rules, Prediction, and Estimation" (2007): use proper scoring rules for probability quality, not hit rate alone. Source: https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf
- Yao, Vehtari, Simpson, and Gelman, "Using stacking to average Bayesian predictive distributions" (2018): stacking optimizes predictive mixtures under proper scoring rules and is better suited than naive model averaging when no candidate model is exactly true. Source: https://arxiv.org/abs/1704.02030
- Raftery, Karny, and Ettler, "Online Prediction Under Model Uncertainty via Dynamic Model Averaging" (2010): model weights can adapt over time with forgetting, useful when the best model changes. Source: https://sites.stat.washington.edu/people/raftery/Research/PDF/Karny2010.pdf
- Hansen, Lunde, and Nason, "The Model Confidence Set" (2011): sometimes data cannot identify one best model; keep a statistically defensible set instead of over-selecting. Source: https://www.kevinsheppard.com/files/teaching/mfe/advanced-econometrics/Hansen_Lunde_Nason.pdf
- Li, Kang, and Li, "Bayesian forecast combination using time-varying features" (2022): time-varying forecast weights can be driven by features rather than a single global constant. Source: https://arxiv.org/abs/2108.02082

## Gaps This Plan Fixes

1. Fixed `0.40 / 0.25 / 0.25 / 0.10` weights are only a seed config, not validated importance.
2. There is no formal `GeneratorRun` contract for four generator outputs.
3. There is no tested rule for dynamic weights by asset, time-left, `z_path`, volatility regime, or bucket quality.
4. There is no clear relationship between generator-level `p_finish`, `p_no_touch`, ensemble outputs, and `z_path`.
5. `mc_dispersion`, `path_diagnosis`, and `uncertainty_buffer` are not defined as first-class outputs.
6. Runtime gates do not consume probability uncertainty, path survival, or dispersion.
7. The API/TUI can show probabilities but cannot explain which generator mattered or why a setup is `WAIT`, `BLOCK`, or `DEMAND_MORE_EDGE`.

## File Structure

- Create `src/polymarket_engine/probability/generator_contracts.py`
  - Typed dataclasses/enums for `GeneratorId`, `GeneratorRun`, `GeneratorSet`, `DynamicWeightScope`, `GeneratorWeight`, and `WeightedEnsemble`.
- Create `src/polymarket_engine/probability/ensemble_weights.py`
  - Pure functions for scoring-rule losses, scope bucketing, static seed weights, dynamic reliability updates, weight smoothing, and sparse fallback.
- Create `src/polymarket_engine/probability/ensemble_outputs.py`
  - Pure reducer from generator runs plus weights into `p_finish`, `p_no_touch`, `mc_dispersion`, `uncertainty_buffer`, and `path_diagnosis`.
- Create `src/polymarket_engine/probability/decision_gates.py`
  - Pure read-only gate evaluator returning `decision_hint`, block reasons, demand-more-edge reasons, and required edge components.
- Create `src/polymarket_engine/research/generator_validation.py`
  - Chronological replay scoring, calibration tables, per-scope generator importance reports, and candidate weight-table builder.
- Modify `src/polymarket_engine/storage/schema.sql`
  - Add versioned storage for generator runs, dynamic weights, ensemble outputs, and gate decisions.
- Modify `src/polymarket_engine/probability/runtime.py`
  - Read latest persisted ensemble/gate summaries and preserve backward-compatible `p_finish`, `p_no_touch`, `z_path`, `sigma_tau` fields.
- Modify `src/polymarket_engine/runtime_api.py`
  - Extend probability rows with ensemble/gate diagnostics without triggering live Monte Carlo.
- Modify `rust/crates/polymarket-cockpit-tui/src/status.rs`
  - Add optional fields for `mc_dispersion`, `path_diagnosis`, `uncertainty_buffer`, `decision_hint`, and compact generator weights.
- Modify `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`
  - Display the new read-only ensemble/gate fields.
- Add focused tests under `tests/probability/`, `tests/research/`, `tests/storage/`, and Rust TUI tests.

## Core Math Contract

For generator `g` and decision state `x_t`:

```text
p_finish_g = mean( I(win_i) )
p_no_touch_g = mean( I(survive_i) )
z_path = d_side / sigma_tau
```

`z_path` is not averaged across generators. It is a state feature used to condition generator selection, weights, and gates.

Dynamic weights are bucketed by:

```text
scope = (
  asset,
  horizon_seconds,
  seconds_left_bucket,
  z_path_bucket,
  vol_regime,
  vol_trend,
  wick_regime,
  source_quality_state
)
```

Weights must be learned only from labeled decisions strictly before the current decision time:

```text
loss_finish_g = proper_score(p_finish_g, final_win_label)
loss_touch_g = proper_score(p_no_touch_g, no_touch_label)
loss_joint_g = alpha * loss_finish_g + (1 - alpha) * loss_touch_g
raw_weight_g = exp(-eta * decayed_mean_loss_g)
weight_g = raw_weight_g / sum(raw_weight)
```

Recommended v1:

```text
proper_score = log_loss for optimization
brier_score = calibration/report metric
alpha = 0.70
eta = 3.0
min_scope_labels = 100
forgetting_halflife_labels = 500
weight_floor = 0.05 for G1/G2/G3
stress_weight_cap = 0.10
```

Stress overlay rule:

```text
p_finish_stress_effective = min(p_finish_stress, median(p_finish_G1_G2_G3))
p_no_touch_stress_effective = min(p_no_touch_stress, median(p_no_touch_G1_G2_G3))
```

Stress can worsen confidence or required edge. It must not improve fair value.

Ensemble:

```text
p_finish = sum(weight_g * p_finish_g_effective)
p_no_touch = sum(weight_g * p_no_touch_g_effective)
mc_dispersion_finish = max(abs(p_finish_g_effective - median(p_finish_g_effective)))
mc_dispersion_touch = max(abs(p_no_touch_g_effective - median(p_no_touch_g_effective)))
mc_dispersion = max(mc_dispersion_finish, mc_dispersion_touch)
```

Uncertainty buffer:

```text
uncertainty_buffer =
  base_model_buffer
  + 0.50 * mc_dispersion
  + sparse_scope_penalty
  + calibration_penalty
  + stale_weight_penalty
```

Path diagnosis:

```text
CLEAN: p_no_touch passes, z_path passes, dispersion low, no sparse/fallback flags
TERMINAL_ONLY: p_finish has edge but p_no_touch fails
NEAR_THRESHOLD: abs(z_path) below regime floor
FRAGILE: dispersion elevated or stress overlay materially hurts
SPARSE: dynamic scope lacks enough labels and falls back
STALE_OR_UNSAFE: data/source/cache/rule/sigma invalid
```

Gate relationship:

```text
fair_value = p_finish
edge_after_costs = fair_value - executable_entry_price - execution_costs
required_edge =
  base_edge
  + execution_buffer
  + latency_buffer
  + source_buffer
  + uncertainty_buffer
  + path_risk_buffer
  + event_buffer
```

Read-only decision hint:

```text
TRADE_CANDIDATE if edge_after_costs >= required_edge
  and p_no_touch >= p_no_touch_floor(scope)
  and z_path >= z_path_floor(scope)
  and no hard gates fail

DEMAND_MORE_EDGE if no hard gate fails but edge_after_costs < required_edge
WAIT if path setup is unstable but not invalid
BLOCK if hard gate fails
DISABLED if probability engine is off
```

## Task 1: Add Generator Contracts

**Files:**
- Create: `src/polymarket_engine/probability/generator_contracts.py`
- Test: `tests/probability/test_generator_contracts.py`

- [ ] **Step 1: Write failing tests for generator schema validation**

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from polymarket_engine.probability.generator_contracts import (
    DynamicWeightScope,
    GeneratorId,
    GeneratorRun,
)


def test_generator_run_requires_probability_values() -> None:
    with pytest.raises(ValueError, match="p_finish"):
        GeneratorRun(
            generator_id=GeneratorId.EMPIRICAL_CONDITIONAL,
            p_finish=1.2,
            p_no_touch=0.4,
            path_count=1024,
            seed=1,
            asof_ts=datetime(2026, 6, 5, tzinfo=UTC),
            diagnostics={},
        )


def test_generator_run_accepts_valid_read_only_output() -> None:
    run = GeneratorRun(
        generator_id=GeneratorId.BLOCK_BOOTSTRAP,
        p_finish=0.55,
        p_no_touch=0.42,
        path_count=2048,
        seed=7,
        asof_ts=datetime(2026, 6, 5, tzinfo=UTC),
        diagnostics={"bucket": "btc-5m"},
    )

    assert run.generator_id == GeneratorId.BLOCK_BOOTSTRAP
    assert run.p_finish == 0.55
    assert run.p_no_touch == 0.42
    assert run.path_count == 2048


def test_dynamic_weight_scope_is_hashable_and_specific() -> None:
    scope = DynamicWeightScope(
        asset="BTC",
        horizon_seconds=300,
        seconds_left_bucket="120-180",
        z_path_bucket="0.25-0.50",
        vol_regime="normal",
        vol_trend="flat",
        wick_regime="quiet",
        source_quality_state="ok",
    )

    assert {scope: "ok"}[scope] == "ok"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/probability/test_generator_contracts.py -q
```

Expected: import failure because `generator_contracts.py` does not exist.

- [ ] **Step 3: Implement generator contracts**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Any


class GeneratorId(StrEnum):
    EMPIRICAL_CONDITIONAL = "empirical_conditional"
    BLOCK_BOOTSTRAP = "block_bootstrap"
    FILTERED_HISTORICAL = "filtered_historical"
    STRESS_OVERLAY = "stress_overlay"
    LOGNORMAL_BASELINE = "lognormal_baseline"


@dataclass(frozen=True)
class GeneratorRun:
    generator_id: GeneratorId
    p_finish: float
    p_no_touch: float
    path_count: int
    seed: int
    asof_ts: datetime
    diagnostics: dict[str, Any] = field(default_factory=dict)
    sparse: bool = False
    fallback_level: str = "none"

    def __post_init__(self) -> None:
        _require_probability(self.p_finish, "p_finish")
        _require_probability(self.p_no_touch, "p_no_touch")
        if self.path_count <= 0:
            raise ValueError("path_count must be positive")
        if not isinstance(self.seed, int):
            raise ValueError("seed must be an int")
        if self.asof_ts.tzinfo is None:
            raise ValueError("asof_ts must be timezone-aware")
        if not isinstance(self.diagnostics, dict):
            raise ValueError("diagnostics must be an object")


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


@dataclass(frozen=True)
class GeneratorWeight:
    generator_id: GeneratorId
    weight: float
    scope: DynamicWeightScope
    label_count: int
    source: str
    score: float | None = None

    def __post_init__(self) -> None:
        _require_probability(self.weight, "weight")
        if self.label_count < 0:
            raise ValueError("label_count must be non-negative")
        if self.score is not None and not isfinite(self.score):
            raise ValueError("score must be finite")


def _require_probability(value: float, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be numeric")
    if not isfinite(float(value)) or not 0.0 <= float(value) <= 1.0:
        raise ValueError(f"{name} must be in [0, 1]")
```

- [ ] **Step 4: Verify task**

```bash
uv run pytest tests/probability/test_generator_contracts.py -q
uv run ruff check src/polymarket_engine/probability/generator_contracts.py tests/probability/test_generator_contracts.py
```

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/probability/generator_contracts.py tests/probability/test_generator_contracts.py
git commit -m "Add Monte Carlo generator contracts"
```

## Task 2: Add Proper-Scoring Weight Updates

**Files:**
- Create: `src/polymarket_engine/probability/ensemble_weights.py`
- Test: `tests/probability/test_ensemble_weights.py`

- [ ] **Step 1: Write failing tests for scoring and dynamic weights**

```python
from __future__ import annotations

import pytest

from polymarket_engine.probability.ensemble_weights import (
    DEFAULT_SEED_WEIGHTS,
    brier_loss,
    dynamic_weights_from_losses,
    log_loss,
)
from polymarket_engine.probability.generator_contracts import GeneratorId


def test_log_loss_rewards_better_binary_probability() -> None:
    assert log_loss(0.80, 1) < log_loss(0.55, 1)
    assert log_loss(0.20, 0) < log_loss(0.45, 0)


def test_brier_loss_rewards_better_binary_probability() -> None:
    assert brier_loss(0.80, 1) < brier_loss(0.55, 1)
    assert brier_loss(0.20, 0) < brier_loss(0.45, 0)


def test_dynamic_weights_shift_toward_lower_loss_without_zeroing_generators() -> None:
    weights = dynamic_weights_from_losses(
        losses={
            GeneratorId.EMPIRICAL_CONDITIONAL: 0.20,
            GeneratorId.BLOCK_BOOTSTRAP: 0.35,
            GeneratorId.FILTERED_HISTORICAL: 0.40,
            GeneratorId.STRESS_OVERLAY: 0.50,
        },
        seed_weights=DEFAULT_SEED_WEIGHTS,
        eta=3.0,
        weight_floor=0.05,
        stress_weight_cap=0.10,
    )

    assert weights[GeneratorId.EMPIRICAL_CONDITIONAL] > weights[GeneratorId.BLOCK_BOOTSTRAP]
    assert weights[GeneratorId.STRESS_OVERLAY] <= 0.10
    assert sum(weights.values()) == pytest.approx(1.0)
    assert min(weights.values()) >= 0.05
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/probability/test_ensemble_weights.py -q
```

Expected: import failure because `ensemble_weights.py` does not exist.

- [ ] **Step 3: Implement scoring and weight update**

```python
from __future__ import annotations

from math import exp, log

from polymarket_engine.probability.generator_contracts import GeneratorId


DEFAULT_SEED_WEIGHTS: dict[GeneratorId, float] = {
    GeneratorId.EMPIRICAL_CONDITIONAL: 0.40,
    GeneratorId.BLOCK_BOOTSTRAP: 0.25,
    GeneratorId.FILTERED_HISTORICAL: 0.25,
    GeneratorId.STRESS_OVERLAY: 0.10,
}


def log_loss(probability: float, label: int, *, eps: float = 1e-6) -> float:
    if label not in {0, 1}:
        raise ValueError("label must be 0 or 1")
    p = min(max(float(probability), eps), 1.0 - eps)
    return -log(p) if label == 1 else -log(1.0 - p)


def brier_loss(probability: float, label: int) -> float:
    if label not in {0, 1}:
        raise ValueError("label must be 0 or 1")
    return (float(probability) - float(label)) ** 2


def dynamic_weights_from_losses(
    *,
    losses: dict[GeneratorId, float],
    seed_weights: dict[GeneratorId, float],
    eta: float,
    weight_floor: float,
    stress_weight_cap: float,
) -> dict[GeneratorId, float]:
    if eta <= 0.0:
        raise ValueError("eta must be positive")
    if not 0.0 <= weight_floor < 1.0:
        raise ValueError("weight_floor must be in [0, 1)")
    raw: dict[GeneratorId, float] = {}
    for generator_id, seed_weight in seed_weights.items():
        if seed_weight <= 0.0:
            raise ValueError("seed weights must be positive")
        raw[generator_id] = seed_weight * exp(-eta * losses[generator_id])

    floored = {
        generator_id: max(weight_floor, value)
        for generator_id, value in raw.items()
    }
    if GeneratorId.STRESS_OVERLAY in floored:
        floored[GeneratorId.STRESS_OVERLAY] = min(
            floored[GeneratorId.STRESS_OVERLAY],
            stress_weight_cap,
        )
    total = sum(floored.values())
    if total <= 0.0:
        raise ValueError("weights must sum positive")
    return {generator_id: value / total for generator_id, value in floored.items()}
```

- [ ] **Step 4: Verify task**

```bash
uv run pytest tests/probability/test_ensemble_weights.py -q
uv run ruff check src/polymarket_engine/probability/ensemble_weights.py tests/probability/test_ensemble_weights.py
```

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/probability/ensemble_weights.py tests/probability/test_ensemble_weights.py
git commit -m "Add dynamic generator weight scoring"
```

## Task 3: Add Ensemble Reducer Outputs

**Files:**
- Create: `src/polymarket_engine/probability/ensemble_outputs.py`
- Test: `tests/probability/test_ensemble_outputs.py`

- [ ] **Step 1: Write failing reducer tests**

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from polymarket_engine.probability.ensemble_outputs import reduce_generator_runs
from polymarket_engine.probability.generator_contracts import GeneratorId, GeneratorRun


def _run(generator_id: GeneratorId, p_finish: float, p_no_touch: float) -> GeneratorRun:
    return GeneratorRun(
        generator_id=generator_id,
        p_finish=p_finish,
        p_no_touch=p_no_touch,
        path_count=1024,
        seed=1,
        asof_ts=datetime(2026, 6, 5, tzinfo=UTC),
        diagnostics={},
    )


def test_reduce_generator_runs_uses_weighted_finish_and_touch() -> None:
    output = reduce_generator_runs(
        runs=[
            _run(GeneratorId.EMPIRICAL_CONDITIONAL, 0.60, 0.50),
            _run(GeneratorId.BLOCK_BOOTSTRAP, 0.50, 0.40),
            _run(GeneratorId.FILTERED_HISTORICAL, 0.55, 0.45),
            _run(GeneratorId.STRESS_OVERLAY, 0.40, 0.20),
        ],
        weights={
            GeneratorId.EMPIRICAL_CONDITIONAL: 0.40,
            GeneratorId.BLOCK_BOOTSTRAP: 0.25,
            GeneratorId.FILTERED_HISTORICAL: 0.25,
            GeneratorId.STRESS_OVERLAY: 0.10,
        },
        z_path=0.8,
        sparse_scope=False,
        calibration_penalty=0.0,
        stale_weight_penalty=0.0,
    )

    assert output.p_finish == pytest.approx(0.545)
    assert output.p_no_touch == pytest.approx(0.425)
    assert output.mc_dispersion > 0.0
    assert output.uncertainty_buffer >= output.mc_dispersion * 0.50


def test_stress_overlay_cannot_improve_fair_value() -> None:
    output = reduce_generator_runs(
        runs=[
            _run(GeneratorId.EMPIRICAL_CONDITIONAL, 0.55, 0.50),
            _run(GeneratorId.BLOCK_BOOTSTRAP, 0.54, 0.49),
            _run(GeneratorId.FILTERED_HISTORICAL, 0.53, 0.48),
            _run(GeneratorId.STRESS_OVERLAY, 0.95, 0.95),
        ],
        weights={
            GeneratorId.EMPIRICAL_CONDITIONAL: 0.40,
            GeneratorId.BLOCK_BOOTSTRAP: 0.25,
            GeneratorId.FILTERED_HISTORICAL: 0.25,
            GeneratorId.STRESS_OVERLAY: 0.10,
        },
        z_path=0.8,
        sparse_scope=False,
        calibration_penalty=0.0,
        stale_weight_penalty=0.0,
    )

    assert output.p_finish < 0.56
    assert output.path_diagnosis in {"CLEAN", "FRAGILE"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/probability/test_ensemble_outputs.py -q
```

- [ ] **Step 3: Implement reducer**

```python
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from polymarket_engine.probability.generator_contracts import GeneratorId, GeneratorRun


@dataclass(frozen=True)
class EnsembleOutput:
    p_finish: float
    p_no_touch: float
    mc_dispersion: float
    uncertainty_buffer: float
    path_diagnosis: str
    effective_weights: dict[str, float]


def reduce_generator_runs(
    *,
    runs: list[GeneratorRun],
    weights: dict[GeneratorId, float],
    z_path: float,
    sparse_scope: bool,
    calibration_penalty: float,
    stale_weight_penalty: float,
) -> EnsembleOutput:
    by_id = {run.generator_id: run for run in runs}
    core_ids = [
        GeneratorId.EMPIRICAL_CONDITIONAL,
        GeneratorId.BLOCK_BOOTSTRAP,
        GeneratorId.FILTERED_HISTORICAL,
    ]
    core_finish_median = median(by_id[gid].p_finish for gid in core_ids if gid in by_id)
    core_touch_median = median(by_id[gid].p_no_touch for gid in core_ids if gid in by_id)

    finish_values: dict[GeneratorId, float] = {}
    touch_values: dict[GeneratorId, float] = {}
    for generator_id, run in by_id.items():
        if generator_id == GeneratorId.STRESS_OVERLAY:
            finish_values[generator_id] = min(run.p_finish, core_finish_median)
            touch_values[generator_id] = min(run.p_no_touch, core_touch_median)
        else:
            finish_values[generator_id] = run.p_finish
            touch_values[generator_id] = run.p_no_touch

    total_weight = sum(weights[generator_id] for generator_id in finish_values)
    if total_weight <= 0.0:
        raise ValueError("weights must sum positive")
    normalized = {
        generator_id: weights[generator_id] / total_weight
        for generator_id in finish_values
    }
    p_finish = sum(normalized[gid] * finish_values[gid] for gid in finish_values)
    p_no_touch = sum(normalized[gid] * touch_values[gid] for gid in touch_values)
    dispersion_finish = max(abs(value - median(finish_values.values())) for value in finish_values.values())
    dispersion_touch = max(abs(value - median(touch_values.values())) for value in touch_values.values())
    mc_dispersion = max(dispersion_finish, dispersion_touch)
    sparse_penalty = 0.04 if sparse_scope else 0.0
    uncertainty_buffer = 0.01 + 0.50 * mc_dispersion + sparse_penalty + calibration_penalty + stale_weight_penalty
    diagnosis = _diagnose(
        p_no_touch=p_no_touch,
        z_path=z_path,
        mc_dispersion=mc_dispersion,
        sparse_scope=sparse_scope,
    )
    return EnsembleOutput(
        p_finish=p_finish,
        p_no_touch=p_no_touch,
        mc_dispersion=mc_dispersion,
        uncertainty_buffer=uncertainty_buffer,
        path_diagnosis=diagnosis,
        effective_weights={generator_id.value: weight for generator_id, weight in normalized.items()},
    )


def _diagnose(
    *,
    p_no_touch: float,
    z_path: float,
    mc_dispersion: float,
    sparse_scope: bool,
) -> str:
    if sparse_scope:
        return "SPARSE"
    if abs(z_path) < 0.5:
        return "NEAR_THRESHOLD"
    if p_no_touch < 0.55:
        return "TERMINAL_ONLY"
    if mc_dispersion > 0.05:
        return "FRAGILE"
    return "CLEAN"
```

- [ ] **Step 4: Verify task**

```bash
uv run pytest tests/probability/test_ensemble_outputs.py -q
uv run ruff check src/polymarket_engine/probability/ensemble_outputs.py tests/probability/test_ensemble_outputs.py
```

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/probability/ensemble_outputs.py tests/probability/test_ensemble_outputs.py
git commit -m "Add Monte Carlo ensemble reducer outputs"
```

## Task 4: Add Read-Only Decision Gate Evaluator

**Files:**
- Create: `src/polymarket_engine/probability/decision_gates.py`
- Test: `tests/probability/test_decision_gates.py`

- [ ] **Step 1: Write failing gate tests**

```python
from __future__ import annotations

from polymarket_engine.probability.decision_gates import evaluate_probability_gates
from polymarket_engine.probability.ensemble_outputs import EnsembleOutput


def _output(
    *,
    p_finish: float,
    p_no_touch: float,
    mc_dispersion: float,
    uncertainty_buffer: float,
    path_diagnosis: str,
) -> EnsembleOutput:
    return EnsembleOutput(
        p_finish=p_finish,
        p_no_touch=p_no_touch,
        mc_dispersion=mc_dispersion,
        uncertainty_buffer=uncertainty_buffer,
        path_diagnosis=path_diagnosis,
        effective_weights={},
    )


def test_high_edge_clean_path_becomes_trade_candidate() -> None:
    result = evaluate_probability_gates(
        ensemble=_output(
            p_finish=0.72,
            p_no_touch=0.80,
            mc_dispersion=0.02,
            uncertainty_buffer=0.02,
            path_diagnosis="CLEAN",
        ),
        z_path=1.1,
        executable_entry_price=0.60,
        execution_costs=0.01,
        hard_failures=[],
    )

    assert result.decision_hint == "TRADE_CANDIDATE"
    assert result.edge_after_costs > result.required_edge


def test_good_finish_weak_touch_demands_more_edge_or_waits() -> None:
    result = evaluate_probability_gates(
        ensemble=_output(
            p_finish=0.72,
            p_no_touch=0.40,
            mc_dispersion=0.03,
            uncertainty_buffer=0.03,
            path_diagnosis="TERMINAL_ONLY",
        ),
        z_path=0.3,
        executable_entry_price=0.55,
        execution_costs=0.01,
        hard_failures=[],
    )

    assert result.decision_hint in {"WAIT", "DEMAND_MORE_EDGE"}
    assert "p_no_touch below floor" in result.reasons


def test_hard_failure_blocks_even_with_edge() -> None:
    result = evaluate_probability_gates(
        ensemble=_output(
            p_finish=0.90,
            p_no_touch=0.90,
            mc_dispersion=0.01,
            uncertainty_buffer=0.01,
            path_diagnosis="CLEAN",
        ),
        z_path=2.0,
        executable_entry_price=0.50,
        execution_costs=0.01,
        hard_failures=["status file stale"],
    )

    assert result.decision_hint == "BLOCK"
    assert "status file stale" in result.reasons
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/probability/test_decision_gates.py -q
```

- [ ] **Step 3: Implement gate evaluator**

```python
from __future__ import annotations

from dataclasses import dataclass

from polymarket_engine.probability.ensemble_outputs import EnsembleOutput


@dataclass(frozen=True)
class ProbabilityGateResult:
    decision_hint: str
    edge_after_costs: float
    required_edge: float
    reasons: list[str]


def evaluate_probability_gates(
    *,
    ensemble: EnsembleOutput,
    z_path: float,
    executable_entry_price: float,
    execution_costs: float,
    hard_failures: list[str],
    base_edge: float = 0.03,
    p_no_touch_floor: float = 0.65,
    z_path_floor: float = 0.50,
) -> ProbabilityGateResult:
    edge_after_costs = ensemble.p_finish - executable_entry_price - execution_costs
    path_risk_buffer = 0.02 if ensemble.p_no_touch < p_no_touch_floor else 0.0
    z_path_buffer = 0.02 if z_path < z_path_floor else 0.0
    required_edge = base_edge + ensemble.uncertainty_buffer + path_risk_buffer + z_path_buffer
    reasons = list(hard_failures)

    if hard_failures:
        return ProbabilityGateResult("BLOCK", edge_after_costs, required_edge, reasons)
    if ensemble.mc_dispersion > 0.10:
        reasons.append("mc_dispersion above block threshold")
        return ProbabilityGateResult("BLOCK", edge_after_costs, required_edge, reasons)
    if ensemble.p_no_touch < p_no_touch_floor:
        reasons.append("p_no_touch below floor")
    if z_path < z_path_floor:
        reasons.append("z_path below floor")
    if ensemble.path_diagnosis in {"SPARSE", "STALE_OR_UNSAFE"}:
        reasons.append(f"path diagnosis {ensemble.path_diagnosis}")
        return ProbabilityGateResult("BLOCK", edge_after_costs, required_edge, reasons)
    if ensemble.path_diagnosis in {"TERMINAL_ONLY", "NEAR_THRESHOLD"}:
        return ProbabilityGateResult("WAIT", edge_after_costs, required_edge, reasons)
    if edge_after_costs >= required_edge:
        return ProbabilityGateResult("TRADE_CANDIDATE", edge_after_costs, required_edge, reasons)
    reasons.append("edge below required edge")
    return ProbabilityGateResult("DEMAND_MORE_EDGE", edge_after_costs, required_edge, reasons)
```

- [ ] **Step 4: Verify task**

```bash
uv run pytest tests/probability/test_decision_gates.py -q
uv run ruff check src/polymarket_engine/probability/decision_gates.py tests/probability/test_decision_gates.py
```

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/probability/decision_gates.py tests/probability/test_decision_gates.py
git commit -m "Add read only probability decision gates"
```

## Task 5: Add Chronological Generator Validation

**Files:**
- Create: `src/polymarket_engine/research/generator_validation.py`
- Modify: `src/polymarket_engine/cli.py`
- Test: `tests/research/test_generator_validation.py`

- [ ] **Step 1: Write failing validation tests**

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from polymarket_engine.research.generator_validation import (
    GeneratorPredictionLabel,
    score_generator_predictions,
)


def test_score_generator_predictions_uses_only_past_labels_for_scope() -> None:
    base = datetime(2026, 6, 5, tzinfo=UTC)
    rows = [
        GeneratorPredictionLabel(
            generator_id="empirical_conditional",
            asof_ts=base,
            p_finish=0.80,
            final_win_label=1,
            p_no_touch=0.70,
            no_touch_label=1,
        ),
        GeneratorPredictionLabel(
            generator_id="block_bootstrap",
            asof_ts=base + timedelta(minutes=5),
            p_finish=0.30,
            final_win_label=1,
            p_no_touch=0.40,
            no_touch_label=0,
        ),
    ]

    result = score_generator_predictions(rows, cutoff_ts=base + timedelta(minutes=1))

    assert set(result.losses) == {"empirical_conditional"}
    assert result.label_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/research/test_generator_validation.py -q
```

- [ ] **Step 3: Implement validation scoring**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean

from polymarket_engine.probability.ensemble_weights import log_loss


@dataclass(frozen=True)
class GeneratorPredictionLabel:
    generator_id: str
    asof_ts: datetime
    p_finish: float
    final_win_label: int
    p_no_touch: float
    no_touch_label: int


@dataclass(frozen=True)
class GeneratorScoreSummary:
    losses: dict[str, float]
    label_count: int


def score_generator_predictions(
    rows: list[GeneratorPredictionLabel],
    *,
    cutoff_ts: datetime,
    alpha: float = 0.70,
) -> GeneratorScoreSummary:
    usable = [row for row in rows if row.asof_ts < cutoff_ts]
    by_generator: dict[str, list[float]] = {}
    for row in usable:
        finish_loss = log_loss(row.p_finish, row.final_win_label)
        touch_loss = log_loss(row.p_no_touch, row.no_touch_label)
        by_generator.setdefault(row.generator_id, []).append(
            alpha * finish_loss + (1.0 - alpha) * touch_loss
        )
    return GeneratorScoreSummary(
        losses={generator_id: mean(losses) for generator_id, losses in by_generator.items()},
        label_count=len(usable),
    )
```

- [ ] **Step 4: Add CLI skeleton**

Add parser command in `src/polymarket_engine/cli.py`:

```python
validate_generators = subparsers.add_parser("validate-generator-weights")
validate_generators.add_argument("--db", required=True)
validate_generators.add_argument("--cutoff-ts", required=True)
validate_generators.add_argument("--out", required=True)
```

Add dispatch:

```python
if args.command == "validate-generator-weights":
    return _run_validate_generator_weights(args)
```

Add function:

```python
def _run_validate_generator_weights(args: argparse.Namespace) -> int:
    from pathlib import Path

    Path(args.out).write_text(
        "# Generator Weight Validation\n\nNo persisted generator rows were selected.\n",
        encoding="utf-8",
    )
    return 0
```

The first implementation writes a report stub so the CLI contract exists. Task 7 replaces the stub with DuckDB-backed scoring.

- [ ] **Step 5: Verify task**

```bash
uv run pytest tests/research/test_generator_validation.py -q
uv run ruff check src/polymarket_engine/research/generator_validation.py src/polymarket_engine/cli.py tests/research/test_generator_validation.py
```

- [ ] **Step 6: Commit**

```bash
git add src/polymarket_engine/research/generator_validation.py src/polymarket_engine/cli.py tests/research/test_generator_validation.py
git commit -m "Add chronological generator validation scoring"
```

## Task 6: Add Persistence Tables

**Files:**
- Modify: `src/polymarket_engine/storage/schema.sql`
- Test: `tests/storage/test_probability_ensemble_schema.py`

- [ ] **Step 1: Write failing schema tests**

```python
from __future__ import annotations

import duckdb

from polymarket_engine.storage.schema import apply_schema


def test_probability_ensemble_tables_exist(tmp_path) -> None:
    db_path = tmp_path / "test.duckdb"
    con = duckdb.connect(str(db_path))
    apply_schema(con)

    tables = {
        row[0]
        for row in con.execute(
            """
            SELECT table_schema || '.' || table_name
            FROM information_schema.tables
            WHERE table_schema IN ('features', 'research')
            """
        ).fetchall()
    }

    assert "features.generator_runs" in tables
    assert "features.ensemble_probability_outputs" in tables
    assert "features.probability_gate_outputs" in tables
    assert "research.generator_weight_snapshots" in tables
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/storage/test_probability_ensemble_schema.py -q
```

- [ ] **Step 3: Add schema tables**

Add to `src/polymarket_engine/storage/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS features.generator_runs (
    output_id TEXT NOT NULL,
    state_id TEXT NOT NULL,
    generator_id TEXT NOT NULL,
    asof_ts TIMESTAMP NOT NULL,
    p_finish DOUBLE NOT NULL,
    p_no_touch DOUBLE NOT NULL,
    path_count INTEGER NOT NULL,
    seed BIGINT NOT NULL,
    sparse BOOLEAN NOT NULL DEFAULT FALSE,
    fallback_level TEXT NOT NULL DEFAULT 'none',
    diagnostics_json JSON NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (output_id, generator_id)
);

CREATE TABLE IF NOT EXISTS features.ensemble_probability_outputs (
    output_id TEXT PRIMARY KEY,
    state_id TEXT NOT NULL,
    asof_ts TIMESTAMP NOT NULL,
    p_finish DOUBLE NOT NULL,
    p_no_touch DOUBLE NOT NULL,
    z_path DOUBLE NOT NULL,
    sigma_tau DOUBLE NOT NULL,
    mc_dispersion DOUBLE NOT NULL,
    uncertainty_buffer DOUBLE NOT NULL,
    path_diagnosis TEXT NOT NULL,
    weights_json JSON NOT NULL,
    diagnostics_json JSON NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS features.probability_gate_outputs (
    gate_id TEXT PRIMARY KEY,
    output_id TEXT NOT NULL,
    state_id TEXT NOT NULL,
    asof_ts TIMESTAMP NOT NULL,
    decision_hint TEXT NOT NULL,
    edge_after_costs DOUBLE,
    required_edge DOUBLE,
    reasons_json JSON NOT NULL,
    read_only BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS research.generator_weight_snapshots (
    weight_snapshot_id TEXT PRIMARY KEY,
    scope_json JSON NOT NULL,
    cutoff_ts TIMESTAMP NOT NULL,
    label_count INTEGER NOT NULL,
    weights_json JSON NOT NULL,
    scores_json JSON NOT NULL,
    method_version TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 4: Verify task**

```bash
uv run pytest tests/storage/test_probability_ensemble_schema.py -q
uv run ruff check tests/storage/test_probability_ensemble_schema.py
```

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/storage/schema.sql tests/storage/test_probability_ensemble_schema.py
git commit -m "Add ensemble probability storage tables"
```

## Task 7: Build Weight Snapshot Reports

**Files:**
- Modify: `src/polymarket_engine/research/generator_validation.py`
- Modify: `src/polymarket_engine/cli.py`
- Test: `tests/research/test_generator_weight_report.py`

- [ ] **Step 1: Write failing report test**

```python
from __future__ import annotations

from pathlib import Path

from polymarket_engine.research.generator_validation import render_weight_report


def test_render_weight_report_includes_method_and_weights() -> None:
    report = render_weight_report(
        method_version="dynamic-generator-weights-v1",
        label_count=120,
        weights={"empirical_conditional": 0.52, "block_bootstrap": 0.18},
        losses={"empirical_conditional": 0.44, "block_bootstrap": 0.72},
    )

    assert "dynamic-generator-weights-v1" in report
    assert "empirical_conditional" in report
    assert "0.52" in report
    assert "120" in report
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/research/test_generator_weight_report.py -q
```

- [ ] **Step 3: Implement report renderer**

```python
def render_weight_report(
    *,
    method_version: str,
    label_count: int,
    weights: dict[str, float],
    losses: dict[str, float],
) -> str:
    lines = [
        "# Generator Weight Validation",
        "",
        f"- method_version: `{method_version}`",
        f"- label_count: `{label_count}`",
        "",
        "| generator | weight | mean_loss |",
        "| --- | ---: | ---: |",
    ]
    for generator_id, weight in sorted(weights.items()):
        loss = losses.get(generator_id)
        loss_text = "" if loss is None else f"{loss:.6f}"
        lines.append(f"| {generator_id} | {weight:.2f} | {loss_text} |")
    lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Replace CLI stub with report renderer**

In `_run_validate_generator_weights`, replace the stub body:

```python
from pathlib import Path

from polymarket_engine.research.generator_validation import render_weight_report

report = render_weight_report(
    method_version="dynamic-generator-weights-v1",
    label_count=0,
    weights={},
    losses={},
)
Path(args.out).write_text(report, encoding="utf-8")
return 0
```

The DuckDB query should be added after real `features.generator_runs` and official labels are populated; until then, the command gives a stable report contract.

- [ ] **Step 5: Verify task**

```bash
uv run pytest tests/research/test_generator_weight_report.py tests/research/test_generator_validation.py -q
uv run ruff check src/polymarket_engine/research/generator_validation.py src/polymarket_engine/cli.py tests/research/test_generator_weight_report.py
```

- [ ] **Step 6: Commit**

```bash
git add src/polymarket_engine/research/generator_validation.py src/polymarket_engine/cli.py tests/research/test_generator_weight_report.py
git commit -m "Add generator weight validation report"
```

## Task 8: Extend Runtime API Rows

**Files:**
- Modify: `src/polymarket_engine/probability/runtime.py`
- Modify: `src/polymarket_engine/runtime_api.py`
- Test: `tests/probability/test_probability_runtime_ensemble_fields.py`
- Test: `tests/test_runtime_api.py`

- [ ] **Step 1: Write failing API field test**

```python
from __future__ import annotations

from polymarket_engine.probability.runtime import _promote_probability_diagnostics


def test_probability_runtime_promotes_ensemble_diagnostics() -> None:
    row = {
        "diagnostics": {
            "mc_dispersion": 0.07,
            "uncertainty_buffer": 0.05,
            "path_diagnosis": "FRAGILE",
            "decision_hint": "DEMAND_MORE_EDGE",
            "effective_weights": {"empirical_conditional": 0.5},
        }
    }

    promoted = _promote_probability_diagnostics(row)

    assert promoted["mc_dispersion"] == 0.07
    assert promoted["uncertainty_buffer"] == 0.05
    assert promoted["path_diagnosis"] == "FRAGILE"
    assert promoted["decision_hint"] == "DEMAND_MORE_EDGE"
    assert promoted["effective_weights"] == {"empirical_conditional": 0.5}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/probability/test_probability_runtime_ensemble_fields.py -q
```

- [ ] **Step 3: Promote optional fields in runtime rows**

In `src/polymarket_engine/probability/runtime.py`, update the diagnostics promotion helper:

```python
for field in (
    "mc_dispersion",
    "uncertainty_buffer",
    "path_diagnosis",
    "decision_hint",
    "effective_weights",
):
    if field in diagnostics:
        row[field] = diagnostics[field]
```

- [ ] **Step 4: Verify task**

```bash
uv run pytest tests/probability/test_probability_runtime_ensemble_fields.py tests/test_runtime_api.py -q
uv run ruff check src/polymarket_engine/probability/runtime.py src/polymarket_engine/runtime_api.py tests/probability/test_probability_runtime_ensemble_fields.py tests/test_runtime_api.py
```

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/probability/runtime.py src/polymarket_engine/runtime_api.py tests/probability/test_probability_runtime_ensemble_fields.py tests/test_runtime_api.py
git commit -m "Expose ensemble diagnostics in runtime probabilities"
```

## Task 9: Extend Rust TUI Probability Display

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/status.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`
- Test: existing Rust TUI tests

- [ ] **Step 1: Add optional serde fields to `RuntimeProbabilityRow`**

Add fields:

```rust
pub mc_dispersion: Option<f64>,
pub uncertainty_buffer: Option<f64>,
pub path_diagnosis: Option<String>,
pub decision_hint: Option<String>,
pub effective_weights: Option<serde_json::Value>,
```

- [ ] **Step 2: Render compact columns**

Add columns after `z_path`:

```text
disp | buffer | diagnosis | hint | weights
```

Render weights compactly:

```text
emp=0.52 boot=0.18 filt=0.20 stress=0.10
```

- [ ] **Step 3: Run Rust verification**

```bash
cargo fmt -p polymarket-cockpit-tui
cargo test -p polymarket-cockpit-tui
```

Expected: all TUI tests pass.

- [ ] **Step 4: Commit**

```bash
git add rust/crates/polymarket-cockpit-tui/src/status.rs rust/crates/polymarket-cockpit-tui/src/render/probability.rs
git commit -m "Show ensemble gate diagnostics in TUI"
```

## Task 10: Documentation and Research Guardrails

**Files:**
- Create: `docs/probability-generator-weights.md`
- Modify: `docs/BINARY_CONTRACT_ENGINE_PLAN.md`
- Test: `tests/docs/test_active_runtime_docs.py`

- [ ] **Step 1: Write method doc**

Create `docs/probability-generator-weights.md` with:

```markdown
# Probability Generator Weights

Generator weights are not alpha signals. They are reliability weights learned from chronological replay labels.

## Outputs

- `p_finish`: weighted terminal probability for the binary payoff.
- `p_no_touch`: weighted path-survival probability used for risk gates.
- `z_path`: current volatility-normalized cushion; it is not averaged and does not settle a contract.
- `mc_dispersion`: disagreement across generator probabilities.
- `uncertainty_buffer`: required-edge addition from model uncertainty.
- `path_diagnosis`: compact explanation of path quality.

## Rules

- Use only labels with `asof_ts` before the decision timestamp when selecting dynamic weights.
- Freeze the first validation configuration before serious backtests.
- Stress overlay can increase required edge or block; it cannot improve fair value.
- Sparse scopes fall back to coarser/default weights and add an uncertainty penalty.
- Live/TUI surfaces are read-only and must not run full Monte Carlo on repaint.
```

- [ ] **Step 2: Add engine-plan note**

Add to the generator-weight policy section:

```markdown
Dynamic weights may be introduced only after chronological validation exists. The first method is scoring-rule based reliability by scope, using log loss for optimization and Brier/calibration curves for reporting. Weight scopes include asset, horizon, seconds-left bucket, `z_path` bucket, volatility regime, wick regime, and source-quality state. Sparse scopes fall back and add uncertainty. Stress overlays cannot improve fair value.
```

- [ ] **Step 3: Verify docs**

```bash
uv run pytest tests/docs/test_active_runtime_docs.py -q
```

- [ ] **Step 4: Commit**

```bash
git add docs/probability-generator-weights.md docs/BINARY_CONTRACT_ENGINE_PLAN.md tests/docs/test_active_runtime_docs.py
git commit -m "Document dynamic generator weight policy"
```

## Task 11: Final Verification

**Files:** no direct edits unless verification finds a defect.

- [ ] **Step 1: Run focused Python checks**

```bash
uv run pytest tests/probability tests/research tests/storage tests/test_runtime_api.py -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run lint on changed Python files**

```bash
uv run ruff check src/polymarket_engine/probability src/polymarket_engine/research src/polymarket_engine/runtime_api.py tests/probability tests/research tests/storage tests/test_runtime_api.py
```

Expected: no Ruff errors.

- [ ] **Step 3: Run Rust TUI checks**

```bash
cargo test -p polymarket-cockpit-tui
```

Expected: all TUI tests pass.

- [ ] **Step 4: Verify no live-trading hooks were introduced**

```bash
rg -n "private_key|wallet|sign|place_order|create_order|submit_order|live_order" src rust tests
```

Expected: no new real trading path. Existing SDK references, if any, must remain read-only market-data usage.

- [ ] **Step 5: Commit final verification note**

```bash
git status --short
git commit --allow-empty -m "Verify dynamic generator weights and gates"
```

## Execution Notes

- Implement this in a fresh worktree. Current repo state may contain unrelated dirty files.
- Use subagents by file boundary:
  - Probability contracts/reducers: Tasks 1-4.
  - Research validation/storage: Tasks 5-7.
  - API/TUI surface: Tasks 8-9.
  - Docs/final verification: Tasks 10-11.
- Keep all outputs read-only. This creates decision hints, not live trades.
- Do not deploy to THEPC until tests pass and the user explicitly approves deploy.

## Self-Review

- Covers dynamic generator importance with proper scoring and time-varying feature buckets.
- Preserves `p_finish` as fair value and `p_no_touch`/`z_path` as path-risk and gate inputs.
- Adds missing outputs: `mc_dispersion`, `path_diagnosis`, `uncertainty_buffer`, `decision_hint`, effective weights.
- Keeps stress overlay from increasing fair value.
- Prevents future leakage by using only prior labels for weight selection.
- Includes test-first tasks, exact files, verification commands, and commit checkpoints.
