# CUDA Confidence, Sensitivity, and Probability Values Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make CUDA Monte Carlo produce clearer contract-truth probabilities, confidence bands, and prior-derived sensitivity data, then show those values and UP/DOWN selection correctly in the runtime UI.

**Architecture:** Keep one canonical per-contract probability: `p_hat`, meaning "estimated probability this contract finishes true." Preserve `p_finish` as the backward-compatible storage/API alias for `p_hat`; treat `p_no_touch` as a diagnostic risk metric, not the main UI probability. Extend the CUDA worker to run multi-seed batches and derive sensitivity from the simulated prior distribution, not hard-coded dollar moves.

**Tech Stack:** Python 3.14, CuPy/CUDA, DuckDB status/grid cache, FastAPI runtime API, React/Vite runtime monitor, pytest, ruff, esbuild-backed UI helper tests.

---

## File Structure

- Modify `src/polymarket_engine/probability/cuda_monte_carlo.py`
  - Add multi-seed CUDA aggregation.
  - Add prior-distribution sensitivity generation from simulated paths.
  - Keep the existing single-seed `run_cuda_monte_carlo()` for compatibility and tests.
- Modify `src/polymarket_engine/probability/path_policy.py`
  - Add seed-count policy and clarify total path count versus paths per seed.
- Modify `src/polymarket_engine/probability/gpu_worker.py`
  - Call the multi-seed CUDA function.
  - Write `p_hat`, confidence-band fields, seed metadata, total path count, and prior sensitivity diagnostics.
- Modify `src/polymarket_engine/probability/runtime.py`
  - Extract new diagnostics into runtime rows.
- Modify `src/polymarket_engine/probability/grid_cache.py`
  - Keep storing `p_finish` as the canonical `p_hat` value.
  - Surface the new diagnostic fields through runtime rows.
- Modify `ui/src/App.tsx`
  - Display `p_hat` instead of raw `p_finish` labels.
  - Make UP/DOWN selection visibly switch the selected graph/card.
  - Show total paths, preview paths, seed count, and paths per seed.
- Modify `ui/src/probabilityRows.ts`
  - Add helper functions for canonical probability value and row metadata used by tests.
- Create `tests/ui/probability_value_test.ts`
  - Exercise UI helper behavior for `p_hat`, fallback `p_finish`, path-count metadata, and row keys.
- Modify `tests/ui/test_probability_rows_helper.py`
  - Bundle/run the new TypeScript helper test.
- Modify `tests/probability/test_cuda_monte_carlo.py`
  - Add fake-CuPy or monkeypatched CUDA tests for multi-seed aggregation and sensitivity payload shape.
- Modify `tests/probability/test_gpu_worker.py`
  - Verify rows include `p_hat`, confidence bands, seed metadata, and prior sensitivity diagnostics.
- Modify `tests/probability/test_path_policy.py`
  - Verify seed-count and total-path policies.
- Modify `docs/SPOON_DEPLOYMENT.md`
  - Document why the UI does not draw 30,000+ individual lines: the chart intentionally samples preview paths while the numeric row shows total simulated paths.

## Probability Semantics

The API and UI must use these meanings consistently:

- `p_hat`: canonical estimated probability that the displayed contract finishes true.
- `p_finish`: backward-compatible alias for `p_hat`, retained for storage/API compatibility.
- `p_no_touch`: diagnostic path-reversal/risk metric, not the primary operator probability.
- `p_hat_std`: standard deviation of `p_hat` across seeds.
- `p_hat_ci_low` / `p_hat_ci_high`: approximate 95% seed confidence band.
- `path_count`: total simulated paths across all seeds.
- `paths_per_seed`: simulated paths per seed.
- `seed_count`: number of CUDA seeds used.
- `preview_path_count`: number of sampled paths drawn in the browser chart.

The UI should show "Total paths 30,000" while the graph draws a bounded sampled subset such as "24 preview paths." Drawing 30,000 canvas lines every poll is not the goal; it would be visually unreadable and slower.

## Sensitivity Semantics

The sensitivity grid must be prior-derived:

- Do not use hard-coded price moves like `$5`, `$10`, or `$25`.
- Use simulated prior path distribution quantiles and path checkpoints.
- Price sensitivity rows should answer: "Among paths whose prior price at this time checkpoint falls in this quantile band, what fraction finish true?"
- Volatility sensitivity rows may use prior-width scale factors such as `0.75`, `1.0`, and `1.25`, because those perturb the distribution width, not a fixed price move.
- Every sensitivity row must include enough metadata to audit where it came from: `dimension`, `time_fraction`, `quantile_low`, `quantile_high`, `sample_count`, `p_hat`, and `price_quantile`.

---

### Task 1: Path and Seed Policy

**Files:**
- Modify: `src/polymarket_engine/probability/path_policy.py`
- Test: `tests/probability/test_path_policy.py`

- [ ] **Step 1: Write the failing seed-policy tests**

Add these tests to `tests/probability/test_path_policy.py`:

```python
from polymarket_engine.probability.path_policy import runtime_paths_per_seed_for_seconds_left
from polymarket_engine.probability.path_policy import runtime_seed_count_for_seconds_left
from polymarket_engine.probability.path_policy import runtime_total_path_count_for_seconds_left


@pytest.mark.parametrize(
    ("seconds_left", "expected_paths_per_seed", "expected_seed_count", "expected_total"),
    (
        (1_200.0, 10_000, 3, 30_000),
        (600.0, 10_000, 3, 30_000),
        (300.0, 20_000, 4, 80_000),
        (120.0, 30_000, 4, 120_000),
        (30.0, 50_000, 5, 250_000),
    ),
)
def test_runtime_seed_policy_reports_total_paths(
    seconds_left: float,
    expected_paths_per_seed: int,
    expected_seed_count: int,
    expected_total: int,
) -> None:
    assert runtime_paths_per_seed_for_seconds_left(seconds_left) == expected_paths_per_seed
    assert runtime_seed_count_for_seconds_left(seconds_left) == expected_seed_count
    assert runtime_total_path_count_for_seconds_left(seconds_left) == expected_total
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest tests/probability/test_path_policy.py::test_runtime_seed_policy_reports_total_paths -q
```

Expected: FAIL with import errors for the new policy functions.

- [ ] **Step 3: Implement the seed policy**

Replace `src/polymarket_engine/probability/path_policy.py` with:

```python
from __future__ import annotations

import math


def runtime_paths_per_seed_for_seconds_left(seconds_left: float) -> int:
    value = _finite_seconds_left(seconds_left)
    if value <= 30:
        return 50_000
    if value <= 120:
        return 30_000
    if value <= 300:
        return 20_000
    return 10_000


def runtime_seed_count_for_seconds_left(seconds_left: float) -> int:
    value = _finite_seconds_left(seconds_left)
    if value <= 30:
        return 5
    if value <= 300:
        return 4
    return 3


def runtime_total_path_count_for_seconds_left(seconds_left: float) -> int:
    return (
        runtime_paths_per_seed_for_seconds_left(seconds_left)
        * runtime_seed_count_for_seconds_left(seconds_left)
    )


def runtime_path_count_for_seconds_left(seconds_left: float) -> int:
    return runtime_total_path_count_for_seconds_left(seconds_left)


def _finite_seconds_left(seconds_left: float) -> float:
    if isinstance(seconds_left, bool):
        raise ValueError("seconds_left must be finite")
    value = float(seconds_left)
    if not math.isfinite(value):
        raise ValueError("seconds_left must be finite")
    return value
```

- [ ] **Step 4: Run path-policy tests**

Run:

```bash
uv run pytest tests/probability/test_path_policy.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/probability/path_policy.py tests/probability/test_path_policy.py
git commit -m "feat: add cuda seed path policy"
```

---

### Task 2: CUDA Multi-Seed Output Contract

**Files:**
- Modify: `src/polymarket_engine/probability/cuda_monte_carlo.py`
- Test: `tests/probability/test_cuda_monte_carlo.py`

- [ ] **Step 1: Write the failing test for multi-seed aggregation**

Add this test to `tests/probability/test_cuda_monte_carlo.py`:

```python
def test_cuda_multi_seed_aggregates_p_hat_and_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("polymarket_engine.probability.cuda_monte_carlo")
    probability_input = _probability_input()
    outputs = iter(
        (
            module.ProbabilityOutput(
                state_id=probability_input.state_id,
                asof_ts=probability_input.asof_ts,
                p_finish=0.40,
                p_no_touch=0.70,
                z_path=probability_input.z_path,
                model_version="cuda-lognormal-chainlink-sigma-v1",
                seed=11,
                diagnostics={
                    "path_count": 10_000,
                    "steps": 120,
                    "model": "cuda_lognormal_chainlink_sigma",
                    "simulation_preview": {"path_count": 10_000, "sampled_paths": []},
                    "prior_sensitivity": [],
                },
            ),
            module.ProbabilityOutput(
                state_id=probability_input.state_id,
                asof_ts=probability_input.asof_ts,
                p_finish=0.50,
                p_no_touch=0.80,
                z_path=probability_input.z_path,
                model_version="cuda-lognormal-chainlink-sigma-v1",
                seed=22,
                diagnostics={
                    "path_count": 10_000,
                    "steps": 120,
                    "model": "cuda_lognormal_chainlink_sigma",
                    "simulation_preview": {"path_count": 10_000, "sampled_paths": []},
                    "prior_sensitivity": [],
                },
            ),
            module.ProbabilityOutput(
                state_id=probability_input.state_id,
                asof_ts=probability_input.asof_ts,
                p_finish=0.60,
                p_no_touch=0.90,
                z_path=probability_input.z_path,
                model_version="cuda-lognormal-chainlink-sigma-v1",
                seed=33,
                diagnostics={
                    "path_count": 10_000,
                    "steps": 120,
                    "model": "cuda_lognormal_chainlink_sigma",
                    "simulation_preview": {"path_count": 10_000, "sampled_paths": []},
                    "prior_sensitivity": [],
                },
            ),
        )
    )

    def fake_single_seed(*_: object, **__: object) -> module.ProbabilityOutput:
        return next(outputs)

    monkeypatch.setattr(module, "run_cuda_monte_carlo", fake_single_seed)

    result = module.run_cuda_monte_carlo_multi_seed(
        probability_input,
        paths_per_seed=10_000,
        steps=120,
        seed=11,
        seed_count=3,
    )

    assert result.p_finish == pytest.approx(0.50)
    assert result.p_no_touch == pytest.approx(0.80)
    assert result.diagnostics["p_hat"] == pytest.approx(0.50)
    assert result.diagnostics["p_hat_std"] == pytest.approx(0.10)
    assert result.diagnostics["p_hat_ci_low"] == pytest.approx(0.3868, rel=1e-3)
    assert result.diagnostics["p_hat_ci_high"] == pytest.approx(0.6132, rel=1e-3)
    assert result.diagnostics["seed_count"] == 3
    assert result.diagnostics["paths_per_seed"] == 10_000
    assert result.diagnostics["path_count"] == 30_000
    assert [row["seed"] for row in result.diagnostics["seed_runs"]] == [11, 22, 33]
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest tests/probability/test_cuda_monte_carlo.py::test_cuda_multi_seed_aggregates_p_hat_and_confidence -q
```

Expected: FAIL with `AttributeError: module ... has no attribute 'run_cuda_monte_carlo_multi_seed'`.

- [ ] **Step 3: Implement multi-seed aggregation**

Add these imports near the top of `src/polymarket_engine/probability/cuda_monte_carlo.py`:

```python
import statistics
```

Add this function below `run_cuda_monte_carlo()`:

```python
def run_cuda_monte_carlo_multi_seed(
    probability_input: ProbabilityInput,
    *,
    paths_per_seed: int,
    steps: int,
    seed: int,
    seed_count: int,
) -> ProbabilityOutput:
    _require_positive_int(paths_per_seed, "paths_per_seed")
    _require_positive_int(seed_count, "seed_count")
    outputs = [
        run_cuda_monte_carlo(
            probability_input,
            path_count=paths_per_seed,
            steps=steps,
            seed=run_seed,
        )
        for run_seed in _seed_sequence(seed, seed_count)
    ]
    p_finish_values = [output.p_finish for output in outputs]
    p_no_touch_values = [output.p_no_touch for output in outputs]
    p_hat = statistics.fmean(p_finish_values)
    p_no_touch = statistics.fmean(p_no_touch_values)
    p_hat_std = statistics.stdev(p_finish_values) if len(p_finish_values) > 1 else 0.0
    standard_error = p_hat_std / math.sqrt(len(p_finish_values)) if p_finish_values else 0.0
    ci_half_width = 1.96 * standard_error
    total_path_count = paths_per_seed * seed_count
    first_diagnostics = dict(outputs[0].diagnostics)
    diagnostics = {
        "path_count": total_path_count,
        "paths_per_seed": paths_per_seed,
        "seed_count": seed_count,
        "steps": steps,
        "model": "cuda_lognormal_chainlink_sigma_multi_seed",
        "p_hat": p_hat,
        "p_hat_std": p_hat_std,
        "p_hat_ci_low": max(0.0, p_hat - ci_half_width),
        "p_hat_ci_high": min(1.0, p_hat + ci_half_width),
        "p_no_touch_mean": p_no_touch,
        "seed_runs": [
            {
                "seed": output.seed,
                "p_hat": output.p_finish,
                "p_no_touch": output.p_no_touch,
                "path_count": int(output.diagnostics["path_count"]),
            }
            for output in outputs
        ],
        "simulation_preview": first_diagnostics.get("simulation_preview"),
        "prior_sensitivity": first_diagnostics.get("prior_sensitivity", []),
    }
    return ProbabilityOutput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        p_finish=p_hat,
        p_no_touch=p_no_touch,
        z_path=probability_input.z_path,
        model_version="cuda-lognormal-chainlink-sigma-multiseed-v1",
        seed=seed,
        diagnostics=diagnostics,
    )
```

Add this helper below `_load_cupy()`:

```python
def _seed_sequence(seed: int, seed_count: int) -> tuple[int, ...]:
    _require_positive_int(seed_count, "seed_count")
    return tuple(seed + index * 11 for index in range(seed_count))
```

- [ ] **Step 4: Run the multi-seed test**

Run:

```bash
uv run pytest tests/probability/test_cuda_monte_carlo.py::test_cuda_multi_seed_aggregates_p_hat_and_confidence -q
```

Expected: PASS.

- [ ] **Step 5: Run existing CUDA tests**

Run:

```bash
uv run pytest tests/probability/test_cuda_monte_carlo.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/polymarket_engine/probability/cuda_monte_carlo.py tests/probability/test_cuda_monte_carlo.py
git commit -m "feat: aggregate cuda monte carlo seeds"
```

---

### Task 3: Prior-Derived Sensitivity Grid

**Files:**
- Modify: `src/polymarket_engine/probability/cuda_monte_carlo.py`
- Test: `tests/probability/test_cuda_monte_carlo.py`

- [ ] **Step 1: Write the failing test for sensitivity payload shape**

Add this test to `tests/probability/test_cuda_monte_carlo.py`:

```python
def test_prior_sensitivity_rows_are_distribution_based() -> None:
    module = importlib.import_module("polymarket_engine.probability.cuda_monte_carlo")
    paths = (
        (100.0, 101.0, 102.0, 103.0),
        (100.0, 99.0, 98.0, 97.0),
        (100.0, 100.5, 101.0, 101.5),
        (100.0, 98.5, 99.0, 100.0),
    )
    probability_input = module.ProbabilityInput(
        state_id="state-btc-up",
        asof_ts=datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc),
        asset="BTC",
        side="UP",
        comparison_operator=">",
        seconds_left=120.0,
        settlement_price=100.0,
        threshold=100.0,
        sigma_tau=0.02,
        executable_price=0.50,
        source_age_ms=100,
        book_age_ms=100,
        z_path=0.0,
    )

    rows = module._prior_sensitivity_from_cpu_paths(
        probability_input,
        paths=paths,
        terminal_wins=(True, False, True, False),
    )

    assert rows
    assert {row["dimension"] for row in rows} == {"prior_price_quantile"}
    assert all("price_delta" not in row for row in rows)
    assert all("dollar_move" not in row for row in rows)
    assert all(0.0 <= row["p_hat"] <= 1.0 for row in rows)
    assert all(row["sample_count"] > 0 for row in rows)
    assert all("quantile_low" in row and "quantile_high" in row for row in rows)
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest tests/probability/test_cuda_monte_carlo.py::test_prior_sensitivity_rows_are_distribution_based -q
```

Expected: FAIL with missing `_prior_sensitivity_from_cpu_paths`.

- [ ] **Step 3: Implement prior sensitivity from path quantile bands**

Add this function near `_simulation_preview_from_cuda()` in `src/polymarket_engine/probability/cuda_monte_carlo.py`:

```python
def _prior_sensitivity_from_cpu_paths(
    probability_input: ProbabilityInput,
    *,
    paths: tuple[tuple[float, ...], ...],
    terminal_wins: tuple[bool, ...],
) -> list[dict[str, Any]]:
    if not paths:
        return []
    rows: list[dict[str, Any]] = []
    time_fractions = (0.25, 0.50, 0.75)
    quantile_bands = ((0.0, 0.25), (0.25, 0.50), (0.50, 0.75), (0.75, 1.0))
    point_count = len(paths[0])
    for time_fraction in time_fractions:
        point_index = min(point_count - 1, max(0, round((point_count - 1) * time_fraction)))
        values = tuple(path[point_index] for path in paths)
        ranked = sorted(enumerate(values), key=lambda item: item[1])
        for quantile_low, quantile_high in quantile_bands:
            start = int(math.floor(len(ranked) * quantile_low))
            end = int(math.ceil(len(ranked) * quantile_high))
            band = ranked[start:max(start + 1, end)]
            indices = tuple(index for index, _ in band)
            wins = sum(1 for index in indices if terminal_wins[index])
            price_values = tuple(value for _, value in band)
            rows.append(
                {
                    "dimension": "prior_price_quantile",
                    "time_fraction": time_fraction,
                    "point_index": point_index,
                    "quantile_low": quantile_low,
                    "quantile_high": quantile_high,
                    "sample_count": len(indices),
                    "price_quantile": statistics.fmean(price_values),
                    "log_return_quantile": math.log(
                        statistics.fmean(price_values) / probability_input.settlement_price
                    ),
                    "p_hat": wins / len(indices),
                }
            )
    return rows
```

- [ ] **Step 4: Attach sensitivity rows to CUDA diagnostics**

Inside `_simulation_preview_from_cuda()`, after `sampled_path_rows = ...`, add:

```python
    terminal_wins_cpu = tuple(bool(value) for value in cp.asnumpy(terminal_wins_mask).tolist())
    sensitivity_paths = tuple(
        tuple(float(price) for price in row.tolist())
        for row in cp.asnumpy(full_paths[: min(2048, path_count), :]).tolist()
    )
    sensitivity_terminal_wins = terminal_wins_cpu[: len(sensitivity_paths)]
```

Then add this key to the returned dictionary:

```python
        "prior_sensitivity": _prior_sensitivity_from_cpu_paths(
            probability_input,
            paths=sensitivity_paths,
            terminal_wins=sensitivity_terminal_wins,
        ),
```

Change `_simulation_preview_from_cuda()` signature to accept `terminal_wins_mask`:

```python
def _simulation_preview_from_cuda(
    cp: ModuleType,
    probability_input: ProbabilityInput,
    *,
    full_paths: Any,
    terminal_prices: Any,
    terminal_wins_mask: Any,
    terminal_wins: int,
    no_touch_wins: int,
) -> dict[str, Any]:
```

And update the caller:

```python
        preview = _simulation_preview_from_cuda(
            cp,
            probability_input,
            full_paths=full_paths,
            terminal_prices=terminal_prices,
            terminal_wins_mask=terminal_wins_mask,
            terminal_wins=terminal_wins,
            no_touch_wins=no_touch_wins,
        )
```

- [ ] **Step 5: Promote sensitivity rows to top-level diagnostics**

In the `ProbabilityOutput(... diagnostics={...})` dictionary inside `run_cuda_monte_carlo()`, add the top-level `prior_sensitivity` key alongside `simulation_preview`:

```python
        diagnostics={
            "path_count": path_count,
            "steps": steps,
            "model": "cuda_lognormal_chainlink_sigma",
            "simulation_preview": preview,
            "prior_sensitivity": preview.get("prior_sensitivity", []),
        },
```

- [ ] **Step 6: Run sensitivity tests**

Run:

```bash
uv run pytest tests/probability/test_cuda_monte_carlo.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/polymarket_engine/probability/cuda_monte_carlo.py tests/probability/test_cuda_monte_carlo.py
git commit -m "feat: derive cuda sensitivity from prior paths"
```

---

### Task 4: GPU Worker Uses Multi-Seed Values

**Files:**
- Modify: `src/polymarket_engine/probability/gpu_worker.py`
- Test: `tests/probability/test_gpu_worker.py`

- [ ] **Step 1: Write the failing worker test**

Add this test to `tests/probability/test_gpu_worker.py`:

```python
def test_cuda_probability_worker_writes_p_hat_confidence_and_seed_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polymarket_engine.probability import gpu_worker

    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "probabilities.json"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = _decision_state()
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)

    calls: list[dict[str, int]] = []

    def fake_multi_seed(
        probability_input: ProbabilityInput,
        *,
        paths_per_seed: int,
        steps: int,
        seed: int,
        seed_count: int,
    ) -> ProbabilityOutput:
        calls.append(
            {
                "paths_per_seed": paths_per_seed,
                "steps": steps,
                "seed": seed,
                "seed_count": seed_count,
            }
        )
        total_paths = paths_per_seed * seed_count
        return ProbabilityOutput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            p_finish=0.625,
            p_no_touch=0.575,
            z_path=probability_input.z_path,
            model_version="cuda-lognormal-chainlink-sigma-multiseed-v1",
            seed=seed,
            diagnostics={
                "path_count": total_paths,
                "paths_per_seed": paths_per_seed,
                "seed_count": seed_count,
                "steps": steps,
                "model": "cuda_lognormal_chainlink_sigma_multi_seed",
                "p_hat": 0.625,
                "p_hat_std": 0.025,
                "p_hat_ci_low": 0.58,
                "p_hat_ci_high": 0.65,
                "seed_runs": [
                    {"seed": seed, "p_hat": 0.60, "p_no_touch": 0.55, "path_count": paths_per_seed},
                    {"seed": seed + 11, "p_hat": 0.65, "p_no_touch": 0.60, "path_count": paths_per_seed},
                ],
                "prior_sensitivity": [
                    {
                        "dimension": "prior_price_quantile",
                        "time_fraction": 0.5,
                        "quantile_low": 0.5,
                        "quantile_high": 0.75,
                        "sample_count": 200,
                        "price_quantile": probability_input.settlement_price,
                        "log_return_quantile": 0.0,
                        "p_hat": 0.625,
                    }
                ],
                "simulation_preview": {
                    "path_count": total_paths,
                    "sampled_paths": [],
                    "prior_sensitivity": [],
                },
            },
        )

    monkeypatch.setattr(gpu_worker, "run_cuda_monte_carlo_multi_seed", fake_multi_seed)

    result = gpu_worker.run_cuda_probability_worker_cycle(
        duckdb_path=db_path,
        probability_status_path=status_path,
        limit=24,
        valid_seconds=30,
    )

    row = result["rows"][0]
    assert row["p_finish"] == pytest.approx(0.625)
    assert row["p_hat"] == pytest.approx(0.625)
    assert row["p_hat_ci_low"] == pytest.approx(0.58)
    assert row["p_hat_ci_high"] == pytest.approx(0.65)
    assert row["seed_count"] == calls[0]["seed_count"]
    assert row["paths_per_seed"] == calls[0]["paths_per_seed"]
    assert row["path_count"] == calls[0]["paths_per_seed"] * calls[0]["seed_count"]
    assert row["prior_sensitivity"][0]["dimension"] == "prior_price_quantile"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest tests/probability/test_gpu_worker.py::test_cuda_probability_worker_writes_p_hat_confidence_and_seed_metadata -q
```

Expected: FAIL because `gpu_worker` still calls `run_cuda_monte_carlo()` and does not expose the new fields.

- [ ] **Step 3: Update imports and call multi-seed CUDA**

In `src/polymarket_engine/probability/gpu_worker.py`, replace:

```python
from polymarket_engine.probability.cuda_monte_carlo import run_cuda_monte_carlo
```

with:

```python
from polymarket_engine.probability.cuda_monte_carlo import run_cuda_monte_carlo_multi_seed
```

Replace the path-count block inside the worker loop:

```python
        path_count = runtime_path_count_for_seconds_left(probability_input.seconds_left)
```

with:

```python
        paths_per_seed = runtime_paths_per_seed_for_seconds_left(probability_input.seconds_left)
        seed_count = runtime_seed_count_for_seconds_left(probability_input.seconds_left)
        path_count = runtime_total_path_count_for_seconds_left(probability_input.seconds_left)
```

Update the path-policy imports:

```python
from polymarket_engine.probability.path_policy import runtime_paths_per_seed_for_seconds_left
from polymarket_engine.probability.path_policy import runtime_seed_count_for_seconds_left
from polymarket_engine.probability.path_policy import runtime_total_path_count_for_seconds_left
```

Replace the CUDA call:

```python
            output = run_cuda_monte_carlo(
                probability_input,
                path_count=path_count,
                steps=steps,
                seed=seed,
            )
```

with:

```python
            output = run_cuda_monte_carlo_multi_seed(
                probability_input,
                paths_per_seed=paths_per_seed,
                steps=steps,
                seed=seed,
                seed_count=seed_count,
            )
```

Add these diagnostics cache fields:

```python
                "path_count": path_count,
                "paths_per_seed": paths_per_seed,
                "seed_count": seed_count,
```

- [ ] **Step 4: Run worker test**

Run:

```bash
uv run pytest tests/probability/test_gpu_worker.py::test_cuda_probability_worker_writes_p_hat_confidence_and_seed_metadata -q
```

Expected: still FAIL until runtime diagnostic extraction is updated in Task 5.

- [ ] **Step 5: Commit only if Task 5 is already complete**

Do not commit this task alone if tests fail. Continue directly to Task 5, then commit both together.

---

### Task 5: Runtime Row Diagnostic Extraction

**Files:**
- Modify: `src/polymarket_engine/probability/runtime.py`
- Modify: `src/polymarket_engine/probability/grid_cache.py`
- Test: `tests/probability/test_gpu_worker.py`
- Test: `tests/probability/test_grid_cache.py`

- [ ] **Step 1: Write the failing grid row test**

Add this assertion block to `tests/probability/test_grid_cache.py` in `test_probability_grid_entry_round_trips_and_returns_runtime_metadata` after `runtime_row = grid_runtime_row(...)`:

```python
    assert runtime_row["p_hat"] == pytest.approx(runtime_row["p_finish"])
```

Then add a new test:

```python
def test_grid_runtime_row_extracts_confidence_and_sensitivity_diagnostics(tmp_path: Path) -> None:
    store = DuckDbIngestStore(tmp_path / "grid.duckdb")
    store.apply_schema()
    probability_input = _probability_input()
    entry = _entry(
        probability_input,
        diagnostics={
            "p_hat": 0.674,
            "p_hat_std": 0.012,
            "p_hat_ci_low": 0.650,
            "p_hat_ci_high": 0.698,
            "paths_per_seed": 10_000,
            "seed_count": 3,
            "prior_sensitivity": [
                {
                    "dimension": "prior_price_quantile",
                    "time_fraction": 0.5,
                    "quantile_low": 0.5,
                    "quantile_high": 0.75,
                    "sample_count": 200,
                    "price_quantile": 70_100.0,
                    "log_return_quantile": 0.001,
                    "p_hat": 0.674,
                }
            ],
        },
    )
    row = grid_runtime_row(
        probability_input=probability_input,
        contract="BTC 5m UP",
        contract_id="btc-up",
        market_slug="btc-updown-5m",
        start_ts=entry.start_ts,
        expiry_ts=entry.expiry_ts,
        hit=ProbabilityGridHit(entry=entry, cache_status="REFRESH"),
        now=entry.generated_at,
    )

    assert row["p_hat"] == pytest.approx(0.674)
    assert row["p_hat_ci_low"] == pytest.approx(0.650)
    assert row["p_hat_ci_high"] == pytest.approx(0.698)
    assert row["paths_per_seed"] == 10_000
    assert row["seed_count"] == 3
    assert row["prior_sensitivity"][0]["dimension"] == "prior_price_quantile"
```

If `_entry()` does not accept `diagnostics`, change the helper signature from:

```python
def _entry(probability_input: ProbabilityInput) -> ProbabilityGridEntry:
```

to:

```python
def _entry(
    probability_input: ProbabilityInput,
    *,
    diagnostics: dict[str, object] | None = None,
) -> ProbabilityGridEntry:
```

and pass:

```python
        diagnostics=diagnostics or {"source": "test"},
```

- [ ] **Step 2: Run grid test to verify it fails**

Run:

```bash
uv run pytest tests/probability/test_grid_cache.py::test_grid_runtime_row_extracts_confidence_and_sensitivity_diagnostics -q
```

Expected: FAIL because runtime rows do not expose the new fields.

- [ ] **Step 3: Add diagnostic extraction in `runtime.py`**

In `_runtime_detail_from_diagnostics()` return dictionary, add:

```python
        "p_hat": _optional_runtime_float(
            diagnostics.get("p_hat", ensemble.get("p_hat")),
            "p_hat",
        ),
        "p_hat_std": _optional_runtime_float(
            diagnostics.get("p_hat_std", ensemble.get("p_hat_std")),
            "p_hat_std",
        ),
        "p_hat_ci_low": _optional_runtime_float(
            diagnostics.get("p_hat_ci_low", ensemble.get("p_hat_ci_low")),
            "p_hat_ci_low",
        ),
        "p_hat_ci_high": _optional_runtime_float(
            diagnostics.get("p_hat_ci_high", ensemble.get("p_hat_ci_high")),
            "p_hat_ci_high",
        ),
        "paths_per_seed": _optional_runtime_int(
            diagnostics.get("paths_per_seed", ensemble.get("paths_per_seed")),
            "paths_per_seed",
        ),
        "seed_count": _optional_runtime_int(
            diagnostics.get("seed_count", ensemble.get("seed_count")),
            "seed_count",
        ),
        "prior_sensitivity": _optional_json_list(
            diagnostics.get("prior_sensitivity", ensemble.get("prior_sensitivity")),
            "prior_sensitivity",
        ),
```

Add these helpers below `_optional_runtime_float()`:

```python
def _optional_runtime_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    return _int(value, field_name)


def _optional_json_list(value: object, field_name: str) -> list[object]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be a JSON list")
    return list(value)
```

- [ ] **Step 4: Add diagnostic extraction in `grid_cache.py`**

In `grid_runtime_row()`, after `grid_cache = {...}`, add:

```python
    diagnostics = dict(entry.diagnostics)
```

Then add these top-level fields to the returned row:

```python
        "p_hat": _optional_float(diagnostics.get("p_hat")) or entry.p_finish,
        "p_hat_std": _optional_float(diagnostics.get("p_hat_std")),
        "p_hat_ci_low": _optional_float(diagnostics.get("p_hat_ci_low")),
        "p_hat_ci_high": _optional_float(diagnostics.get("p_hat_ci_high")),
        "paths_per_seed": _optional_int(diagnostics.get("paths_per_seed")),
        "seed_count": _optional_int(diagnostics.get("seed_count")),
        "prior_sensitivity": _optional_list(diagnostics.get("prior_sensitivity")),
```

Add these helpers near the bottom of `grid_cache.py`:

```python
def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("optional float must be finite")
    return number


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("optional int must be an int")
    return value


def _optional_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, list):
        raise ValueError("optional list must be a list")
    return list(value)
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/probability/test_grid_cache.py tests/probability/test_gpu_worker.py::test_cuda_probability_worker_writes_p_hat_confidence_and_seed_metadata -q
```

Expected: PASS.

- [ ] **Step 6: Commit Tasks 4 and 5 together**

```bash
git add src/polymarket_engine/probability/gpu_worker.py src/polymarket_engine/probability/runtime.py src/polymarket_engine/probability/grid_cache.py tests/probability/test_gpu_worker.py tests/probability/test_grid_cache.py
git commit -m "feat: publish cuda probability confidence rows"
```

---

### Task 6: UI Probability Value Helper and UP/DOWN Selection

**Files:**
- Modify: `ui/src/probabilityRows.ts`
- Modify: `ui/src/App.tsx`
- Create: `tests/ui/probability_value_test.ts`
- Modify: `tests/ui/test_probability_rows_helper.py`

- [ ] **Step 1: Write the failing UI helper test**

Create `tests/ui/probability_value_test.ts`:

```typescript
import assert from "node:assert/strict";
import {
  probabilityDisplayValue,
  probabilityMetadata,
  probabilityRowKey,
} from "../../ui/src/probabilityRows";

const upRow = {
  contract_id: "btc-up",
  output_id: "out-up",
  asset: "BTC",
  side: "UP",
  expiry_ts: "2026-06-05T13:25:00Z",
  asof_ts: "2026-06-05T13:20:00Z",
  p_finish: 0.55,
  p_hat: 0.56,
  path_count: 120000,
  paths_per_seed: 30000,
  seed_count: 4,
  simulation_preview: {
    path_count: 120000,
    sampled_paths: new Array(24).fill({ points: [1, 2, 3] }),
  },
};

const downRow = {
  ...upRow,
  contract_id: "btc-down",
  output_id: "out-down",
  side: "DOWN",
  p_finish: 0.44,
  p_hat: 0.45,
};

assert.equal(probabilityDisplayValue(upRow), 0.56);
assert.equal(probabilityDisplayValue({ ...upRow, p_hat: undefined }), 0.55);
assert.notEqual(probabilityRowKey(upRow), probabilityRowKey(downRow));
assert.deepEqual(probabilityMetadata(upRow), {
  totalPaths: 120000,
  pathsPerSeed: 30000,
  seedCount: 4,
  previewPathCount: 24,
});
```

Update `tests/ui/test_probability_rows_helper.py`:

```python
def test_probability_value_helper_handles_p_hat_and_path_metadata(tmp_path: Path) -> None:
    bundled = tmp_path / "probability_value_test.cjs"
    subprocess.run(
        [
            str(ROOT / "ui/node_modules/esbuild/bin/esbuild"),
            str(ROOT / "tests/ui/probability_value_test.ts"),
            "--bundle",
            "--platform=node",
            "--format=cjs",
            f"--outfile={bundled}",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        ["node", str(bundled)],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
```

- [ ] **Step 2: Run UI helper test to verify it fails**

Run:

```bash
uv run pytest tests/ui/test_probability_rows_helper.py::test_probability_value_helper_handles_p_hat_and_path_metadata -q
```

Expected: FAIL because the helper exports do not exist.

- [ ] **Step 3: Implement UI helper exports**

Add to `ui/src/probabilityRows.ts`:

```typescript
export type ProbabilityValueRow = ProbabilityRowForGraph & {
  contract_id?: string;
  output_id?: string;
  asset?: string;
  side?: string;
  asof_ts?: string;
  p_finish?: number;
  p_hat?: number;
  path_count?: number;
  paths_per_seed?: number;
  seed_count?: number;
  simulation_preview?: unknown;
};

export function probabilityDisplayValue(row?: ProbabilityValueRow | null) {
  if (!row) {
    return undefined;
  }
  return isFiniteNumber(row.p_hat) ? row.p_hat : row.p_finish;
}

export function probabilityRowKey(row: ProbabilityValueRow) {
  return [
    row.output_id,
    row.contract_id,
    row.asset,
    row.side,
    row.expiry_ts,
    row.asof_ts,
  ]
    .filter((value) => value !== undefined && value !== null && value !== "")
    .join("|");
}

export function probabilityMetadata(row: ProbabilityValueRow) {
  const preview = parsePreview(row.simulation_preview);
  return {
    totalPaths: isFiniteNumber(row.path_count) ? row.path_count : undefined,
    pathsPerSeed: isFiniteNumber(row.paths_per_seed) ? row.paths_per_seed : undefined,
    seedCount: isFiniteNumber(row.seed_count) ? row.seed_count : undefined,
    previewPathCount: Array.isArray(preview?.sampled_paths)
      ? preview.sampled_paths.length
      : undefined,
  };
}

function parsePreview(value: unknown): { sampled_paths?: unknown[] } | null {
  return isRecord(value) ? value : null;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
```

- [ ] **Step 4: Wire `App.tsx` to `p_hat` helper**

Update import:

```typescript
import {
  filterGraphableProbabilityRows,
  probabilityDisplayValue,
  probabilityMetadata,
  probabilityRowKey,
} from "./probabilityRows";
```

Replace every display use of `row.p_finish` with `probabilityDisplayValue(row)` in these locations:

```typescript
formatProbability(probabilityDisplayValue(row))
normalizedProbability(probabilityDisplayValue(marketRow?.upProbability))
normalizedProbability(probabilityDisplayValue(marketRow?.downProbability))
```

Replace `rowKey(row)` implementation with:

```typescript
function rowKey(row: ProbabilityRow) {
  return probabilityRowKey(row);
}
```

In `MonteCarloInputsPanel`, replace path metric labels with:

```typescript
  const metadata = probabilityMetadata(row);
```

Then render:

```typescript
<Metric label="Total CUDA paths" value={formatInteger(metadata.totalPaths)} />
<Metric label="Paths / seed" value={formatInteger(metadata.pathsPerSeed)} />
<Metric label="Seeds" value={formatInteger(metadata.seedCount)} />
<Metric label="Preview paths" value={formatInteger(metadata.previewPathCount)} />
```

- [ ] **Step 5: Run UI helper and build**

Run:

```bash
uv run pytest tests/ui/test_probability_rows_helper.py -q
npm --prefix ui run build
```

Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add ui/src/probabilityRows.ts ui/src/App.tsx tests/ui/probability_value_test.ts tests/ui/test_probability_rows_helper.py
git commit -m "feat: show canonical monte carlo probability values"
```

---

### Task 7: UI Sensitivity Panel

**Files:**
- Modify: `ui/src/App.tsx`
- Modify: `ui/src/styles.css`
- Test: `tests/ui/test_runtime_monitor_source.py`

- [ ] **Step 1: Write the failing source contract test**

Add to `tests/ui/test_runtime_monitor_source.py`:

```python
def test_runtime_monitor_shows_prior_derived_sensitivity_grid() -> None:
    source = (ROOT / "ui/src/App.tsx").read_text(encoding="utf-8")

    assert "PriorSensitivityGrid" in source
    assert "prior_sensitivity" in source
    assert "Prior quantile" in source
    assert "dollar move" not in source.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest tests/ui/test_runtime_monitor_source.py::test_runtime_monitor_shows_prior_derived_sensitivity_grid -q
```

Expected: FAIL because `PriorSensitivityGrid` does not exist.

- [ ] **Step 3: Add UI types**

Add to `ProbabilityRow` in `ui/src/App.tsx`:

```typescript
  p_hat?: number;
  p_hat_std?: number;
  p_hat_ci_low?: number;
  p_hat_ci_high?: number;
  paths_per_seed?: number;
  seed_count?: number;
  prior_sensitivity?: unknown[];
```

Add this type near `ProbabilityRow`:

```typescript
type PriorSensitivityRow = {
  dimension?: string;
  time_fraction?: number;
  quantile_low?: number;
  quantile_high?: number;
  sample_count?: number;
  price_quantile?: number;
  log_return_quantile?: number;
  p_hat?: number;
};
```

- [ ] **Step 4: Add parser and component**

Add these functions in `ui/src/App.tsx` near `MonteCarloInputsPanel`:

```typescript
function PriorSensitivityGrid({ row }: { row: ProbabilityRow }) {
  const rows = parsePriorSensitivity(row.prior_sensitivity).slice(0, 12);
  if (rows.length === 0) {
    return (
      <section className="mc-input-section sensitivity-section">
        <h3>Prior Sensitivity</h3>
        <p className="quiet">Waiting for prior-derived sensitivity rows.</p>
      </section>
    );
  }
  return (
    <section className="mc-input-section sensitivity-section">
      <h3>Prior Sensitivity</h3>
      <div className="sensitivity-grid">
        {rows.map((item, index) => (
          <div className="sensitivity-row" key={`${item.time_fraction}-${item.quantile_low}-${index}`}>
            <span>Prior quantile {formatQuantileBand(item)}</span>
            <strong>{formatProbability(item.p_hat)}</strong>
            <small>
              {compactList([
                formatTimeFraction(item.time_fraction),
                item.sample_count ? `n=${formatInteger(item.sample_count)}` : undefined,
                item.price_quantile ? formatPrice(item.price_quantile) : undefined,
              ])}
            </small>
          </div>
        ))}
      </div>
    </section>
  );
}

function parsePriorSensitivity(value: unknown): PriorSensitivityRow[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(isRecord).map((row) => ({
    dimension: typeof row.dimension === "string" ? row.dimension : undefined,
    time_fraction: numberOrUndefined(row.time_fraction),
    quantile_low: numberOrUndefined(row.quantile_low),
    quantile_high: numberOrUndefined(row.quantile_high),
    sample_count: numberOrUndefined(row.sample_count),
    price_quantile: numberOrUndefined(row.price_quantile),
    log_return_quantile: numberOrUndefined(row.log_return_quantile),
    p_hat: numberOrUndefined(row.p_hat),
  }));
}

function numberOrUndefined(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function formatQuantileBand(row: PriorSensitivityRow) {
  if (row.quantile_low === undefined || row.quantile_high === undefined) {
    return "-";
  }
  return `${Math.round(row.quantile_low * 100)}-${Math.round(row.quantile_high * 100)}%`;
}

function formatTimeFraction(value?: number) {
  return value === undefined ? undefined : `t=${Math.round(value * 100)}%`;
}
```

- [ ] **Step 5: Render sensitivity panel**

Inside `MonteCarloInputsPanel`, after the `Contract State` section, add:

```typescript
        <PriorSensitivityGrid row={row} />
```

- [ ] **Step 6: Add compact CSS**

Add to `ui/src/styles.css`:

```css
.sensitivity-section {
  min-width: 0;
}

.sensitivity-grid {
  display: grid;
  gap: 6px;
}

.sensitivity-row {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 2px 8px;
  align-items: baseline;
  padding: 6px 0;
  border-bottom: 1px solid var(--border-muted);
}

.sensitivity-row span,
.sensitivity-row small {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sensitivity-row small {
  grid-column: 1 / -1;
  color: var(--text-muted);
}
```

- [ ] **Step 7: Run UI tests and build**

Run:

```bash
uv run pytest tests/ui/test_runtime_monitor_source.py tests/ui/test_probability_rows_helper.py -q
npm --prefix ui run build
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add ui/src/App.tsx ui/src/styles.css tests/ui/test_runtime_monitor_source.py
git commit -m "feat: show prior sensitivity grid"
```

---

### Task 8: Browser Verification for UP/DOWN Switching and Path Counts

**Files:**
- No source files required if Task 6 and Task 7 pass.
- Use browser verification against a local or THEPC runtime.

- [ ] **Step 1: Start or identify runtime URL**

Use THEPC if deployed:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -lc 'cd /home/ender/polymarket && git rev-parse HEAD && docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml ps --services --status running | sort'"
```

Expected: deployed SHA matches current branch and services include `api`, `collector`, `normalizer`, `gpu-probability-worker`.

- [ ] **Step 2: Verify API row values**

Run:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -lc 'python3 - <<PY
import json, urllib.request
payload = json.load(urllib.request.urlopen(\"http://127.0.0.1:8000/api/runtime/probabilities?limit=8\", timeout=8))
rows = payload.get(\"rows\") or []
print(json.dumps({
  \"ok\": payload.get(\"ok\"),
  \"rows\": len(rows),
  \"paths\": sorted({row.get(\"path_count\") for row in rows}),
  \"seeds\": sorted({row.get(\"seed_count\") for row in rows}),
  \"has_p_hat\": all(\"p_hat\" in row for row in rows),
  \"has_sensitivity\": all(bool(row.get(\"prior_sensitivity\")) for row in rows),
}, sort_keys=True))
PY'"
```

Expected: `ok=true`, 4 rows, `has_p_hat=true`, `has_sensitivity=true`.

- [ ] **Step 3: Use Browser plugin to inspect UI**

Open:

```text
http://127.0.0.1:8000/
```

If running from Mac against THEPC, open:

```text
http://100.72.104.49:8000/
```

Expected visual checks:

- BTC and ETH each show UP and DOWN buttons.
- Clicking UP highlights/selects UP.
- Clicking DOWN highlights/selects DOWN.
- The selected graph/card label changes side.
- The probability number changes to that side's `p_hat`.
- The panel shows Total CUDA paths, Paths / seed, Seeds, and Preview paths.
- The Prior Sensitivity section shows prior quantile rows.
- No UI copy says fixed dollar moves.

- [ ] **Step 4: Capture browser evidence**

Use the Browser screenshot/tooling to record:

```text
selected UP side screenshot
selected DOWN side screenshot
console logs empty or non-blocking
```

Expected: no React runtime errors, no overlapping metric text.

- [ ] **Step 5: Commit if any UI-only fix was needed**

If browser verification required CSS or selection fixes:

```bash
git add ui/src/App.tsx ui/src/styles.css tests/ui/test_runtime_monitor_source.py
git commit -m "fix: clarify monte carlo side selection"
```

If no changes were needed, do not create a commit.

---

### Task 9: Deploy and Runtime Smoke

**Files:**
- Modify only if deploy smoke reveals a real defect.
- Scripts: `scripts/deploy_pc.sh`

- [ ] **Step 1: Run full verification before deploy**

Run:

```bash
uv run pytest -q
npm --prefix ui run build
bash -n scripts/deploy_pc.sh
bash -n deploy/gpu/gpu-probability-entrypoint.sh
```

Expected:

- pytest reports all tests passing.
- Vite build exits 0.
- shell syntax checks exit 0.

- [ ] **Step 2: Deploy to THEPC**

Run:

```bash
./scripts/deploy_pc.sh
```

Expected:

- Deploy prints `deploy OK`.
- Services running: `collector`, `normalizer`, `api`, `gpu-probability-worker`.
- THEPC deployed SHA equals local HEAD.

- [ ] **Step 3: Verify CUDA probability payload**

Run:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -lc 'cd /home/ender/polymarket && python3 - <<PY
import json, urllib.request
payload = json.load(urllib.request.urlopen(\"http://127.0.0.1:8000/api/runtime/probabilities?limit=8\", timeout=8))
rows = payload.get(\"rows\") or []
print(json.dumps({
  \"ok\": payload.get(\"ok\"),
  \"state\": payload.get(\"state\"),
  \"rows\": len(rows),
  \"generators\": sorted({row.get(\"generator_version\") for row in rows}),
  \"path_counts\": sorted({row.get(\"path_count\") for row in rows}),
  \"seed_counts\": sorted({row.get(\"seed_count\") for row in rows}),
  \"p_hat_complete\": all(\"p_hat\" in row for row in rows),
  \"confidence_complete\": all(\"p_hat_ci_low\" in row and \"p_hat_ci_high\" in row for row in rows),
  \"sensitivity_complete\": all(bool(row.get(\"prior_sensitivity\")) for row in rows),
  \"contracts\": sorted((row.get(\"asset\"), row.get(\"side\")) for row in rows),
  \"errors\": payload.get(\"errors\"),
}, sort_keys=True))
PY'"
```

Expected:

- `ok=true`
- `rows=4`
- `generators=["cuda-lognormal-chainlink-sigma-multiseed-v1"]`
- BTC/ETH UP/DOWN present
- all rows include `p_hat`, confidence fields, seed counts, and prior sensitivity rows
- `errors=[]`

- [ ] **Step 4: Sample GPU usage**

Run:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -lc 'nvidia-smi --query-gpu=utilization.gpu,utilization.memory,memory.used,power.draw --format=csv,noheader && docker stats --no-stream --format \"{{.Name}} {{.CPUPerc}} {{.MemUsage}}\" polymarket-rust-collector-gpu-probability-worker-1'"
```

Expected:

- GPU worker is visible.
- GPU memory is stable.
- Utilization may remain bursty; higher path totals should increase peaks compared with single-seed 20k/30k.

- [ ] **Step 5: Commit deploy-only fixes if required**

If deploy required script changes:

```bash
git add scripts/deploy_pc.sh deploy/collector/docker-compose.yml deploy/gpu/gpu-probability-entrypoint.sh
git commit -m "fix: deploy multiseed cuda probability worker"
```

If no deploy script changes were needed, do not create a commit.

---

### Task 10: PR Update

**Files:**
- No source files.

- [ ] **Step 1: Confirm branch status**

Run:

```bash
git status -sb
git rev-parse HEAD
git rev-list --left-right --count origin/codex/stabilize-tui-layout...HEAD
```

Expected: clean branch; local branch ahead only if commits have not yet been pushed.

- [ ] **Step 2: Push branch**

Run:

```bash
git push -u origin codex/stabilize-tui-layout
```

Expected: branch pushes without force.

- [ ] **Step 3: Update PR body**

Run:

```bash
DEPLOYED_SHA="$(git rev-parse HEAD)"
gh pr edit 25 --title "Add CUDA confidence bands and prior sensitivity" --body-file - <<EOF
## Summary

- Adds multi-seed CUDA Monte Carlo aggregation with canonical `p_hat` values.
- Keeps `p_finish` as the backward-compatible storage/API alias for `p_hat`.
- Adds confidence-band and seed metadata to runtime probability rows.
- Adds prior-derived sensitivity rows based on simulated path quantile bands, not fixed dollar moves.
- Updates the runtime UI so UP/DOWN selection changes the displayed side and graph, and path-count metadata distinguishes total CUDA paths from sampled preview paths.

## Verification

- [ ] `uv run pytest -q`
- [ ] `npm --prefix ui run build`
- [ ] THEPC deploy at `${DEPLOYED_SHA}`
- [ ] Remote CUDA probability smoke includes BTC/ETH UP/DOWN, `p_hat`, confidence fields, seed metadata, and prior sensitivity rows
- [ ] Browser verification: UP/DOWN click changes selected graph and path-count panel shows total paths versus preview paths

🤖 Generated with [Codex](https://Codex.com/Codex)
EOF
```

- [ ] **Step 4: Stop**

Do not merge automatically. Wait for explicit user approval.

---

## Self-Review

Spec coverage:

- GPU does more useful work: Tasks 1, 2, 4, and 9 increase CUDA paths and seeds.
- Probability value calculation changes: Tasks 2, 4, 5, and 6 introduce `p_hat` and confidence bands.
- Sensitivity grid is prior-derived: Task 3 computes sensitivity from path quantile bands and explicitly rejects dollar moves.
- UI shows path counts clearly: Task 6 separates total paths from preview paths.
- UP/DOWN switching changes selected graph/value: Task 6 updates row keys and display helpers; Task 8 verifies interaction in browser.

Red-flag scan:

- The plan contains no deferred-work markers or empty implementation instructions.
- Each code-changing task includes concrete code snippets and commands.

Type consistency:

- Backend fields: `p_hat`, `p_hat_std`, `p_hat_ci_low`, `p_hat_ci_high`, `paths_per_seed`, `seed_count`, `prior_sensitivity`.
- UI fields use the same snake_case names from API rows.
- `path_count` means total simulated CUDA paths across all seeds.
