# Coherent Probability UI and Rollover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make UP/DOWN terminal probabilities coherent, move stress overlays into a separate risk-adjusted lane, make browser/TUI labels truthful, and add immediate next-contract NOWCAST visibility plus calibration reporting for the `.99` market versus `.889` model gap.

**Architecture:** Keep `p_finish` as the terminal fair-value probability and make each binary UP/DOWN pair sum to approximately 1.0. Preserve the stress overlay as `risk_adjusted_p_finish` and diagnostics rather than letting it remove probability mass from both sides. Publish pair-coherence fields through runtime JSON, browser UI, TUI, and probability events, then add a replay-safe calibration report to measure whether the model is systematically under-confident near expiry.

**Tech Stack:** Python 3.10+ probability runtime, pytest, TypeScript React browser UI, Node UI helper tests, Rust ratatui TUI tests, DuckDB-backed runtime artifacts.

---

## File Structure

- Modify: `src/polymarket_engine/probability/ensemble_outputs.py`
  - Responsibility: split terminal ensemble probability from stress/risk-adjusted probability.
- Modify: `src/polymarket_engine/probability/ensemble_runtime.py`
  - Responsibility: publish new ensemble diagnostics and keep preview/generator metadata unchanged.
- Create: `src/polymarket_engine/probability/pair_coherence.py`
  - Responsibility: normalize UP/DOWN rows by market window and attach pair-sum diagnostics.
- Modify: `src/polymarket_engine/probability/gpu_worker.py`
  - Responsibility: normalize live worker rows before status/event publication and include pair diagnostics in event payloads.
- Modify: `src/polymarket_engine/probability/runtime.py`
  - Responsibility: normalize fallback/runtime rows and promote new diagnostics from persisted outputs.
- Modify: `src/polymarket_engine/runtime_api.py`
  - Responsibility: keep probability status rows and thresholds compatible with the new fields.
- Modify: `ui/src/probabilityRows.ts`
  - Responsibility: provide display helpers for terminal probability, risk-adjusted probability, and pair-coherence labels.
- Modify: `ui/src/App.tsx`
  - Responsibility: relabel browser metrics and pair cards so users see terminal probability separately from risk adjustment.
- Create: `ui/src/ErrorBoundary.tsx`
  - Responsibility: show a visible UI failure panel instead of a blank browser app when React rendering crashes.
- Modify: `ui/src/main.tsx`
  - Responsibility: wrap `<App />` in the error boundary.
- Modify: `rust/crates/polymarket-cockpit-tui/src/status.rs`
  - Responsibility: deserialize new probability fields without failing older payloads.
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`
  - Responsibility: relabel TUI probability columns and show risk-adjusted probability separately.
- Modify: `src/polymarket_engine/research/generator_validation.py`
  - Responsibility: add calibration metrics for settled labels without leaking future labels into live decisions.
- Modify: `docs/probability-generator-weights.md`
  - Responsibility: document `p_finish` versus `risk_adjusted_p_finish` and pair-coherence expectations.
- Modify: `docs/PART_TWO_LIVE_COLLECTORS.md`
  - Responsibility: document immediate NOWCAST/rollover behavior and runtime visibility guarantees.
- Test: `tests/probability/test_ensemble_outputs.py`
- Test: `tests/probability/test_ensemble_runtime.py`
- Test: `tests/probability/test_pair_coherence.py`
- Test: `tests/probability/test_gpu_worker.py`
- Test: `tests/probability/test_runtime.py`
- Test: `tests/probability/test_runtime_cache.py`
- Test: `tests/ui/probability_value_test.ts`
- Test: `tests/ui/probability_rows_test.ts`
- Test: `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`
- Test: `tests/research/test_generator_validation.py`
- Test: `tests/docs/test_active_runtime_docs.py`

---

### Task 1: Split terminal probability from stress-adjusted probability in the ensemble reducer

**Files:**
- Modify: `src/polymarket_engine/probability/ensemble_outputs.py`
- Modify: `src/polymarket_engine/probability/ensemble_runtime.py`
- Test: `tests/probability/test_ensemble_outputs.py`
- Test: `tests/probability/test_ensemble_runtime.py`

- [ ] **Step 1: Write failing reducer tests**

Append this test to `tests/probability/test_ensemble_outputs.py`:

```python
def test_reduce_ensemble_keeps_terminal_probability_separate_from_stress_haircut() -> None:
    result = reduce_ensemble(
        runs=(
            _run(GeneratorId.EMPIRICAL_CONDITIONAL, 0.60, 0.70),
            _run(GeneratorId.BLOCK_BOOTSTRAP, 0.58, 0.68),
            _run(GeneratorId.FILTERED_HISTORICAL, 0.62, 0.72),
            _run(GeneratorId.STRESS_OVERLAY, 0.20, 0.30),
        ),
        weights=(
            GeneratorWeight(GeneratorId.EMPIRICAL_CONDITIONAL, 0.40),
            GeneratorWeight(GeneratorId.BLOCK_BOOTSTRAP, 0.25),
            GeneratorWeight(GeneratorId.FILTERED_HISTORICAL, 0.25),
            GeneratorWeight(GeneratorId.STRESS_OVERLAY, 0.10),
        ),
        base_model_buffer=0.01,
    )

    assert result.p_finish == pytest.approx(0.60)
    assert result.p_no_touch == pytest.approx(0.70)
    assert result.risk_adjusted_p_finish == pytest.approx(0.56)
    assert result.risk_adjusted_p_no_touch == pytest.approx(0.66)
    assert result.risk_adjustment == pytest.approx(0.04)
    assert result.effective_generator_values["stress_overlay"]["p_finish"] == 0.20
```

Append this assertion to `test_reduce_ensemble_caps_stress_overlay_so_it_cannot_improve_probability`:

```python
    assert result.risk_adjusted_p_finish == pytest.approx(0.60)
    assert result.risk_adjustment == pytest.approx(0.0)
```

- [ ] **Step 2: Run reducer tests and verify failure**

Run:

```bash
uv run pytest tests/probability/test_ensemble_outputs.py -q
```

Expected: fail because `EnsembleProbability` has no `risk_adjusted_p_finish`, `risk_adjusted_p_no_touch`, or `risk_adjustment` fields.

- [ ] **Step 3: Implement reducer split**

In `src/polymarket_engine/probability/ensemble_outputs.py`, update the dataclass and reducer with this structure:

```python
@dataclass(frozen=True)
class EnsembleProbability:
    p_finish: float
    p_no_touch: float
    risk_adjusted_p_finish: float
    risk_adjusted_p_no_touch: float
    risk_adjustment: float
    u_gen_finish: float
    u_gen_touch: float
    u_gen: float
    mc_dispersion: float
    uncertainty_buffer: float
    path_diagnosis: PathDiagnosis
    effective_generator_values: dict[str, dict[str, float]]
```

Replace the probability calculation block in `reduce_ensemble` with:

```python
    normalized = {
        generator_id: weight / total_weight
        for generator_id, weight in weights_by_id.items()
    }
    effective_finish, effective_touch = _effective_values(runs_by_id)

    terminal_weights = _terminal_probability_weights(weights_by_id, runs_by_id)
    p_finish = _weighted_mean(effective_finish, terminal_weights)
    p_no_touch = _weighted_mean(effective_touch, terminal_weights)
    risk_adjusted_p_finish = _weighted_mean(effective_finish, normalized)
    risk_adjusted_p_no_touch = _weighted_mean(effective_touch, normalized)
    risk_adjustment = max(0.0, p_finish - risk_adjusted_p_finish)
    u_gen_finish = _weighted_std(effective_finish, normalized, risk_adjusted_p_finish)
    u_gen_touch = _weighted_std(effective_touch, normalized, risk_adjusted_p_no_touch)
```

Add this helper below `_weights_by_id`:

```python
def _terminal_probability_weights(
    weights_by_id: dict[GeneratorId, float],
    runs_by_id: dict[GeneratorId, GeneratorRun],
) -> dict[GeneratorId, float]:
    terminal_raw = {
        generator_id: weight
        for generator_id, weight in weights_by_id.items()
        if generator_id in runs_by_id and generator_id != GeneratorId.STRESS_OVERLAY
    }
    total = sum(terminal_raw.values())
    if total <= 0:
        return {
            generator_id: weight / sum(weights_by_id.values())
            for generator_id, weight in weights_by_id.items()
        }
    return {
        generator_id: weight / total
        for generator_id, weight in terminal_raw.items()
    }
```

Update the return block:

```python
    return EnsembleProbability(
        p_finish=p_finish,
        p_no_touch=p_no_touch,
        risk_adjusted_p_finish=risk_adjusted_p_finish,
        risk_adjusted_p_no_touch=risk_adjusted_p_no_touch,
        risk_adjustment=risk_adjustment,
        u_gen_finish=u_gen_finish,
        u_gen_touch=u_gen_touch,
        u_gen=u_gen,
        mc_dispersion=mc_dispersion,
        uncertainty_buffer=base_model_buffer + 0.5 * u_gen + sparse_penalty + risk_adjustment,
        path_diagnosis=path_diagnosis,
        effective_generator_values=effective_generator_values,
    )
```

- [ ] **Step 4: Publish new diagnostics from runtime wrapper**

In `src/polymarket_engine/probability/ensemble_runtime.py`, add these diagnostics near the existing `u_gen`, `mc_dispersion`, and `uncertainty_buffer` keys:

```python
        "terminal_probability_source": "core_generators_ex_stress_overlay",
        "risk_adjusted_p_finish": float(ensemble.risk_adjusted_p_finish),
        "risk_adjusted_p_no_touch": float(ensemble.risk_adjusted_p_no_touch),
        "risk_adjustment": float(ensemble.risk_adjustment),
```

Keep the returned `ProbabilityOutput` fields as:

```python
        p_finish=float(ensemble.p_finish),
        p_no_touch=float(ensemble.p_no_touch),
```

- [ ] **Step 5: Write runtime diagnostics test**

Append this assertion block to `test_run_four_generator_ensemble_diagnostics_include_four_generators_and_seed_weights` in `tests/probability/test_ensemble_runtime.py`:

```python
    assert diagnostics["terminal_probability_source"] == "core_generators_ex_stress_overlay"
    assert diagnostics["risk_adjusted_p_finish"] == pytest.approx(output.p_finish)
    assert diagnostics["risk_adjusted_p_no_touch"] == pytest.approx(output.p_no_touch)
    assert diagnostics["risk_adjustment"] == pytest.approx(0.0)
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run pytest tests/probability/test_ensemble_outputs.py tests/probability/test_ensemble_runtime.py -q
```

Expected: pass.

- [ ] **Step 7: Commit reducer split**

```bash
git add src/polymarket_engine/probability/ensemble_outputs.py src/polymarket_engine/probability/ensemble_runtime.py tests/probability/test_ensemble_outputs.py tests/probability/test_ensemble_runtime.py
git commit -m "fix: split terminal and risk adjusted ensemble probabilities"
```

---

### Task 2: Normalize UP/DOWN terminal pairs and publish pair-coherence diagnostics

**Files:**
- Create: `src/polymarket_engine/probability/pair_coherence.py`
- Modify: `src/polymarket_engine/probability/gpu_worker.py`
- Modify: `src/polymarket_engine/probability/runtime.py`
- Test: `tests/probability/test_pair_coherence.py`
- Test: `tests/probability/test_gpu_worker.py`
- Test: `tests/probability/test_runtime.py`

- [ ] **Step 1: Write pair normalizer tests**

Create `tests/probability/test_pair_coherence.py` with:

```python
from __future__ import annotations

import pytest

from polymarket_engine.probability.pair_coherence import normalize_binary_probability_pairs


def _row(side: str, p_finish: float) -> dict[str, object]:
    return {
        "contract": f"BTC 5m {side}",
        "contract_id": f"btc-{side.lower()}",
        "market_slug": "btc-updown-5m-1780752000",
        "asset": "BTC",
        "side": side,
        "start_ts": "2026-06-07T21:40:00+00:00",
        "expiry_ts": "2026-06-07T21:45:00+00:00",
        "p_finish": p_finish,
    }


def test_normalize_binary_probability_pairs_preserves_relative_odds_and_records_gap() -> None:
    rows = normalize_binary_probability_pairs((_row("UP", 0.28), _row("DOWN", 0.62)))
    by_side = {row["side"]: row for row in rows}

    assert by_side["UP"]["p_finish"] == pytest.approx(0.28 / 0.90)
    assert by_side["DOWN"]["p_finish"] == pytest.approx(0.62 / 0.90)
    assert by_side["UP"]["pair_probability_sum_before"] == pytest.approx(0.90)
    assert by_side["DOWN"]["pair_probability_sum_before"] == pytest.approx(0.90)
    assert by_side["UP"]["pair_complement_gap"] == pytest.approx(0.10)
    assert by_side["DOWN"]["pair_complement_gap"] == pytest.approx(0.10)
    assert by_side["UP"]["pair_normalized"] is True
    assert by_side["DOWN"]["pair_normalized"] is True
    assert by_side["UP"]["counterparty_p_finish"] == pytest.approx(0.62 / 0.90)


def test_normalize_binary_probability_pairs_leaves_already_coherent_pair_unchanged() -> None:
    rows = normalize_binary_probability_pairs((_row("UP", 0.56), _row("DOWN", 0.44)))
    by_side = {row["side"]: row for row in rows}

    assert by_side["UP"]["p_finish"] == pytest.approx(0.56)
    assert by_side["DOWN"]["p_finish"] == pytest.approx(0.44)
    assert by_side["UP"]["pair_probability_sum_before"] == pytest.approx(1.0)
    assert by_side["UP"]["pair_complement_gap"] == pytest.approx(0.0)
    assert by_side["UP"]["pair_normalized"] is False


def test_normalize_binary_probability_pairs_does_not_invent_missing_side() -> None:
    rows = normalize_binary_probability_pairs((_row("UP", 0.56),))

    assert rows[0]["p_finish"] == pytest.approx(0.56)
    assert "pair_probability_sum_before" not in rows[0]
```

- [ ] **Step 2: Run pair tests and verify failure**

Run:

```bash
uv run pytest tests/probability/test_pair_coherence.py -q
```

Expected: fail because `pair_coherence.py` does not exist.

- [ ] **Step 3: Create pair normalizer implementation**

Create `src/polymarket_engine/probability/pair_coherence.py` with:

```python
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any


def normalize_binary_probability_pairs(
    rows: Sequence[Mapping[str, Any]],
    *,
    tolerance: float = 0.002,
) -> list[dict[str, Any]]:
    if tolerance < 0 or not math.isfinite(tolerance):
        raise ValueError("tolerance must be nonnegative and finite")

    normalized_rows = [dict(row) for row in rows]
    groups: dict[tuple[str, str, str, str], list[int]] = defaultdict(list)
    for index, row in enumerate(normalized_rows):
        key = _pair_key(row)
        if key is not None:
            groups[key].append(index)

    for indices in groups.values():
        up_indices = [index for index in indices if _side(normalized_rows[index]) == "UP"]
        down_indices = [index for index in indices if _side(normalized_rows[index]) == "DOWN"]
        if len(up_indices) != 1 or len(down_indices) != 1:
            continue
        up_index = up_indices[0]
        down_index = down_indices[0]
        up_p = _probability(normalized_rows[up_index].get("p_finish"))
        down_p = _probability(normalized_rows[down_index].get("p_finish"))
        if up_p is None or down_p is None:
            continue
        pair_sum = up_p + down_p
        if pair_sum <= 0 or not math.isfinite(pair_sum):
            continue
        gap = abs(1.0 - pair_sum)
        should_normalize = gap > tolerance
        normalized_up = up_p / pair_sum if should_normalize else up_p
        normalized_down = down_p / pair_sum if should_normalize else down_p
        for index, own_p, counterparty_p in (
            (up_index, normalized_up, normalized_down),
            (down_index, normalized_down, normalized_up),
        ):
            normalized_rows[index]["p_finish"] = own_p
            normalized_rows[index]["p_hat"] = own_p
            normalized_rows[index]["pair_probability_sum_before"] = pair_sum
            normalized_rows[index]["pair_complement_gap"] = gap
            normalized_rows[index]["pair_normalized"] = should_normalize
            normalized_rows[index]["counterparty_p_finish"] = counterparty_p
    return normalized_rows


def _pair_key(row: Mapping[str, Any]) -> tuple[str, str, str, str] | None:
    asset = _string(row.get("asset"))
    market = _string(row.get("market_slug"))
    start_ts = _string(row.get("start_ts"))
    expiry_ts = _string(row.get("expiry_ts"))
    if not asset or not expiry_ts:
        return None
    return (asset.upper(), market, start_ts, expiry_ts)


def _side(row: Mapping[str, Any]) -> str:
    return _string(row.get("side")).upper()


def _string(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _probability(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        return None
    return number
```

- [ ] **Step 4: Integrate pair normalizer into live worker rows**

In `src/polymarket_engine/probability/gpu_worker.py`, import the helper:

```python
from polymarket_engine.probability.pair_coherence import normalize_binary_probability_pairs
```

After each worker cycle has collected MC rows and before writing `probabilities.json` or appending events, normalize rows:

```python
    rows = normalize_binary_probability_pairs(rows)
```

If the existing local variable is named `output_rows`, use:

```python
    output_rows = normalize_binary_probability_pairs(output_rows)
```

The normalizer must run before `_event_payload_from_row(...)` receives a row, so probability events contain the same `p_finish` shown by the browser and TUI.

- [ ] **Step 5: Include pair diagnostics in event payloads**

In `_event_payload_from_row(...)` inside `src/polymarket_engine/probability/gpu_worker.py`, include these optional keys when present:

```python
    for optional_key in (
        "risk_adjusted_p_finish",
        "risk_adjusted_p_no_touch",
        "risk_adjustment",
        "terminal_probability_source",
        "pair_probability_sum_before",
        "pair_complement_gap",
        "pair_normalized",
        "counterparty_p_finish",
    ):
        if optional_key in row:
            payload[optional_key] = row[optional_key]
```

- [ ] **Step 6: Promote new diagnostics in runtime rows**

In `src/polymarket_engine/probability/runtime.py`, add these keys to `_merge_grid_diagnostics(...)`:

```python
        "terminal_probability_source",
        "risk_adjusted_p_finish",
        "risk_adjusted_p_no_touch",
        "risk_adjustment",
        "pair_probability_sum_before",
        "pair_complement_gap",
        "pair_normalized",
        "counterparty_p_finish",
```

Import the pair helper:

```python
from polymarket_engine.probability.pair_coherence import normalize_binary_probability_pairs
```

Before `build_probability_payload(...)` returns rows from hot/fallback compute, apply:

```python
    rows = normalize_binary_probability_pairs(rows)
```

For persisted rows, apply the same helper after `latest_probability_output_rows_from_connection(...)` has materialized row dictionaries and before returning them.

- [ ] **Step 7: Add live worker regression test**

In `tests/probability/test_gpu_worker.py`, update the existing fake ensemble output in the ensemble worker test so the row includes risk fields:

```python
                "risk_adjusted_p_finish": 0.56,
                "risk_adjusted_p_no_touch": 0.53,
                "risk_adjustment": 0.08,
                "terminal_probability_source": "core_generators_ex_stress_overlay",
```

Add assertions after the row is read:

```python
    assert row["risk_adjusted_p_finish"] == 0.56
    assert row["terminal_probability_source"] == "core_generators_ex_stress_overlay"
```

Add assertions after `mc_event` is read:

```python
    assert mc_event["risk_adjusted_p_finish"] == 0.56
    assert mc_event["terminal_probability_source"] == "core_generators_ex_stress_overlay"
```

- [ ] **Step 8: Add runtime promotion regression test**

Append this test to `tests/probability/test_runtime_cache.py`:

```python
def test_latest_probability_output_rows_promotes_risk_adjusted_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    probability_input = _persisted_probability_input(store)
    store.insert_probability_output(
        output_id="prob-risk-fields",
        probability_input=probability_input,
        output=ProbabilityOutput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            p_finish=0.62,
            p_no_touch=0.58,
            z_path=probability_input.z_path,
            model_version="ensemble-v1",
            seed=20260607,
            diagnostics={
                "risk_adjusted_p_finish": 0.56,
                "risk_adjusted_p_no_touch": 0.51,
                "risk_adjustment": 0.06,
                "terminal_probability_source": "core_generators_ex_stress_overlay",
            },
        ),
    )

    rows = latest_probability_output_rows(duckdb_path=db_path, limit=1)

    assert rows[0]["p_finish"] == pytest.approx(0.62)
    assert rows[0]["risk_adjusted_p_finish"] == pytest.approx(0.56)
    assert rows[0]["risk_adjustment"] == pytest.approx(0.06)
    assert rows[0]["terminal_probability_source"] == "core_generators_ex_stress_overlay"
```

- [ ] **Step 9: Run focused tests**

Run:

```bash
uv run pytest tests/probability/test_pair_coherence.py tests/probability/test_gpu_worker.py tests/probability/test_runtime.py tests/probability/test_runtime_cache.py -q
```

Expected: pass.

- [ ] **Step 10: Commit pair coherence**

```bash
git add src/polymarket_engine/probability/pair_coherence.py src/polymarket_engine/probability/gpu_worker.py src/polymarket_engine/probability/runtime.py tests/probability/test_pair_coherence.py tests/probability/test_gpu_worker.py tests/probability/test_runtime.py tests/probability/test_runtime_cache.py
git commit -m "fix: normalize binary probability pairs"
```

---

### Task 3: Make browser UI probability labels truthful and resilient

**Files:**
- Modify: `ui/src/probabilityRows.ts`
- Modify: `ui/src/App.tsx`
- Create: `ui/src/ErrorBoundary.tsx`
- Modify: `ui/src/main.tsx`
- Test: `tests/ui/probability_value_test.ts`
- Test: `tests/ui/probability_rows_test.ts`

- [ ] **Step 1: Write UI helper tests**

Append these tests to `tests/ui/probability_value_test.ts`:

```typescript
import {
  pairCoherenceLabel,
  riskAdjustedDisplayValue,
} from "../../ui/src/probabilityRows";

assert.equal(
  riskAdjustedDisplayValue({ p_finish: 0.62, risk_adjusted_p_finish: 0.56 }),
  0.56,
);
assert.equal(
  riskAdjustedDisplayValue({ p_finish: 0.62 }),
  undefined,
);
assert.equal(
  pairCoherenceLabel({ pair_probability_sum_before: 0.9, pair_normalized: true }),
  "normalized from 0.900 pair sum",
);
assert.equal(
  pairCoherenceLabel({ pair_probability_sum_before: 1.0, pair_normalized: false }),
  "pair sum 1.000",
);
```

- [ ] **Step 2: Run UI helper tests and verify failure**

Run:

```bash
node --import tsx tests/ui/probability_value_test.ts
```

Expected: fail because `riskAdjustedDisplayValue` and `pairCoherenceLabel` are not exported.

- [ ] **Step 3: Add UI row fields and helpers**

In `ui/src/probabilityRows.ts`, add fields to `ProbabilityValueRow`:

```typescript
  risk_adjusted_p_finish?: number;
  risk_adjusted_p_no_touch?: number;
  risk_adjustment?: number;
  terminal_probability_source?: string;
  pair_probability_sum_before?: number;
  pair_complement_gap?: number;
  pair_normalized?: boolean;
  counterparty_p_finish?: number;
```

Add exported helpers below `probabilityDisplayValue`:

```typescript
export function riskAdjustedDisplayValue(row?: ProbabilityValueRow | null) {
  if (!row) {
    return undefined;
  }
  return isFiniteNumber(row.risk_adjusted_p_finish) ? row.risk_adjusted_p_finish : undefined;
}

export function pairCoherenceLabel(row?: ProbabilityValueRow | null) {
  if (!row || !isFiniteNumber(row.pair_probability_sum_before)) {
    return undefined;
  }
  const sum = row.pair_probability_sum_before.toFixed(3);
  return row.pair_normalized === true ? `normalized from ${sum} pair sum` : `pair sum ${sum}`;
}
```

- [ ] **Step 4: Update browser metric labels**

In `ui/src/App.tsx`, import the helpers from `./probabilityRows`:

```typescript
  pairCoherenceLabel,
  riskAdjustedDisplayValue,
```

In `SelectedDetails`, change the primary metric label from `Monte Carlo` to `Terminal probability` and add risk/pair metrics:

```tsx
        <Metric label="Terminal probability" value={formatProbability(probabilityDisplayValue(row))} />
        <Metric label="Risk adjusted" value={formatProbability(riskAdjustedDisplayValue(row))} />
        <Metric label="Pair coherence" value={pairCoherenceLabel(row) ?? "-"} />
```

In pair cards or comparison panels, keep the side button primary value as `probabilityDisplayValue(row)` and show the risk-adjusted value as a secondary line:

```tsx
<span className="pair-risk-adjusted">
  risk {formatProbability(riskAdjustedDisplayValue(row))}
</span>
```

If the target component uses a different class name, keep the exact text `risk` and the helper `riskAdjustedDisplayValue(row)`.

- [ ] **Step 5: Add browser error boundary**

Create `ui/src/ErrorBoundary.tsx` with:

```tsx
import { Component, type ErrorInfo, type ReactNode } from "react";

type ErrorBoundaryProps = {
  children: ReactNode;
};

type ErrorBoundaryState = {
  error: Error | null;
};

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Runtime UI crashed", error, info.componentStack);
  }

  render() {
    if (!this.state.error) {
      return this.props.children;
    }
    return (
      <main className="app-shell app-shell-error">
        <section className="panel detail-panel">
          <p className="panel-kicker">Browser UI error</p>
          <h1>Runtime UI crashed</h1>
          <p>{this.state.error.message}</p>
          <button type="button" onClick={() => window.location.reload()}>
            Reload UI
          </button>
        </section>
      </main>
    );
  }
}
```

Modify `ui/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { ErrorBoundary } from "./ErrorBoundary";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
```

- [ ] **Step 6: Run UI tests**

Run:

```bash
node --import tsx tests/ui/probability_value_test.ts
node --import tsx tests/ui/probability_rows_test.ts
```

Expected: pass.

- [ ] **Step 7: Commit browser UI updates**

```bash
git add ui/src/probabilityRows.ts ui/src/App.tsx ui/src/ErrorBoundary.tsx ui/src/main.tsx tests/ui/probability_value_test.ts tests/ui/probability_rows_test.ts
git commit -m "fix: label terminal and risk adjusted probabilities in browser UI"
```

---

### Task 4: Update TUI probability schema and rendering

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/status.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`

- [ ] **Step 1: Write TUI rendering expectations**

In the test module inside `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`, update `RuntimeProbabilityRow` fixtures by adding:

```rust
                    risk_adjusted_p_finish: Some(0.5249),
                    risk_adjustment: Some(0.0500),
                    pair_probability_sum_before: Some(0.9000),
                    pair_complement_gap: Some(0.1000),
                    pair_normalized: Some(true),
```

Update the header assertion in `probability_rows_render_read_only_probability_outputs`:

```rust
        assert_eq!(
            probability_header_labels(),
            [
                "Contract",
                "Terminal",
                "RiskAdj",
                "Edge",
                "Req",
                "Hint/Reasons"
            ]
        );
```

Update the row assertions:

```rust
        assert_eq!(rows[0].p_finish, "0.575");
        assert_eq!(rows[0].risk_adjusted, "0.525");
```

- [ ] **Step 2: Run TUI probability tests and verify failure**

Run:

```bash
cargo test -p polymarket-cockpit-tui render::probability --quiet
```

Expected: fail because `RuntimeProbabilityRow` and `ProbabilityDisplayRow` do not include risk-adjusted fields.

- [ ] **Step 3: Add new Rust status fields**

In `rust/crates/polymarket-cockpit-tui/src/status.rs`, add these optional fields to `RuntimeProbabilityRow`:

```rust
    #[serde(default)]
    pub risk_adjusted_p_finish: Option<f64>,
    #[serde(default)]
    pub risk_adjusted_p_no_touch: Option<f64>,
    #[serde(default)]
    pub risk_adjustment: Option<f64>,
    #[serde(default)]
    pub pair_probability_sum_before: Option<f64>,
    #[serde(default)]
    pub pair_complement_gap: Option<f64>,
    #[serde(default)]
    pub pair_normalized: Option<bool>,
```

- [ ] **Step 4: Update TUI rendering model**

In `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`, update `ProbabilityDisplayRow`:

```rust
pub struct ProbabilityDisplayRow {
    pub contract: String,
    pub p_finish: String,
    pub risk_adjusted: String,
    pub edge: String,
    pub required_edge: String,
    pub hint_reasons: String,
}
```

Update `probability_header_labels()`:

```rust
pub fn probability_header_labels() -> [&'static str; 6] {
    [
        "Contract",
        "Terminal",
        "RiskAdj",
        "Edge",
        "Req",
        "Hint/Reasons",
    ]
}
```

Update `probability_table(...)` row mapping:

```rust
                    row.contract,
                    row.p_finish,
                    row.risk_adjusted,
                    row.edge,
                    row.required_edge,
                    row.hint_reasons,
```

Update `probability_row(...)`:

```rust
fn probability_row(row: &RuntimeProbabilityRow) -> ProbabilityDisplayRow {
    ProbabilityDisplayRow {
        contract: row.contract.clone(),
        p_finish: format_probability(row.p_finish),
        risk_adjusted: format_optional_probability(row.risk_adjusted_p_finish),
        edge: format_optional_probability(row.edge_after_costs),
        required_edge: format_optional_probability(row.required_edge),
        hint_reasons: hint_reasons(row),
    }
}
```

Update compact table headers and values:

```rust
headers: vec!["Contract", "Terminal", "RiskAdj", "Paths", "Model"],
```

```rust
                        format_probability(row.p_finish),
                        format_optional_probability(row.risk_adjusted_p_finish),
```

- [ ] **Step 5: Run TUI focused tests**

Run:

```bash
cargo test -p polymarket-cockpit-tui render::probability --quiet
```

Expected: pass.

- [ ] **Step 6: Commit TUI updates**

```bash
git add rust/crates/polymarket-cockpit-tui/src/status.rs rust/crates/polymarket-cockpit-tui/src/render/probability.rs
git commit -m "fix: show terminal and risk adjusted probabilities in tui"
```

---

### Task 5: Make next-contract probabilities visible immediately through NOWCAST

**Files:**
- Modify: `src/polymarket_engine/probability/gpu_worker.py`
- Modify: `ui/src/probabilityRows.ts`
- Test: `tests/probability/test_gpu_worker.py`
- Test: `tests/ui/probability_rows_test.ts`

- [ ] **Step 1: Write worker acceptance test for new contract NOWCAST**

Append a test to `tests/probability/test_gpu_worker.py` that creates two hot input rows for the same asset, one current and one next, and monkeypatches `run_four_generator_ensemble` to raise after NOWCAST rows are materialized:

```python
def test_worker_publishes_nowcast_rows_for_new_contracts_before_mc_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asof_ts = datetime.now(UTC)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_event_path = tmp_path / "probability-events.jsonl"

    def input_row(state_id: str, side: str, start_offset_minutes: int) -> dict[str, object]:
        probability_input = ProbabilityInput(
            state_id=state_id,
            asof_ts=asof_ts,
            asset="BTC",
            side=side,
            comparison_operator=">=" if side == "UP" else "<",
            seconds_left=300.0 + start_offset_minutes * 60.0,
            settlement_price=70_100.0,
            threshold=70_000.0,
            sigma_tau=0.012,
            executable_price=0.52 if side == "UP" else 0.48,
            source_age_ms=100,
            book_age_ms=100,
            z_path=0.12,
        )
        return {
            "contract": f"BTC 5m {side}",
            "contract_id": f"btc-{start_offset_minutes}-{side.lower()}",
            "market_slug": f"btc-updown-5m-{start_offset_minutes}",
            "start_ts": (asof_ts + timedelta(minutes=start_offset_minutes)).isoformat(),
            "expiry_ts": (asof_ts + timedelta(minutes=start_offset_minutes + 5)).isoformat(),
            "flags": ["OK"],
            "probability_input": probability_input.to_json_dict(),
            "volatility_regime": "normal",
        }

    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": asof_ts.isoformat(),
                "rows": [
                    input_row("state-current-up", "UP", 0),
                    input_row("state-current-down", "DOWN", 0),
                    input_row("state-next-up", "UP", 5),
                    input_row("state-next-down", "DOWN", 5),
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    def fail_mc(*_: object, **__: object) -> ProbabilityOutput:
        raise RuntimeError("mc intentionally unavailable")

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fail_mc,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_event_path=probability_event_path,
        budget=ProbabilityWorkerBudget(max_total_paths=80_000),
    )

    assert payload["state"] == "NOWCAST"
    assert len(payload["rows"]) == 4
    assert {row["probability_kind"] for row in payload["rows"]} == {"NOWCAST"}
    assert {row["market_slug"] for row in payload["rows"]} == {
        "btc-updown-5m-0",
        "btc-updown-5m-5",
    }
```

- [ ] **Step 2: Run worker NOWCAST test and verify failure**

Run:

```bash
uv run pytest tests/probability/test_gpu_worker.py::test_worker_publishes_nowcast_rows_for_new_contracts_before_mc_finishes -q
```

Expected: fail if the worker drops rows after MC failure or does not publish next-window rows.

- [ ] **Step 3: Implement NOWCAST-first publication**

In `src/polymarket_engine/probability/gpu_worker.py`, ensure cycle logic constructs nowcast rows from every hot input before MC work starts:

```python
    nowcast_rows = [
        _nowcast_row_from_runtime_input(runtime_input, generated_at=cycle_started_at)
        for runtime_input in runtime_inputs
    ]
```

When MC rows are unavailable for a runtime input, keep its NOWCAST row in the published `rows`. When MC rows exist for a contract, prefer MC over NOWCAST for that same `contract_id` and `side`:

```python
    rows_by_contract = {
        str(row.get("contract_id")): row
        for row in nowcast_rows
        if row.get("contract_id")
    }
    for row in mc_rows:
        contract_id = str(row.get("contract_id") or "")
        if contract_id:
            rows_by_contract[contract_id] = row
    rows = normalize_binary_probability_pairs(tuple(rows_by_contract.values()))
```

Set state to `NOWCAST` when any published row is a NOWCAST row and MC rows are incomplete:

```python
    state = "OK" if len(mc_rows) == len(runtime_inputs) else "NOWCAST"
```

- [ ] **Step 4: Add UI rollover helper test**

Append to `tests/ui/probability_rows_test.ts`:

```typescript
import { visibleProbabilityDiagnosticRows } from "../../ui/src/probabilityRows";

const nowMs = Date.parse("2026-06-07T21:45:00Z");
const rows = visibleProbabilityDiagnosticRows(
  [
    {
      asset: "BTC",
      side: "UP",
      market_slug: "btc-current",
      expiry_ts: "2026-06-07T21:44:59Z",
      p_finish: 0.99,
    },
    {
      asset: "BTC",
      side: "UP",
      market_slug: "btc-next",
      expiry_ts: "2026-06-07T21:50:00Z",
      probability_kind: "NOWCAST",
      p_finish: 0.51,
    },
  ],
  nowMs,
);

assert.equal(rows.length, 1);
assert.equal(rows[0].market_slug, "btc-next");
```

- [ ] **Step 5: Run rollover tests**

Run:

```bash
uv run pytest tests/probability/test_gpu_worker.py::test_worker_publishes_nowcast_rows_for_new_contracts_before_mc_finishes -q
node --import tsx tests/ui/probability_rows_test.ts
```

Expected: pass.

- [ ] **Step 6: Commit rollover NOWCAST fix**

```bash
git add src/polymarket_engine/probability/gpu_worker.py ui/src/probabilityRows.ts tests/probability/test_gpu_worker.py tests/ui/probability_rows_test.ts
git commit -m "fix: publish next contract nowcast rows immediately"
```

---

### Task 6: Add replay-safe calibration metrics for market `.99` versus model confidence gaps

**Files:**
- Modify: `src/polymarket_engine/research/generator_validation.py`
- Test: `tests/research/test_generator_validation.py`

- [ ] **Step 1: Write calibration metric tests**

Append to `tests/research/test_generator_validation.py`:

```python
from polymarket_engine.research.generator_validation import (
    CalibrationBucket,
    ProbabilityCalibrationRow,
    build_calibration_buckets,
)


def test_build_calibration_buckets_reports_underconfidence_near_certain_market() -> None:
    rows = build_calibration_buckets(
        (
            ProbabilityCalibrationRow(
                state_id="state-down-1",
                asof_ts=datetime(2026, 6, 7, 21, 44, tzinfo=UTC),
                side="DOWN",
                model_probability=0.889,
                market_probability=0.99,
                did_finish_win=True,
                seconds_left=8.0,
            ),
            ProbabilityCalibrationRow(
                state_id="state-down-2",
                asof_ts=datetime(2026, 6, 7, 21, 49, tzinfo=UTC),
                side="DOWN",
                model_probability=0.91,
                market_probability=0.98,
                did_finish_win=True,
                seconds_left=12.0,
            ),
        ),
        bucket_count=10,
    )

    assert rows == (
        CalibrationBucket(
            lower=0.8,
            upper=0.9,
            count=1,
            win_rate=1.0,
            mean_model_probability=0.889,
            mean_market_probability=0.99,
            mean_market_model_gap=0.101,
            brier=pytest.approx((1.0 - 0.889) ** 2),
        ),
        CalibrationBucket(
            lower=0.9,
            upper=1.0,
            count=1,
            win_rate=1.0,
            mean_model_probability=0.91,
            mean_market_probability=0.98,
            mean_market_model_gap=0.07,
            brier=pytest.approx((1.0 - 0.91) ** 2),
        ),
    )
```

- [ ] **Step 2: Run calibration tests and verify failure**

Run:

```bash
uv run pytest tests/research/test_generator_validation.py -q
```

Expected: fail because calibration row and bucket types do not exist.

- [ ] **Step 3: Implement calibration dataclasses and bucket builder**

Append to `src/polymarket_engine/research/generator_validation.py`:

```python
@dataclass(frozen=True)
class ProbabilityCalibrationRow:
    state_id: str
    asof_ts: datetime
    side: str
    model_probability: float
    market_probability: float
    did_finish_win: bool
    seconds_left: float

    def __post_init__(self) -> None:
        _require_nonempty_string(self.state_id, "state_id")
        _require_utc(self.asof_ts, "asof_ts")
        if self.side not in {"UP", "DOWN"}:
            raise ValueError("side must be UP or DOWN")
        _require_probability(self.model_probability, "model_probability")
        _require_probability(self.market_probability, "market_probability")
        if not isinstance(self.did_finish_win, bool):
            raise ValueError("did_finish_win must be a bool")
        _require_nonnegative_finite(self.seconds_left, "seconds_left")


@dataclass(frozen=True)
class CalibrationBucket:
    lower: float
    upper: float
    count: int
    win_rate: float
    mean_model_probability: float
    mean_market_probability: float
    mean_market_model_gap: float
    brier: float
```

Add this function:

```python
def build_calibration_buckets(
    rows: tuple[ProbabilityCalibrationRow, ...],
    *,
    bucket_count: int,
) -> tuple[CalibrationBucket, ...]:
    _require_positive_int(bucket_count, "bucket_count")
    buckets: list[list[ProbabilityCalibrationRow]] = [[] for _ in range(bucket_count)]
    for row in rows:
        index = min(bucket_count - 1, int(row.model_probability * bucket_count))
        buckets[index].append(row)

    output: list[CalibrationBucket] = []
    for index, bucket_rows in enumerate(buckets):
        if not bucket_rows:
            continue
        lower = index / bucket_count
        upper = (index + 1) / bucket_count
        count = len(bucket_rows)
        win_rate = sum(1 for row in bucket_rows if row.did_finish_win) / count
        mean_model = sum(row.model_probability for row in bucket_rows) / count
        mean_market = sum(row.market_probability for row in bucket_rows) / count
        brier = sum(
            (float(row.did_finish_win) - row.model_probability) ** 2
            for row in bucket_rows
        ) / count
        output.append(
            CalibrationBucket(
                lower=lower,
                upper=upper,
                count=count,
                win_rate=win_rate,
                mean_model_probability=mean_model,
                mean_market_probability=mean_market,
                mean_market_model_gap=mean_market - mean_model,
                brier=brier,
            )
        )
    return tuple(output)
```

Add this helper near the other validation helpers:

```python
def _require_nonnegative_finite(value: float, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0.0
    ):
        raise ValueError(f"{field_name} must be nonnegative and finite")
```

- [ ] **Step 4: Run calibration tests**

Run:

```bash
uv run pytest tests/research/test_generator_validation.py -q
```

Expected: pass.

- [ ] **Step 5: Commit calibration metrics**

```bash
git add src/polymarket_engine/research/generator_validation.py tests/research/test_generator_validation.py
git commit -m "feat: add replay safe probability calibration metrics"
```

---

### Task 7: Document probability semantics and run focused verification

**Files:**
- Modify: `docs/probability-generator-weights.md`
- Modify: `docs/PART_TWO_LIVE_COLLECTORS.md`
- Test: `tests/docs/test_active_runtime_docs.py`

- [ ] **Step 1: Write docs tests**

Add assertions to `tests/docs/test_active_runtime_docs.py`:

```python
def test_probability_docs_separate_terminal_and_risk_adjusted_probability() -> None:
    text = (ROOT / "docs" / "probability-generator-weights.md").read_text(encoding="utf-8")

    assert "`p_finish` is the terminal fair-value probability" in text
    assert "`risk_adjusted_p_finish` is a stress-haircuted score" in text
    assert "UP and DOWN terminal probabilities should sum to approximately 1.0" in text


def test_live_collector_docs_require_nowcast_on_contract_rollover() -> None:
    text = (ROOT / "docs" / "PART_TWO_LIVE_COLLECTORS.md").read_text(encoding="utf-8")

    assert "new current and next contracts publish NOWCAST rows before Monte Carlo finishes" in text
```

- [ ] **Step 2: Run docs tests and verify failure**

Run:

```bash
uv run pytest tests/docs/test_active_runtime_docs.py -q
```

Expected: fail because the new wording is not present.

- [ ] **Step 3: Update probability documentation**

Add this section to `docs/probability-generator-weights.md`:

```markdown
## Terminal probability versus risk-adjusted score

`p_finish` is the terminal fair-value probability for the binary payoff. For a matched UP/DOWN market window, UP and DOWN terminal probabilities should sum to approximately 1.0 after pair normalization. Small gaps can come from rounding or missing counterpart rows; a large gap is a runtime diagnostic, not a third outcome.

`risk_adjusted_p_finish` is a stress-haircuted score. It can be below `p_finish` because stress overlays are adversarial path-risk diagnostics. It is not allowed to remove probability mass from both UP and DOWN while still being labeled as the primary probability.

`p_no_touch` remains a path-survival metric for risk gates. It is not the payout probability and is not expected to complement across UP and DOWN.
```

Add this paragraph to `docs/PART_TWO_LIVE_COLLECTORS.md` near the live probability status description:

```markdown
On contract rollover, new current and next contracts publish NOWCAST rows before Monte Carlo finishes. The browser UI and TUI may replace NOWCAST rows with MC rows as they arrive, but they must not keep showing an expired contract as the primary current row when fresh hot inputs for the next market window are available.
```

- [ ] **Step 4: Run full focused verification**

Run:

```bash
uv run pytest tests/probability/test_ensemble_outputs.py tests/probability/test_ensemble_runtime.py tests/probability/test_pair_coherence.py tests/probability/test_gpu_worker.py tests/probability/test_runtime.py tests/probability/test_runtime_cache.py tests/research/test_generator_validation.py tests/docs/test_active_runtime_docs.py -q
node --import tsx tests/ui/probability_value_test.ts
node --import tsx tests/ui/probability_rows_test.ts
cargo test -p polymarket-cockpit-tui render::probability --quiet
```

Expected: all commands pass.

- [ ] **Step 5: Commit docs and verification updates**

```bash
git add docs/probability-generator-weights.md docs/PART_TWO_LIVE_COLLECTORS.md tests/docs/test_active_runtime_docs.py
git commit -m "docs: clarify probability semantics and rollover visibility"
```

---

## Deployment and Runtime Check Plan

- [ ] **Step 1: Build and deploy through the existing THEPC path**

Use the existing deploy flow for this repo. Do not add live trading hooks. The deployment must restart the API and `gpu-probability-worker` so both browser and TUI consume the same probability fields.

- [ ] **Step 2: Check live API health**

Run from the Mac:

```bash
curl -sS http://100.72.104.49:8000/health
curl -sS 'http://100.72.104.49:8000/api/runtime/probabilities?limit=8' | python3 -m json.tool | sed -n '1,220p'
```

Expected:

```text
health status is ok
probability rows include p_finish, risk_adjusted_p_finish, pair_probability_sum_before, pair_complement_gap
matched UP/DOWN rows have p_finish sums near 1.0
```

- [ ] **Step 3: Check browser UI shell**

Open:

```text
http://100.72.104.49:8000/?v=coherent-probability
```

Expected:

```text
Browser app loads.
Selected details show Terminal probability, Risk adjusted, Pair coherence.
Current UP/DOWN cards show visible simulation previews when MC rows exist.
If React crashes, a Browser UI error panel appears instead of a blank page.
```

- [ ] **Step 4: Check TUI**

Run the existing TUI command from the runtime host.

Expected:

```text
Probability table headers include Terminal and RiskAdj.
Terminal UP/DOWN values for the same market window add to approximately 1.000.
RiskAdj can be lower and does not need to complement.
```

---

## Self-Review

**Spec coverage:** This plan covers the observed UP/DOWN sum bug, the missing distinction between terminal and stress-adjusted probability, browser UI labeling and crash visibility, TUI labeling, rollover NOWCAST visibility, and replay calibration for market/model confidence gaps.

**Placeholder scan:** The plan contains concrete file paths, test names, code snippets, commands, and expected outcomes. No deferred sections are left for an implementer to infer.

**Type consistency:** Python uses `risk_adjusted_p_finish`, `risk_adjusted_p_no_touch`, `risk_adjustment`, `pair_probability_sum_before`, `pair_complement_gap`, and `pair_normalized`. TypeScript and Rust use the same JSON field names so the API, browser, and TUI agree.
