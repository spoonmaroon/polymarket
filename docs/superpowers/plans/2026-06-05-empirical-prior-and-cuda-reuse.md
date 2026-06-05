# Empirical Prior and CUDA Reuse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add process-local CUDA context/module reuse and a first-pass as-of-safe empirical Chainlink path-fragment prior for Monte Carlo probability outputs.

**Architecture:** The CUDA backend stays in-process and caches the CUDA context, stream, loaded module, function handle, and GPU metadata inside each long-lived `CudaBackend`. The empirical prior remains read-only and output-only: it samples historical Chainlink BTC/ETH fragments whose event and observed timestamps are both no later than the current decision `asof_ts`, rescales normalized historical residual paths by current `sigma_tau`, and falls back to the existing lognormal baseline when the empirical bucket is sparse.

**Tech Stack:** Rust 2024, `cudarc`, Ratatui-adjacent probability crates, Python 3.11+, DuckDB, NumPy, pytest, Cargo tests, THEPC WSL CUDA 13.2.

---

## File Structure

- Modify `rust/crates/polymarket-probability-cuda/src/lib.rs`: make `CudaBackend` stateful with an internal `Mutex<Option<CudaRuntime>>`, move CUDA setup into `CudaRuntime::new`, reuse context/module/function for later runs, add `cuda_cache_hit` diagnostics.
- Modify `rust/crates/polymarket-probability-cuda/tests/monte_carlo.rs`: construct `CudaBackend::default()` and add an ignored THEPC-only cache miss/hit regression.
- Modify `rust/crates/polymarket-probability-cuda/examples/benchmark_cuda.rs`: construct the stateful backend once and reuse it for all benchmark iterations.
- Create `src/polymarket_engine/probability/empirical_prior.py`: pure empirical prior path generator from Chainlink `PriceObservation` rows.
- Modify `src/polymarket_engine/probability/runtime.py`: route `POLYMARKET_PROBABILITY_GENERATOR=empirical_conditional` through the empirical prior using Chainlink history from `DuckDbIngestStore.price_ticks_before`.
- Modify `src/polymarket_engine/probability/__init__.py`: export the empirical prior entrypoint.
- Create `tests/probability/test_empirical_prior.py`: unit tests for as-of safety, sparse fallback, and sigma-scaled empirical paths.
- Create `tests/probability/test_probability_runtime_empirical_prior.py`: focused runtime test proving the env-selected empirical generator persists empirical diagnostics.
- Modify `scripts/benchmark_monte_carlo_backends.sh`: keep the existing cases and use the now-stateful CUDA backend to rerun THEPC timings.
- Modify `docs/reports/monte-carlo-backend-benchmark-2026-06-05.md`: update measured THEPC results after rerun.

## Task 1: CUDA Cache Test First

**Files:**
- Modify: `rust/crates/polymarket-probability-cuda/tests/monte_carlo.rs`

- [ ] **Step 1: Add the failing cache diagnostic test**

Add this ignored test near the other ignored CUDA tests:

```rust
#[test]
#[ignore = "requires THEPC CUDA driver/runtime and NVRTC"]
fn cuda_backend_reports_cache_miss_then_hit_when_reused() -> Result<()> {
    let backend = CudaBackend::default();
    let first = backend.run(&input(), &config())?;
    let second = backend.run(&input(), &config())?;

    assert_eq!(first.diagnostics["cuda_cache_hit"], false);
    assert_eq!(second.diagnostics["cuda_cache_hit"], true);
    assert_eq!(first.diagnostics["gpu"], second.diagnostics["gpu"]);
    Ok(())
}
```

- [ ] **Step 2: Update current test construction**

Replace `let backend = CudaBackend;` with `let backend = CudaBackend::default();` in the CUDA tests so the tests match the stateful backend API.

- [ ] **Step 3: Verify RED on THEPC**

Run from the Mac after syncing this test-only commit to THEPC:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "source ~/.profile && cd ~/polymarket/rust && cargo test -p polymarket-probability-cuda --release cuda_backend_reports_cache_miss_then_hit_when_reused -- --ignored --nocapture"'
```

Expected: FAIL because `cuda_cache_hit` is missing from diagnostics.

- [ ] **Step 4: Commit**

```bash
git add rust/crates/polymarket-probability-cuda/tests/monte_carlo.rs
git commit -m "Test CUDA backend cache diagnostics"
```

## Task 2: Stateful CUDA Backend

**Files:**
- Modify: `rust/crates/polymarket-probability-cuda/src/lib.rs`
- Modify: `rust/crates/polymarket-probability-cuda/examples/benchmark_cuda.rs`

- [ ] **Step 1: Replace unit backend with stateful backend**

Use this structure:

```rust
#[derive(Debug, Default)]
pub struct CudaBackend {
    runtime: Mutex<Option<CudaRuntime>>,
}

#[derive(Debug)]
struct CudaRuntime {
    stream: Arc<CudaStream>,
    function: CudaFunction,
    gpu: serde_json::Value,
}
```

The module is kept alive through `CudaFunction`, which owns an `Arc<CudaModule>`.

- [ ] **Step 2: Move CUDA setup into `CudaRuntime::new`**

`CudaRuntime::new` compiles `MONTE_CARLO_KERNEL`, creates `CudaContext::new(0)`, loads the module, loads `simulate_monte_carlo`, creates the default stream, and captures GPU metadata once.

- [ ] **Step 3: Reuse runtime in `CudaBackend::run`**

Inside `run`, lock `self.runtime`. If it is empty, create the runtime and record `cuda_cache_hit = false`; otherwise record `cuda_cache_hit = true`. Launch the kernel through the cached runtime.

- [ ] **Step 4: Add diagnostics**

Every CUDA `SimulationRun` diagnostics object must include:

```json
{
  "cuda_cache_hit": true,
  "gpu": {
    "device_ordinal": 0,
    "name": "NVIDIA ...",
    "compute_capability": "12.0"
  }
}
```

First run reports `cuda_cache_hit: false`; later runs report `true`.

- [ ] **Step 5: Update benchmark example**

Use `let backend = CudaBackend::default();` and keep the backend outside the iteration loop.

- [ ] **Step 6: Verify GREEN**

Run locally:

```bash
cd rust && cargo fmt --check && cargo test -p polymarket-probability-cuda
```

Run on THEPC:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "source ~/.profile && cd ~/polymarket/rust && cargo test -p polymarket-probability-cuda --release -- --ignored --nocapture"'
```

- [ ] **Step 7: Commit**

```bash
git add rust/crates/polymarket-probability-cuda/src/lib.rs rust/crates/polymarket-probability-cuda/examples/benchmark_cuda.rs rust/crates/polymarket-probability-cuda/tests/monte_carlo.rs
git commit -m "Reuse CUDA Monte Carlo runtime"
```

## Task 3: Empirical Prior Pure Module

**Files:**
- Create: `src/polymarket_engine/probability/empirical_prior.py`
- Create: `tests/probability/test_empirical_prior.py`
- Modify: `src/polymarket_engine/probability/__init__.py`

- [ ] **Step 1: Write failing tests**

Add tests that prove:

```python
def test_empirical_prior_uses_only_chainlink_ticks_observed_by_asof() -> None:
    ...
    assert output.diagnostics["asof_safe"] is True
    assert output.diagnostics["excluded_future_tick_count"] == 1
    assert output.diagnostics["generator"] == "empirical_conditional_prior"
```

```python
def test_empirical_prior_falls_back_to_lognormal_when_bucket_is_sparse() -> None:
    ...
    assert output.diagnostics["generator"] == "lognormal_fallback"
    assert output.diagnostics["prior_fallback_level"] == "lognormal"
```

```python
def test_empirical_prior_rescales_historical_residuals_by_current_sigma_tau() -> None:
    ...
    assert output.diagnostics["sigma_scaled"] is True
    assert output.diagnostics["prior_bucket_size"] >= 2
```

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/probability/test_empirical_prior.py -q
```

Expected: FAIL with `ModuleNotFoundError` for `polymarket_engine.probability.empirical_prior`.

- [ ] **Step 3: Implement dataclasses and helpers**

Create:

```python
CHAINLINK_SOURCE_KEY = "polymarket_rtds_chainlink"

@dataclass(frozen=True)
class EmpiricalPriorConfig:
    min_bucket_size: int = 8
    history_limit: int = 2_000
    sigma_floor: float = 1e-9
```

Create pure helpers for symbol mapping, as-of filtering, fragment extraction, residual normalization, seeded sampling, and strict diagnostics.

- [ ] **Step 4: Implement `run_empirical_conditional_monte_carlo`**

Signature:

```python
def run_empirical_conditional_monte_carlo(
    probability_input: ProbabilityInput,
    *,
    price_ticks: Sequence[PriceObservation],
    path_count: int,
    steps: int,
    seed: int,
    config: EmpiricalPriorConfig | None = None,
) -> ProbabilityOutput:
```

Rules:

- keep only Chainlink rows for `BTC/USD` or `ETH/USD`;
- keep only rows with `event_ts <= asof_ts` and `observed_ts <= asof_ts`;
- create contiguous fragments of length `steps + 1`;
- calculate historical aggregate sigma as `sqrt(sum(step_return ** 2))`;
- normalize cumulative residuals by historical sigma;
- rescale residuals by current `probability_input.sigma_tau`;
- score paths with existing `score_paths`;
- if fragments are fewer than `min_bucket_size`, call `run_seeded_monte_carlo` and wrap diagnostics with sparse-prior fallback metadata.

- [ ] **Step 5: Verify GREEN**

```bash
uv run pytest tests/probability/test_empirical_prior.py -q
```

- [ ] **Step 6: Commit**

```bash
git add src/polymarket_engine/probability/empirical_prior.py src/polymarket_engine/probability/__init__.py tests/probability/test_empirical_prior.py
git commit -m "Add empirical Chainlink prior generator"
```

## Task 4: Runtime Integration

**Files:**
- Modify: `src/polymarket_engine/probability/runtime.py`
- Create: `tests/probability/test_probability_runtime_empirical_prior.py`

- [ ] **Step 1: Write failing runtime test**

Create a focused temp-DuckDB test that:

- applies schema;
- inserts a valid contract and as-of state;
- inserts enough Chainlink BTC/USD rows before `asof_ts`;
- sets `POLYMARKET_PROBABILITY_GENERATOR=empirical_conditional`;
- calls `compute_and_persist_probability_outputs`;
- reads `features.probability_outputs.output_json`;
- asserts diagnostics include `generator == "empirical_conditional_prior"` and `asof_safe is True`.

- [ ] **Step 2: Verify RED**

```bash
uv run pytest tests/probability/test_probability_runtime_empirical_prior.py -q
```

Expected: FAIL because runtime ignores `POLYMARKET_PROBABILITY_GENERATOR`.

- [ ] **Step 3: Add runtime generator selection**

Add:

```python
generator = os.environ.get("POLYMARKET_PROBABILITY_GENERATOR", "lognormal")
```

For `empirical_conditional`, fetch Chainlink price history through `store.price_ticks_before` with asset-to-symbol mapping and env-configured history/min-bucket defaults, then call `run_empirical_conditional_monte_carlo`.

- [ ] **Step 4: Keep existing default unchanged**

When `POLYMARKET_PROBABILITY_GENERATOR` is absent or equals `lognormal`, continue using `run_native_or_python` exactly as before.

- [ ] **Step 5: Verify GREEN**

```bash
uv run pytest tests/probability/test_probability_runtime_empirical_prior.py tests/probability/test_monte_carlo.py tests/probability/test_native_probability.py -q
```

- [ ] **Step 6: Commit**

```bash
git add src/polymarket_engine/probability/runtime.py tests/probability/test_probability_runtime_empirical_prior.py
git commit -m "Route probability runtime to empirical prior generator"
```

## Task 5: Benchmark and Report

**Files:**
- Modify: `docs/reports/monte-carlo-backend-benchmark-2026-06-05.md`

- [ ] **Step 1: Sync branch to THEPC**

Create a bundle, copy it to `/home/ender/polymarket.bundle`, and reset THEPC to the current branch commit.

- [ ] **Step 2: Run THEPC CUDA tests**

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "source ~/.profile && cd ~/polymarket/rust && cargo test -p polymarket-probability-cuda --release -- --ignored --nocapture"'
```

- [ ] **Step 3: Run THEPC benchmark**

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "source ~/.profile && cd ~/polymarket && ./scripts/benchmark_monte_carlo_backends.sh"'
```

- [ ] **Step 4: Copy report back and commit**

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cat ~/polymarket/docs/reports/monte-carlo-backend-benchmark-2026-06-05.md"' > docs/reports/monte-carlo-backend-benchmark-2026-06-05.md
git add docs/reports/monte-carlo-backend-benchmark-2026-06-05.md
git commit -m "Update THEPC Monte Carlo benchmark after CUDA reuse"
```

## Final Verification

- [ ] Run Python focused tests:

```bash
uv run pytest tests/probability/test_empirical_prior.py tests/probability/test_probability_runtime_empirical_prior.py tests/probability/test_monte_carlo.py tests/probability/test_native_probability.py -q
```

- [ ] Run Rust local tests:

```bash
cd rust && cargo fmt --check && cargo test -p polymarket-probability-cuda && cargo test -p polymarket-probability-core
```

- [ ] Run lint/type checks if touched Python imports affect package surface:

```bash
uv run ruff check src/polymarket_engine/probability tests/probability
uv run mypy src/polymarket_engine/probability tests/probability
```

- [ ] Confirm THEPC branch is clean and synced to final commit:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd ~/polymarket && git status --short --branch && git rev-parse HEAD"'
```

## Self-Review

- Spec coverage: CUDA runtime reuse, cache diagnostics, empirical prior, as-of safety, sparse fallback, and THEPC benchmark are all covered.
- Placeholder scan: no placeholder markers remain.
- Type consistency: Python uses `ProbabilityInput`, `ProbabilityOutput`, and `PriceObservation`; Rust keeps the existing `SimulationBackend::run(&self, ...)` trait and uses interior mutability for cached CUDA state.
