# Soft CPU Target Offload Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Spoon the default CPU authority, keep THEPC as GPU/API authority by default, and make THEPC probability work adapt toward a 15-20% soft CPU target instead of only reporting a target value.

**Architecture:** Keep one writer per artifact. Spoon owns collector, normalizer, hot probability inputs, probability fragments, volatility, and outcomes; THEPC owns API, CUDA/ensemble probability outputs, and probability events. The GPU probability worker keeps its Docker CPU quota loose, measures per-cycle process CPU usage, and adapts the next cycle's total Monte Carlo path budget down or up to target sustained 15-20% CPU without hiding stale inputs or suppressing diagnostics.

**Tech Stack:** Python 3.14, pytest, Docker Compose overlays, Bash deploy scripts, FastAPI runtime status JSON, existing Polymarket probability worker and cluster artifact-sync helpers.

---

## Current Answer

This is not already fully happening.

Current main already has:

- `POLYMARKET_PROBABILITY_CPU_TARGET_PERCENT`
- `POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS`
- `POLYMARKET_GPU_WORKER_CPUS`
- budget diagnostics in `probabilities.json`
- Spoon/THEPC compose overlays
- a manual cluster artifact sync command

Current main does not have:

- measured CPU percent from the probability worker process
- adaptive path-budget changes based on measured CPU
- a deploy default that makes THEPC GPU/API-only
- a deploy default that installs/runs the Spoon-to-THEPC artifact sync loop
- outcome status freshness when no outcome rows change

The existing `cpu_target_percent` value is mostly metadata today. It is not a controller.

## File Structure

- Create `src/polymarket_engine/probability/cpu_budget.py`
  - Pure soft-target controller helpers.
  - Computes cycle CPU percent from process CPU seconds and wall seconds.
  - Computes next effective total path budget from prior effective budget and measured CPU.

- Modify `src/polymarket_engine/probability/gpu_worker.py`
  - Add new budget fields for soft max CPU percent and minimum total paths.
  - Add measured CPU diagnostics to `budget`.
  - In loop mode, carry an adaptive `effective_max_total_paths` into the next cycle.
  - Keep `--once` deterministic with no prior adaptive state.

- Modify `src/polymarket_engine/cli.py`
  - Add CLI args for `--cpu-soft-max-percent` and `--min-total-paths`.
  - Pass those args into `ProbabilityWorkerBudget`.

- Modify `deploy/gpu/gpu-probability-entrypoint.sh`
  - Read `POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT`.
  - Read `POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS`.
  - Pass both into `polymarket-engine run-cuda-probability-worker`.

- Modify `deploy/collector/docker-compose.yml`
  - Add environment entries for the new soft CPU variables.
  - Keep `POLYMARKET_GPU_WORKER_CPUS` default loose at `1.0`.

- Modify `deploy/collector/.env.example`
  - Set `POLYMARKET_PROBABILITY_CPU_TARGET_PERCENT=15.0`.
  - Add `POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT=20.0`.
  - Add `POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS=4000`.
  - Keep `POLYMARKET_GPU_WORKER_CPUS=1.0`.
  - Keep `POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS=40000`.

- Modify `scripts/deploy.sh`
  - Add `POLYMARKET_DEPLOY_ROLE`, default `spoon-cpu-authority`.
  - Use `docker-compose.spoon-cpu-authority.yml` by default.
  - Start only Spoon-owned services by default.
  - Preserve an explicit `POLYMARKET_DEPLOY_ROLE=full` escape hatch.

- Modify `scripts/deploy_pc.sh`
  - Add `PC_DEPLOY_ROLE`, default `thepc-gpu-api`.
  - Use `docker-compose.thepc-gpu-api.yml` by default.
  - Stop THEPC collector, normalizer, and outcome-refresh by default.
  - Set THEPC soft CPU env defaults.
  - Install and enable the Spoon artifact sync loop by default.

- Create `scripts/install_thepc_spoon_artifact_sync.sh`
  - Installs `/home/ender/bin/polymarket-sync-spoon-artifacts.sh`.
  - Adds or updates the WSL SSH `Host spoon` alias.
  - Installs a user-systemd service when available.
  - Falls back to a single nohup loop when user-systemd is unavailable.

- Modify `src/polymarket_engine/validation/outcomes.py`
  - Ensure outcome status can be rewritten with a fresh `generated_at` even when no DB rows changed.

- Modify `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
  - When outcome refresh is enabled, write a fresh `outcomes.json` status snapshot even if `upsert_official_market_outcomes` returns `0`.

- Modify `docs/SPOON_DEPLOYMENT.md`
  - Document Spoon CPU authority as the default deploy role.
  - Document THEPC GPU/API authority as the default PC deploy role.
  - Document soft CPU target semantics: target is adaptive, not a hard Docker cap.

- Tests:
  - Create `tests/probability/test_cpu_budget.py`.
  - Modify `tests/probability/test_gpu_worker.py`.
  - Modify `tests/test_cli.py`.
  - Modify `tests/scripts/test_deploy_script.py`.
  - Create `tests/scripts/test_thepc_spoon_artifact_sync_script.py`.
  - Modify `tests/ingestion/test_rust_normalizer_sidecar.py`.
  - Modify `tests/validation/test_outcomes.py`.
  - Modify `tests/docs/test_active_runtime_docs.py`.

---

### Task 1: Add Pure Soft CPU Budget Helpers

**Files:**
- Create: `src/polymarket_engine/probability/cpu_budget.py`
- Create: `tests/probability/test_cpu_budget.py`

- [ ] **Step 1: Write failing tests for CPU percent measurement and path-budget adjustment**

Create `tests/probability/test_cpu_budget.py`:

```python
from __future__ import annotations

from polymarket_engine.probability.cpu_budget import CpuBudgetAdjustment
from polymarket_engine.probability.cpu_budget import adjust_total_path_budget
from polymarket_engine.probability.cpu_budget import cycle_cpu_percent


def test_cycle_cpu_percent_uses_process_cpu_over_wall_time() -> None:
    assert cycle_cpu_percent(
        start_process_seconds=10.0,
        end_process_seconds=10.15,
        start_monotonic_seconds=100.0,
        end_monotonic_seconds=101.0,
    ) == 15.0


def test_cycle_cpu_percent_returns_none_for_zero_wall_time() -> None:
    assert (
        cycle_cpu_percent(
            start_process_seconds=10.0,
            end_process_seconds=10.15,
            start_monotonic_seconds=100.0,
            end_monotonic_seconds=100.0,
        )
        is None
    )


def test_adjust_total_path_budget_reduces_after_soft_max_breach() -> None:
    decision = adjust_total_path_budget(
        current_total_paths=40_000,
        configured_max_total_paths=40_000,
        min_total_paths=4_000,
        cpu_percent=25.0,
        target_percent=15.0,
        soft_max_percent=20.0,
    )

    assert decision == CpuBudgetAdjustment(
        next_total_paths=28_000,
        reason="cpu_above_soft_max",
        cpu_percent=25.0,
        target_percent=15.0,
        soft_max_percent=20.0,
    )


def test_adjust_total_path_budget_increases_slowly_below_target() -> None:
    decision = adjust_total_path_budget(
        current_total_paths=10_000,
        configured_max_total_paths=40_000,
        min_total_paths=4_000,
        cpu_percent=9.0,
        target_percent=15.0,
        soft_max_percent=20.0,
    )

    assert decision.next_total_paths == 11_500
    assert decision.reason == "cpu_below_target"


def test_adjust_total_path_budget_stays_inside_target_band() -> None:
    decision = adjust_total_path_budget(
        current_total_paths=10_000,
        configured_max_total_paths=40_000,
        min_total_paths=4_000,
        cpu_percent=16.0,
        target_percent=15.0,
        soft_max_percent=20.0,
    )

    assert decision.next_total_paths == 10_000
    assert decision.reason == "cpu_inside_band"


def test_adjust_total_path_budget_respects_minimum_and_ceiling() -> None:
    low = adjust_total_path_budget(
        current_total_paths=4_100,
        configured_max_total_paths=40_000,
        min_total_paths=4_000,
        cpu_percent=50.0,
        target_percent=15.0,
        soft_max_percent=20.0,
    )
    high = adjust_total_path_budget(
        current_total_paths=39_000,
        configured_max_total_paths=40_000,
        min_total_paths=4_000,
        cpu_percent=4.0,
        target_percent=15.0,
        soft_max_percent=20.0,
    )

    assert low.next_total_paths == 4_000
    assert high.next_total_paths == 40_000
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
uv run pytest tests/probability/test_cpu_budget.py -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'polymarket_engine.probability.cpu_budget'`.

- [ ] **Step 3: Implement the helper module**

Create `src/polymarket_engine/probability/cpu_budget.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CpuBudgetAdjustment:
    next_total_paths: int
    reason: str
    cpu_percent: float | None
    target_percent: float
    soft_max_percent: float


def cycle_cpu_percent(
    *,
    start_process_seconds: float,
    end_process_seconds: float,
    start_monotonic_seconds: float,
    end_monotonic_seconds: float,
) -> float | None:
    wall_seconds = end_monotonic_seconds - start_monotonic_seconds
    if wall_seconds <= 0:
        return None
    process_seconds = max(0.0, end_process_seconds - start_process_seconds)
    return round((process_seconds / wall_seconds) * 100.0, 3)


def adjust_total_path_budget(
    *,
    current_total_paths: int,
    configured_max_total_paths: int,
    min_total_paths: int,
    cpu_percent: float | None,
    target_percent: float,
    soft_max_percent: float,
) -> CpuBudgetAdjustment:
    if configured_max_total_paths <= 0:
        raise ValueError("configured_max_total_paths must be positive")
    if min_total_paths <= 0:
        raise ValueError("min_total_paths must be positive")
    if min_total_paths > configured_max_total_paths:
        raise ValueError("min_total_paths must be <= configured_max_total_paths")
    if target_percent <= 0:
        raise ValueError("target_percent must be positive")
    if soft_max_percent < target_percent:
        raise ValueError("soft_max_percent must be >= target_percent")

    bounded_current = min(configured_max_total_paths, max(min_total_paths, current_total_paths))
    if cpu_percent is None:
        return CpuBudgetAdjustment(
            next_total_paths=bounded_current,
            reason="cpu_unmeasured",
            cpu_percent=None,
            target_percent=target_percent,
            soft_max_percent=soft_max_percent,
        )

    if cpu_percent > soft_max_percent:
        next_total_paths = max(min_total_paths, int(bounded_current * 0.70))
        return CpuBudgetAdjustment(
            next_total_paths=next_total_paths,
            reason="cpu_above_soft_max",
            cpu_percent=cpu_percent,
            target_percent=target_percent,
            soft_max_percent=soft_max_percent,
        )

    if cpu_percent < target_percent * 0.80 and bounded_current < configured_max_total_paths:
        next_total_paths = min(configured_max_total_paths, int(bounded_current * 1.15))
        return CpuBudgetAdjustment(
            next_total_paths=next_total_paths,
            reason="cpu_below_target",
            cpu_percent=cpu_percent,
            target_percent=target_percent,
            soft_max_percent=soft_max_percent,
        )

    return CpuBudgetAdjustment(
        next_total_paths=bounded_current,
        reason="cpu_inside_band",
        cpu_percent=cpu_percent,
        target_percent=target_percent,
        soft_max_percent=soft_max_percent,
    )
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
uv run pytest tests/probability/test_cpu_budget.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/probability/cpu_budget.py tests/probability/test_cpu_budget.py
git commit -m "feat(probability): add soft CPU budget helper"
```

---

### Task 2: Wire Adaptive CPU Budget Into The Probability Worker

**Files:**
- Modify: `src/polymarket_engine/probability/gpu_worker.py`
- Modify: `src/polymarket_engine/cli.py`
- Modify: `deploy/gpu/gpu-probability-entrypoint.sh`
- Modify: `deploy/collector/docker-compose.yml`
- Modify: `deploy/collector/.env.example`
- Modify: `tests/probability/test_gpu_worker.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/scripts/test_deploy_script.py`

- [ ] **Step 1: Write failing worker-budget tests**

Append to `tests/probability/test_gpu_worker.py`:

```python
def test_worker_budget_includes_soft_cpu_limits() -> None:
    budget = ProbabilityWorkerBudget(
        cpu_target_percent=15.0,
        cpu_soft_max_percent=20.0,
        min_total_paths=4_000,
        max_total_paths=40_000,
    )

    assert budget.cpu_target_percent == 15.0
    assert budget.cpu_soft_max_percent == 20.0
    assert budget.min_total_paths == 4_000


def test_worker_budget_rejects_soft_max_below_target() -> None:
    with pytest.raises(ValueError, match="cpu_soft_max_percent"):
        ProbabilityWorkerBudget(
            cpu_target_percent=20.0,
            cpu_soft_max_percent=15.0,
        )


def test_worker_budget_rejects_min_paths_above_max_paths() -> None:
    with pytest.raises(ValueError, match="min_total_paths"):
        ProbabilityWorkerBudget(
            min_total_paths=50_000,
            max_total_paths=40_000,
        )
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
uv run pytest tests/probability/test_gpu_worker.py::test_worker_budget_includes_soft_cpu_limits tests/probability/test_gpu_worker.py::test_worker_budget_rejects_soft_max_below_target tests/probability/test_gpu_worker.py::test_worker_budget_rejects_min_paths_above_max_paths -q
```

Expected: FAIL with `TypeError: ProbabilityWorkerBudget.__init__() got an unexpected keyword argument 'cpu_soft_max_percent'`.

- [ ] **Step 3: Extend `ProbabilityWorkerBudget`**

In `src/polymarket_engine/probability/gpu_worker.py`, update defaults and dataclass:

```python
DEFAULT_CPU_TARGET_PERCENT = 15.0
DEFAULT_CPU_SOFT_MAX_PERCENT = 20.0
DEFAULT_MAX_RSS_MB = 512
DEFAULT_MAX_CYCLE_RUNTIME_MS = 750
DEFAULT_MAX_TOTAL_PATHS = 40_000
DEFAULT_MIN_TOTAL_PATHS = 4_000
```

Update `ProbabilityWorkerBudget`:

```python
@dataclass(frozen=True)
class ProbabilityWorkerBudget:
    worker_mode: str = DEFAULT_WORKER_MODE
    generator_policy: str = DEFAULT_GENERATOR_POLICY
    cpu_target_percent: float = DEFAULT_CPU_TARGET_PERCENT
    cpu_soft_max_percent: float = DEFAULT_CPU_SOFT_MAX_PERCENT
    max_rss_mb: int = DEFAULT_MAX_RSS_MB
    max_cycle_runtime_ms: int = DEFAULT_MAX_CYCLE_RUNTIME_MS
    max_total_paths: int = DEFAULT_MAX_TOTAL_PATHS
    min_total_paths: int = DEFAULT_MIN_TOTAL_PATHS
    sustained_breach_cycles: int = DEFAULT_SUSTAINED_BREACH_CYCLES
    fragment_max_rows: int = DEFAULT_FRAGMENT_MAX_ROWS
    cpu_threads: int = DEFAULT_CPU_THREADS

    def __post_init__(self) -> None:
        if self.worker_mode == "":
            raise ValueError("worker_mode must not be empty")
        if self.generator_policy == "":
            raise ValueError("generator_policy must not be empty")
        if self.cpu_target_percent <= 0:
            raise ValueError("cpu_target_percent must be positive")
        if self.cpu_soft_max_percent < self.cpu_target_percent:
            raise ValueError("cpu_soft_max_percent must be >= cpu_target_percent")
        if self.max_rss_mb <= 0:
            raise ValueError("max_rss_mb must be positive")
        if self.max_cycle_runtime_ms <= 0:
            raise ValueError("max_cycle_runtime_ms must be positive")
        if self.max_total_paths <= 0:
            raise ValueError("max_total_paths must be positive")
        if self.min_total_paths <= 0 or self.min_total_paths > self.max_total_paths:
            raise ValueError("min_total_paths must be positive and <= max_total_paths")
        if self.sustained_breach_cycles <= 0:
            raise ValueError("sustained_breach_cycles must be positive")
        if self.fragment_max_rows <= 0:
            raise ValueError("fragment_max_rows must be positive")
        if self.cpu_threads <= 0:
            raise ValueError("cpu_threads must be positive")
```

- [ ] **Step 4: Run worker-budget tests to verify GREEN**

Run:

```bash
uv run pytest tests/probability/test_gpu_worker.py::test_worker_budget_includes_soft_cpu_limits tests/probability/test_gpu_worker.py::test_worker_budget_rejects_soft_max_below_target tests/probability/test_gpu_worker.py::test_worker_budget_rejects_min_paths_above_max_paths -q
```

Expected: PASS.

- [ ] **Step 5: Write failing adaptive-loop test**

Append to `tests/probability/test_gpu_worker.py`:

```python
def test_probability_loop_adapts_next_cycle_path_budget_from_cpu(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polymarket_engine.probability.gpu_worker import run_cuda_probability_worker_loop

    observed_budgets: list[int] = []

    def fake_cycle(**kwargs: object) -> dict[str, object]:
        budget = kwargs["budget"]
        observed_budgets.append(budget.max_total_paths)
        if len(observed_budgets) >= 2:
            raise KeyboardInterrupt
        return {
            "ok": True,
            "budget": {
                "cpu_percent": 25.0,
                "effective_max_total_paths": budget.max_total_paths,
            },
        }

    sleeps: list[float] = []
    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_cuda_probability_worker_cycle",
        fake_cycle,
    )
    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.time.sleep",
        sleeps.append,
    )

    with pytest.raises(KeyboardInterrupt):
        run_cuda_probability_worker_loop(
            duckdb_path=tmp_path / "unused.duckdb",
            probability_status_path=tmp_path / "probabilities.json",
            interval_seconds=0.01,
            budget=ProbabilityWorkerBudget(
                max_total_paths=40_000,
                min_total_paths=4_000,
                cpu_target_percent=15.0,
                cpu_soft_max_percent=20.0,
            ),
        )

    assert observed_budgets == [40_000, 28_000]
    assert sleeps == [0.01]
```

- [ ] **Step 6: Run adaptive-loop test to verify RED**

Run:

```bash
uv run pytest tests/probability/test_gpu_worker.py::test_probability_loop_adapts_next_cycle_path_budget_from_cpu -q
```

Expected: FAIL because loop mode always passes the original `ProbabilityWorkerBudget(max_total_paths=40000)` into each cycle.

- [ ] **Step 7: Add CPU diagnostics and adaptive next-cycle budget**

In `src/polymarket_engine/probability/gpu_worker.py`, import:

```python
from dataclasses import replace

from polymarket_engine.probability.cpu_budget import adjust_total_path_budget
from polymarket_engine.probability.cpu_budget import cycle_cpu_percent
```

Update `run_cuda_probability_worker_cycle` near `cycle_started_monotonic`:

```python
cycle_started_monotonic = time.monotonic()
cycle_started_process = time.process_time()
```

Update `_budget_diagnostics` signature:

```python
def _budget_diagnostics(
    *,
    budget: ProbabilityWorkerBudget,
    cycle_started_monotonic: float,
    cycle_started_process: float,
    requested_total_paths: int,
    allocated_total_paths: int,
    clamped_inputs: int,
    mc_input_skipped: int,
    path_budget_per_input: int,
) -> dict[str, Any]:
```

Inside `_budget_diagnostics`, compute and include CPU fields:

```python
elapsed_ms = round((time.monotonic() - cycle_started_monotonic) * 1000.0, 3)
cpu_percent = cycle_cpu_percent(
    start_process_seconds=cycle_started_process,
    end_process_seconds=time.process_time(),
    start_monotonic_seconds=cycle_started_monotonic,
    end_monotonic_seconds=time.monotonic(),
)
return {
    "worker_mode": budget.worker_mode,
    "generator_policy": budget.generator_policy,
    "cpu_target_percent": budget.cpu_target_percent,
    "cpu_soft_max_percent": budget.cpu_soft_max_percent,
    "cpu_percent": cpu_percent,
    "max_rss_mb": budget.max_rss_mb,
    "max_cycle_runtime_ms": budget.max_cycle_runtime_ms,
    "max_total_paths": budget.max_total_paths,
    "min_total_paths": budget.min_total_paths,
    "sustained_breach_cycles": budget.sustained_breach_cycles,
    "fragment_max_rows": budget.fragment_max_rows,
    "cpu_threads": budget.cpu_threads,
    "path_budget_per_input": path_budget_per_input,
    "requested_total_paths": requested_total_paths,
    "allocated_total_paths": allocated_total_paths,
    "clamped_inputs": clamped_inputs,
    "mc_input_skipped": mc_input_skipped,
    "elapsed_ms": elapsed_ms,
    "cycle_runtime_breached": elapsed_ms > budget.max_cycle_runtime_ms,
}
```

Update every `_budget_diagnostics(...)` call to pass `cycle_started_process=cycle_started_process`.

Update `run_cuda_probability_worker_loop`:

```python
effective_max_total_paths = budget.max_total_paths
while True:
    loop_budget = replace(budget, max_total_paths=effective_max_total_paths)
    try:
        payload = run_cuda_probability_worker_cycle(
            duckdb_path=duckdb_path,
            probability_status_path=probability_status_path,
            probability_inputs_path=probability_inputs_path,
            probability_fragments_path=probability_fragments_path,
            limit=limit,
            valid_seconds=valid_seconds,
            max_state_age_seconds=max_state_age_seconds,
            max_input_snapshot_age_seconds=max_input_snapshot_age_seconds,
            budget=loop_budget,
        )
        print(json.dumps(payload, sort_keys=True), flush=True)
        budget_payload = payload.get("budget")
        cpu_percent = None
        if isinstance(budget_payload, Mapping):
            raw_cpu_percent = budget_payload.get("cpu_percent")
            if raw_cpu_percent is not None:
                cpu_percent = float(raw_cpu_percent)
        adjustment = adjust_total_path_budget(
            current_total_paths=effective_max_total_paths,
            configured_max_total_paths=budget.max_total_paths,
            min_total_paths=budget.min_total_paths,
            cpu_percent=cpu_percent,
            target_percent=budget.cpu_target_percent,
            soft_max_percent=budget.cpu_soft_max_percent,
        )
        effective_max_total_paths = adjustment.next_total_paths
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "state": "ERROR",
                    "error": f"{type(exc).__name__}: {exc}",
                },
                sort_keys=True,
            ),
            flush=True,
        )
    time.sleep(interval_seconds)
```

- [ ] **Step 8: Run adaptive-loop test to verify GREEN**

Run:

```bash
uv run pytest tests/probability/test_gpu_worker.py::test_probability_loop_adapts_next_cycle_path_budget_from_cpu -q
```

Expected: PASS.

- [ ] **Step 9: Write failing CLI/deploy default tests**

Modify `tests/test_cli.py` near existing `run-cuda-probability-worker` parse tests:

```python
def test_parse_run_cuda_probability_worker_soft_cpu_defaults() -> None:
    args = parse_args(["run-cuda-probability-worker"])

    assert args.cpu_target_percent == 15.0
    assert args.cpu_soft_max_percent == 20.0
    assert args.min_total_paths == 4000
```

Modify `tests/scripts/test_deploy_script.py` in the GPU deploy/env assertions:

```python
assert "POLYMARKET_PROBABILITY_CPU_TARGET_PERCENT=15.0" in env_example
assert "POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT=20.0" in env_example
assert "POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS=4000" in env_example
assert "POLYMARKET_GPU_WORKER_CPUS=1.0" in env_example
assert "POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT:" in compose
assert "POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS:" in compose
assert '--cpu-soft-max-percent "$CPU_SOFT_MAX_PERCENT"' in entrypoint
assert '--min-total-paths "$MIN_TOTAL_PATHS"' in entrypoint
```

- [ ] **Step 10: Run CLI/deploy tests to verify RED**

Run:

```bash
uv run pytest tests/test_cli.py::test_parse_run_cuda_probability_worker_soft_cpu_defaults tests/scripts/test_deploy_script.py -q
```

Expected: FAIL because the new CLI args and env wiring do not exist.

- [ ] **Step 11: Wire CLI and deployment env**

In `src/polymarket_engine/cli.py`, add args on the `run-cuda-probability-worker` parser:

```python
cuda_worker.add_argument("--cpu-soft-max-percent", type=float, default=20.0)
cuda_worker.add_argument("--min-total-paths", type=int, default=4000)
```

Update `ProbabilityWorkerBudget(...)` construction:

```python
budget = ProbabilityWorkerBudget(
    worker_mode=args.worker_mode,
    generator_policy=args.generator_policy,
    cpu_target_percent=args.cpu_target_percent,
    cpu_soft_max_percent=args.cpu_soft_max_percent,
    max_rss_mb=args.max_rss_mb,
    max_cycle_runtime_ms=args.max_cycle_runtime_ms,
    max_total_paths=args.max_total_paths,
    min_total_paths=args.min_total_paths,
    sustained_breach_cycles=args.sustained_breach_cycles,
    fragment_max_rows=args.fragment_max_rows,
    cpu_threads=args.cpu_threads,
)
```

In `deploy/gpu/gpu-probability-entrypoint.sh`, add:

```sh
CPU_SOFT_MAX_PERCENT="${POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT:-20.0}"
MIN_TOTAL_PATHS="${POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS:-4000}"
```

Pass:

```sh
  --cpu-soft-max-percent "$CPU_SOFT_MAX_PERCENT" \
  --min-total-paths "$MIN_TOTAL_PATHS" \
```

In `deploy/collector/docker-compose.yml`, add under `gpu-probability-worker.environment`:

```yaml
      POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT: ${POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT:-20.0}
      POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS: ${POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS:-4000}
```

In `deploy/collector/.env.example`, update/add:

```dotenv
POLYMARKET_PROBABILITY_CPU_TARGET_PERCENT=15.0
POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT=20.0
POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS=4000
POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS=40000
POLYMARKET_GPU_WORKER_CPUS=1.0
```

- [ ] **Step 12: Run focused tests**

Run:

```bash
uv run pytest tests/probability/test_cpu_budget.py tests/probability/test_gpu_worker.py tests/test_cli.py::test_parse_run_cuda_probability_worker_soft_cpu_defaults tests/scripts/test_deploy_script.py -q
```

Expected: PASS.

- [ ] **Step 13: Commit**

```bash
git add src/polymarket_engine/probability/cpu_budget.py \
  src/polymarket_engine/probability/gpu_worker.py \
  src/polymarket_engine/cli.py \
  deploy/gpu/gpu-probability-entrypoint.sh \
  deploy/collector/docker-compose.yml \
  deploy/collector/.env.example \
  tests/probability/test_cpu_budget.py \
  tests/probability/test_gpu_worker.py \
  tests/test_cli.py \
  tests/scripts/test_deploy_script.py
git commit -m "feat(probability): adapt paths to soft CPU target"
```

---

### Task 3: Make Spoon/THEPC Role Split The Deploy Default

**Files:**
- Modify: `scripts/deploy.sh`
- Modify: `scripts/deploy_pc.sh`
- Create: `scripts/install_thepc_spoon_artifact_sync.sh`
- Modify: `tests/scripts/test_deploy_script.py`
- Create: `tests/scripts/test_thepc_spoon_artifact_sync_script.py`

- [ ] **Step 1: Write failing deploy-role tests**

Add to `tests/scripts/test_deploy_script.py`:

```python
def test_spoon_deploy_defaults_to_cpu_authority_overlay() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert 'DEPLOY_ROLE="${POLYMARKET_DEPLOY_ROLE:-spoon-cpu-authority}"' in script
    assert "docker-compose.spoon-cpu-authority.yml" in script
    assert "collector normalizer" in script
    assert "api gpu-probability-worker" not in script.split('DEPLOY_ROLE="${POLYMARKET_DEPLOY_ROLE:-spoon-cpu-authority}"', 1)[1].split('POLYMARKET_DEPLOY_ROLE=full', 1)[0]


def test_pc_deploy_defaults_to_gpu_api_overlay_and_sync() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert 'PC_DEPLOY_ROLE="${PC_DEPLOY_ROLE:-thepc-gpu-api}"' in script
    assert "docker-compose.thepc-gpu-api.yml" in script
    assert "stop collector normalizer outcome-refresh" in script
    assert "install_thepc_spoon_artifact_sync.sh" in script
    assert 'set_env POLYMARKET_PROBABILITY_CPU_TARGET_PERCENT "15.0"' in script
    assert 'set_env POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT "20.0"' in script
```

Create `tests/scripts/test_thepc_spoon_artifact_sync_script.py`:

```python
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_thepc_spoon_artifact_sync_installer_is_role_safe() -> None:
    script = (ROOT / "scripts" / "install_thepc_spoon_artifact_sync.sh").read_text(
        encoding="utf-8"
    )

    assert "status.json normalized_health.json probability_inputs.json probability_fragments.json outcomes.json volatility.json" in script
    assert "Host spoon" in script
    assert "HostName 100.126.126.1" in script
    assert "User spoon" in script
    assert "polymarket-spoon-artifact-sync.service" in script
    assert "systemctl --user enable --now polymarket-spoon-artifact-sync.service" in script
    assert "nohup bash -lc" in script
```

- [ ] **Step 2: Run deploy-role tests to verify RED**

Run:

```bash
uv run pytest tests/scripts/test_deploy_script.py::test_spoon_deploy_defaults_to_cpu_authority_overlay tests/scripts/test_deploy_script.py::test_pc_deploy_defaults_to_gpu_api_overlay_and_sync tests/scripts/test_thepc_spoon_artifact_sync_script.py -q
```

Expected: FAIL because `deploy.sh` and `deploy_pc.sh` still default to full-stack service starts and the sync installer file does not exist.

- [ ] **Step 3: Implement THEPC artifact sync installer**

Create `scripts/install_thepc_spoon_artifact_sync.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

SPOON_HOSTNAME="${SPOON_HOSTNAME:-100.126.126.1}"
SPOON_USER="${SPOON_USER:-spoon}"
THEPC_HOME="${THEPC_HOME:-$HOME}"
BIN_DIR="$THEPC_HOME/bin"
DATA_DIR="${POLYMARKET_DATA_DIR:-$THEPC_HOME/polymarket-data}"
LIVE_DIR="$DATA_DIR/live"
LOG_DIR="$DATA_DIR/logs"
SYNC_SCRIPT="$BIN_DIR/polymarket-sync-spoon-artifacts.sh"
SERVICE_DIR="$THEPC_HOME/.config/systemd/user"
SERVICE_PATH="$SERVICE_DIR/polymarket-spoon-artifact-sync.service"

mkdir -p "$BIN_DIR" "$LIVE_DIR" "$LOG_DIR" "$THEPC_HOME/.ssh"
chmod 700 "$THEPC_HOME/.ssh"
touch "$THEPC_HOME/.ssh/config"
chmod 600 "$THEPC_HOME/.ssh/config"

python3 - "$THEPC_HOME/.ssh/config" "$SPOON_HOSTNAME" "$SPOON_USER" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
hostname = sys.argv[2]
user = sys.argv[3]
text = path.read_text(encoding="utf-8") if path.exists() else ""
block = f"""
Host spoon
  HostName {hostname}
  User {user}
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
"""
lines = text.splitlines()
out: list[str] = []
skip = False
for line in lines:
    if line.strip().lower() == "host spoon":
        skip = True
        continue
    if skip and line.startswith("Host "):
        skip = False
    if not skip:
        out.append(line)
path.write_text("\n".join(out).rstrip() + block + "\n", encoding="utf-8")
PY

cat > "$SYNC_SCRIPT" <<'SH'
#!/usr/bin/env bash
set -euo pipefail

src="spoon:/home/spoon/polymarket-data/live"
dst="${POLYMARKET_DATA_DIR:-$HOME/polymarket-data}/live"
mkdir -p "$dst"
for file in status.json normalized_health.json probability_inputs.json probability_fragments.json outcomes.json volatility.json; do
  rsync -az --delay-updates --partial --timeout=5 "$src/$file" "$dst/$file"
done
SH
chmod 755 "$SYNC_SCRIPT"

if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
  mkdir -p "$SERVICE_DIR"
  cat > "$SERVICE_PATH" <<UNIT
[Unit]
Description=Polymarket Spoon artifact sync loop
After=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/bash -lc 'while true; do $SYNC_SCRIPT; sleep 1; done'
Restart=always
RestartSec=2

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now polymarket-spoon-artifact-sync.service
else
  if [ -f "$LIVE_DIR/artifact-sync.pid" ]; then
    old_pid="$(cat "$LIVE_DIR/artifact-sync.pid" || true)"
    if [ -n "$old_pid" ]; then
      kill "$old_pid" >/dev/null 2>&1 || true
    fi
  fi
  nohup bash -lc "while true; do $SYNC_SCRIPT; sleep 1; done" > "$LOG_DIR/artifact-sync.log" 2>&1 &
  echo "$!" > "$LIVE_DIR/artifact-sync.pid"
fi

"$SYNC_SCRIPT"
```

- [ ] **Step 4: Implement deploy role defaults**

In `scripts/deploy.sh`, add:

```bash
DEPLOY_ROLE="${POLYMARKET_DEPLOY_ROLE:-spoon-cpu-authority}"
SPOON_OVERLAY="$REPO/deploy/collector/docker-compose.spoon-cpu-authority.yml"
```

Replace direct `compose -f "$COMPOSE_FILE"` service starts with a helper:

```bash
compose_for_role() {
  case "$DEPLOY_ROLE" in
    spoon-cpu-authority)
      compose -f "$COMPOSE_FILE" -f "$SPOON_OVERLAY" "$@"
      ;;
    full)
      compose -f "$COMPOSE_FILE" "$@"
      ;;
    *)
      LOG "unsupported POLYMARKET_DEPLOY_ROLE=$DEPLOY_ROLE"
      exit 2
      ;;
  esac
}
```

For the default role, start only:

```bash
compose_for_role up -d collector normalizer
```

Keep the full role path:

```bash
POLYMARKET_DEPLOY_ROLE=full
compose_for_role up -d collector normalizer api gpu-probability-worker
```

In `scripts/deploy_pc.sh`, add near defaults:

```bash
PC_DEPLOY_ROLE="${PC_DEPLOY_ROLE:-thepc-gpu-api}"
PC_PROBABILITY_CPU_TARGET_PERCENT="${PC_PROBABILITY_CPU_TARGET_PERCENT:-15.0}"
PC_PROBABILITY_CPU_SOFT_MAX_PERCENT="${PC_PROBABILITY_CPU_SOFT_MAX_PERCENT:-20.0}"
PC_PROBABILITY_MIN_TOTAL_PATHS="${PC_PROBABILITY_MIN_TOTAL_PATHS:-4000}"
```

Pass and set env values inside the remote heredoc:

```bash
set_env POLYMARKET_PROBABILITY_CPU_TARGET_PERCENT "$PC_PROBABILITY_CPU_TARGET_PERCENT" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT "$PC_PROBABILITY_CPU_SOFT_MAX_PERCENT" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS "$PC_PROBABILITY_MIN_TOTAL_PATHS" deploy/collector/.env
```

Install sync:

```bash
./scripts/install_thepc_spoon_artifact_sync.sh
```

Start THEPC default role:

```bash
docker compose --env-file deploy/collector/.env \
  -f deploy/collector/docker-compose.yml \
  -f deploy/collector/docker-compose.thepc-gpu-api.yml \
  stop collector normalizer outcome-refresh >/dev/null 2>&1 || true

docker compose --env-file deploy/collector/.env \
  -f deploy/collector/docker-compose.yml \
  -f deploy/collector/docker-compose.thepc-gpu-api.yml \
  up -d --no-build api gpu-probability-worker
```

- [ ] **Step 5: Run deploy-role tests to verify GREEN**

Run:

```bash
uv run pytest tests/scripts/test_deploy_script.py tests/scripts/test_thepc_spoon_artifact_sync_script.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/deploy.sh \
  scripts/deploy_pc.sh \
  scripts/install_thepc_spoon_artifact_sync.sh \
  tests/scripts/test_deploy_script.py \
  tests/scripts/test_thepc_spoon_artifact_sync_script.py
git commit -m "feat(deploy): default to Spoon CPU and THEPC GPU roles"
```

---

### Task 4: Keep Outcomes Fresh Under Spoon Ownership

**Files:**
- Modify: `src/polymarket_engine/validation/outcomes.py`
- Modify: `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
- Modify: `tests/validation/test_outcomes.py`
- Modify: `tests/ingestion/test_rust_normalizer_sidecar.py`

- [ ] **Step 1: Write failing outcome status freshness test**

Add to `tests/validation/test_outcomes.py`:

```python
def test_write_outcome_history_status_rewrites_generated_at_when_rows_unchanged(
    tmp_path: Path,
) -> None:
    out_path = tmp_path / "live" / "outcomes.json"
    rows = [
        {
            "asset": "BTC",
            "market_id": "btc-updown-5m-1780502400",
            "market_slug": "btc-updown-5m-1780502400",
            "market": "BTC 5m",
            "interval": "5m",
            "start_ts": "2026-06-06T00:00:00+00:00",
            "expiry_ts": "2026-06-06T00:05:00+00:00",
            "threshold_price": 70000.0,
            "end_price": 70010.0,
            "computed_winner": None,
            "official_winner": None,
            "official_resolution_status": "pending",
            "winning_token_id": None,
        }
    ]

    write_outcome_history_status(out_path=out_path, rows=rows)
    first = json.loads(out_path.read_text(encoding="utf-8"))
    write_outcome_history_status(out_path=out_path, rows=rows)
    second = json.loads(out_path.read_text(encoding="utf-8"))

    assert second["generated_at"] >= first["generated_at"]
    assert second["rows"] == first["rows"]
```

Add to `tests/ingestion/test_rust_normalizer_sidecar.py`:

```python
def test_sidecar_outcome_refresh_rewrites_status_when_no_rows_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    outcome_path = tmp_path / "live" / "outcomes.json"
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    (raw_root / ".polymarket_archive_root").write_text("", encoding="utf-8")
    status_path.parent.mkdir()
    status_path.write_text(_state_manager_status_payload(), encoding="utf-8")
    outcome_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-outcome-runtime-v1",
                "state": "OK",
                "generated_at": "2026-06-06T00:00:00+00:00",
                "rows": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.normalize_rust_event_tree",
        lambda **_: (),
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.refresh_market_outcomes",
        lambda **_: 0,
    )

    result = run_rust_normalizer_cycle(
        raw_root=raw_root,
        db_path=tmp_path / "db.duckdb",
        status_path=status_path,
        normalized_health_path=health_path,
        outcome_status_path=outcome_path,
        refresh_outcomes=True,
    )

    payload = json.loads(outcome_path.read_text(encoding="utf-8"))
    assert result.market_outcomes_written == 0
    assert payload["generated_at"] != "2026-06-06T00:00:00+00:00"
```

- [ ] **Step 2: Run outcome tests to verify RED**

Run:

```bash
uv run pytest tests/validation/test_outcomes.py::test_write_outcome_history_status_rewrites_generated_at_when_rows_unchanged tests/ingestion/test_rust_normalizer_sidecar.py::test_sidecar_outcome_refresh_rewrites_status_when_no_rows_change -q
```

Expected: one test may pass if `write_outcome_history_status` already rewrites; the sidecar test should fail because a `0`-row refresh leaves the old status file unchanged.

- [ ] **Step 3: Implement fresh status rewrite on no-op refresh**

In `src/polymarket_engine/validation/outcomes.py`, keep `write_outcome_history_status(...)` as the status-file writer. Do not introduce a second schema.

In `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`, after `refresh_market_outcomes(...)` returns during a refresh cycle, always rebuild and write the status snapshot from the writer-owned DuckDB connection:

```python
if refresh_outcomes:
    market_outcomes_written = refresh_market_outcomes(
        store=store,
        out_path=outcome_status_path,
    )
    if market_outcomes_written == 0:
        rows = latest_market_outcome_rows_from_connection(
            conn=store.connection,
            limit=_official_outcome_output_limit_from_env(),
        )
        write_outcome_history_status(out_path=outcome_status_path, rows=rows)
else:
    market_outcomes_written = 0
```

If `DuckDbIngestStore` does not expose a reusable connection attribute, add a small helper in `rust_normalizer_sidecar.py` that calls the existing latest-row function through the current store context rather than opening a competing DuckDB connection.

- [ ] **Step 4: Run outcome tests to verify GREEN**

Run:

```bash
uv run pytest tests/validation/test_outcomes.py tests/ingestion/test_rust_normalizer_sidecar.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/validation/outcomes.py \
  src/polymarket_engine/ingestion/rust_normalizer_sidecar.py \
  tests/validation/test_outcomes.py \
  tests/ingestion/test_rust_normalizer_sidecar.py
git commit -m "fix(runtime): keep outcome status fresh on no-op refresh"
```

---

### Task 5: Document Defaults And Verify The Full Lane

**Files:**
- Modify: `docs/SPOON_DEPLOYMENT.md`
- Modify: `docs/PART_TWO_LIVE_COLLECTORS.md`
- Modify: `tests/docs/test_active_runtime_docs.py`

- [ ] **Step 1: Write failing docs tests**

Add to `tests/docs/test_active_runtime_docs.py`:

```python
def test_docs_describe_default_soft_cpu_split() -> None:
    text = (ROOT / "docs" / "SPOON_DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "Spoon CPU authority is the default deploy role" in text
    assert "THEPC GPU/API authority is the default PC deploy role" in text
    assert "soft CPU target" in text
    assert "POLYMARKET_PROBABILITY_CPU_TARGET_PERCENT=15.0" in text
    assert "POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT=20.0" in text
    assert "not a hard Docker CPU cap" in text
    assert "artifact sync loop" in text
```

- [ ] **Step 2: Run docs test to verify RED**

Run:

```bash
uv run pytest tests/docs/test_active_runtime_docs.py::test_docs_describe_default_soft_cpu_split -q
```

Expected: FAIL because the new default wording is absent.

- [ ] **Step 3: Update docs**

In `docs/SPOON_DEPLOYMENT.md`, under `CPU Authority / THEPC GPU Active-Active Split`, add:

```markdown
Spoon CPU authority is the default deploy role. `scripts/deploy.sh` uses
`POLYMARKET_DEPLOY_ROLE=spoon-cpu-authority` unless an operator explicitly sets
`POLYMARKET_DEPLOY_ROLE=full`.

THEPC GPU/API authority is the default PC deploy role. `scripts/deploy_pc.sh`
uses `PC_DEPLOY_ROLE=thepc-gpu-api`, starts only `api` and
`gpu-probability-worker`, stops THEPC `collector`, `normalizer`, and
`outcome-refresh`, and installs the artifact sync loop that pulls Spoon-owned
live artifacts.

THEPC probability CPU control is a soft CPU target, not a hard Docker CPU cap.
The default is `POLYMARKET_PROBABILITY_CPU_TARGET_PERCENT=15.0` with
`POLYMARKET_PROBABILITY_CPU_SOFT_MAX_PERCENT=20.0`. The worker measures
per-cycle process CPU and adapts its next total path budget between
`POLYMARKET_PROBABILITY_MIN_TOTAL_PATHS=4000` and
`POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS=40000`.
```

In `docs/PART_TWO_LIVE_COLLECTORS.md`, add one sentence to the probability runtime section:

```markdown
THEPC probability path count is adaptive under the soft CPU target; the target
changes path budget, not model authority or artifact ownership.
```

- [ ] **Step 4: Run docs tests to verify GREEN**

Run:

```bash
uv run pytest tests/docs/test_active_runtime_docs.py -q
```

Expected: PASS.

- [ ] **Step 5: Run focused verification**

Run:

```bash
uv run pytest \
  tests/probability/test_cpu_budget.py \
  tests/probability/test_gpu_worker.py \
  tests/test_cli.py::test_parse_run_cuda_probability_worker_soft_cpu_defaults \
  tests/scripts/test_deploy_script.py \
  tests/scripts/test_thepc_spoon_artifact_sync_script.py \
  tests/validation/test_outcomes.py \
  tests/ingestion/test_rust_normalizer_sidecar.py \
  tests/docs/test_active_runtime_docs.py \
  -q
```

Expected: PASS.

Run:

```bash
uv run ruff check \
  src/polymarket_engine/probability/cpu_budget.py \
  src/polymarket_engine/probability/gpu_worker.py \
  src/polymarket_engine/cli.py \
  src/polymarket_engine/validation/outcomes.py \
  src/polymarket_engine/ingestion/rust_normalizer_sidecar.py \
  tests/probability/test_cpu_budget.py \
  tests/probability/test_gpu_worker.py \
  tests/test_cli.py \
  tests/scripts/test_deploy_script.py \
  tests/scripts/test_thepc_spoon_artifact_sync_script.py \
  tests/validation/test_outcomes.py \
  tests/ingestion/test_rust_normalizer_sidecar.py \
  tests/docs/test_active_runtime_docs.py
```

Expected: PASS.

- [ ] **Step 6: Commit docs and final test wiring**

```bash
git add docs/SPOON_DEPLOYMENT.md \
  docs/PART_TWO_LIVE_COLLECTORS.md \
  tests/docs/test_active_runtime_docs.py
git commit -m "docs(runtime): document soft CPU offload defaults"
```

---

### Task 6: Deploy And Verify Live Defaults

**Files:**
- No source edits.

- [ ] **Step 1: Push main after local verification**

Run:

```bash
git status --short --branch
git log --oneline -5
git push origin main
```

Expected:

- Worktree is clean before push.
- Push updates only `main`.

- [ ] **Step 2: Deploy Spoon default CPU authority**

Run:

```bash
ssh spoon 'cd /home/spoon/polymarket-main && git fetch origin main && git reset --hard origin/main'
ssh spoon 'cd /home/spoon/polymarket-main && POLYMARKET_DEPLOY_ROLE=spoon-cpu-authority DEPLOY_FORCE=1 POLYMARKET_DEPLOY_USE_PREBUILT=1 POLYMARKET_EXPECTED_DEPLOY_SHA=$(git rev-parse HEAD) ./scripts/deploy.sh'
```

Expected:

- Spoon runs `collector` and `normalizer`.
- Spoon does not run API or GPU probability worker in the default role.

- [ ] **Step 3: Deploy THEPC default GPU/API authority**

Run:

```bash
PC_DEPLOY_ROLE=thepc-gpu-api ./scripts/deploy_pc.sh
```

Expected:

- THEPC runs `api` and `gpu-probability-worker`.
- THEPC `collector`, `normalizer`, and `outcome-refresh` are stopped.
- THEPC artifact sync service is active.

- [ ] **Step 4: Verify live CPU/offload state**

Run:

```bash
ssh spoon 'cd /home/spoon/polymarket-main/deploy/collector && docker compose --env-file .env -f docker-compose.yml -f docker-compose.spoon-cpu-authority.yml ps'
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "systemctl --user is-active polymarket-spoon-artifact-sync.service && cd /home/ender/polymarket/deploy/collector && docker compose --env-file .env -f docker-compose.yml -f docker-compose.thepc-gpu-api.yml ps"'
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "python3 - <<'\''PY'\''
import json, urllib.request
data=json.load(urllib.request.urlopen(\"http://127.0.0.1:8000/api/runtime/probabilities?limit=8\", timeout=5))
print(data.get(\"ok\"), data.get(\"state\"), len(data.get(\"rows\") or []), data.get(\"budget\"))
PY"'
```

Expected:

- Spoon services are healthy.
- THEPC sync service is active.
- THEPC API/GPU services are running.
- Probability endpoint has BTC/ETH UP/DOWN rows.
- Probability payload budget includes `cpu_target_percent=15.0`, `cpu_soft_max_percent=20.0`, `cpu_percent`, and adaptive path budget values.

- [ ] **Step 5: Watch THEPC CPU settle**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd /home/ender/polymarket/deploy/collector && for i in $(seq 1 6); do docker stats --no-stream --format '\''{{.Name}} {{.CPUPerc}} {{.MemUsage}}'\'' $(docker compose --env-file .env -f docker-compose.yml -f docker-compose.thepc-gpu-api.yml ps -q); sleep 10; done"'
```

Expected:

- Brief spikes can happen.
- Sustained GPU probability worker CPU should trend toward the 15-20% soft target as the adaptive path budget backs off.
- If sustained CPU remains above 20% after multiple cycles, capture `probabilities.json` budget diagnostics and open a follow-up bug against the controller thresholds.

---

## Self-Review

- Spec coverage: plan covers the user request that Spoon should do most work by default, THEPC should default to GPU/API-only, THEPC CPU should use a 15-20% soft target, and the observed stale outcomes bug should be fixed under Spoon ownership.
- Placeholder scan: no `TBD`, `TODO`, or open-ended "add tests" placeholders remain.
- Type consistency: `cpu_soft_max_percent`, `min_total_paths`, `CpuBudgetAdjustment`, and `adjust_total_path_budget` are named consistently across planned source, tests, CLI, entrypoint, and docs.
- Safety: no real trading, signing, or order placement is added. The runtime remains read-only.
