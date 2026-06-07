# PC CUDA Monte Carlo Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move all live Monte Carlo probability simulation on THEPC to NVIDIA CUDA so the normalizer no longer spends CPU on Monte Carlo work.

**Architecture:** Keep the existing normalizer focused on raw event normalization and as-of state building. Add a separate PC-only CUDA probability worker that reads replay-safe probability inputs from DuckDB, runs CuPy-backed CUDA simulations, persists `features.probability_outputs`, and writes `data/live/probabilities.json` for the API/UI/TUI to read. Disable CPU probability fallback in the deployed PC runtime so missing CUDA is visible instead of hidden by CPU Monte Carlo.

**Tech Stack:** Python 3.11+, CuPy `cupy-cuda13x`, NVIDIA CUDA container runtime on THEPC WSL2, DuckDB, existing FastAPI runtime API, existing Docker Compose deployment.

---

## Sources And Current State

- CuPy official install docs list CUDA 13.x wheels via `pip install cupy-cuda13x` and support Python 3.10 through 3.14 plus CUDA Toolkit versions through 13.2.
- NVIDIA publishes CUDA container images through NGC/Docker Hub; THEPC has an NVIDIA driver that already reports CUDA 13.2 support.
- Current Python Monte Carlo lives in `src/polymarket_engine/probability/monte_carlo.py` and runs NumPy on CPU.
- Current runtime probability compute lives in `src/polymarket_engine/probability/runtime.py`.
- Current normalizer calls `compute_and_persist_probability_outputs(...)` when `--enable-probabilities` is set.
- Current API route `/api/runtime/probabilities` reads `data/live/probabilities.json` first, then falls back to CPU computation from DuckDB if the status file is missing and runtime probabilities are enabled.

## Non-Goals

- No CUDA execution on the Mac.
- No silent CPU fallback on THEPC live runtime.
- No real trading, signing, private keys, or order placement.
- No change to replay/as-of feature rules.
- No Rust/CUDA native rewrite in this pass. CuPy is enough to move the existing vectorized NumPy simulation to NVIDIA GPU.

## File Structure

Create:

- `src/polymarket_engine/probability/cuda_monte_carlo.py`
  - Lazily imports CuPy.
  - Runs all path generation, cumulative returns, threshold scoring, and counts on GPU.
  - Raises a clear `CudaUnavailable` error when CUDA is not usable.
- `src/polymarket_engine/probability/gpu_worker.py`
  - Reads latest active probability inputs from DuckDB.
  - Runs CUDA Monte Carlo for each active input.
  - Persists outputs and writes atomic probability status JSON.
  - Provides once and loop entrypoints.
- `tests/probability/test_cuda_monte_carlo.py`
  - CPU-safe import and CUDA-required smoke tests.
- `tests/probability/test_gpu_worker.py`
  - Worker behavior test with the CUDA function monkeypatched, so it can run on the Mac.
- `deploy/gpu/Dockerfile`
  - CUDA runtime image with Python, project dependencies, and `cupy-cuda13x`.
- `deploy/gpu/probability-worker-entrypoint.sh`
  - Container entrypoint for the CUDA worker.
- `scripts/thepc_cuda_preflight.sh`
  - THEPC-only preflight for NVIDIA driver, Docker GPU access, and CuPy.

Modify:

- `src/polymarket_engine/cli.py`
  - Add `run-cuda-probability-worker`.
- `src/polymarket_engine/app.py`
  - Thread through a runtime probability compute fallback flag.
- `src/polymarket_engine/runtime_api.py`
  - Return a missing/stale status envelope instead of CPU-computing when fallback is disabled.
- `deploy/normalizer/normalizer-entrypoint.sh`
  - Default `POLYMARKET_NORMALIZER_ENABLE_PROBABILITIES` to `0`.
- `deploy/collector/docker-compose.yml`
  - Add `gpu-probability` service.
  - Disable API CPU probability fallback in deployment.
  - Keep normalizer probability compute disabled by default.

## Runtime Policy

- Deployed THEPC Monte Carlo backend: `cuda_cupy`.
- Deployed THEPC path count default: `10000`.
- Deployed THEPC worker interval default: `1.0` second.
- API status file freshness threshold: `10` seconds.
- If CUDA is unavailable, the worker writes `state: "CUDA_UNAVAILABLE"` and exits non-zero in `--once` mode.
- In loop mode, CUDA failures write a visible status payload and retry after the configured interval.
- The API does not compute CPU probabilities when `POLYMARKET_ALLOW_RUNTIME_PROBABILITY_COMPUTE=0`.

## Task 1: Add THEPC CUDA Preflight

**Files:**

- Create: `scripts/thepc_cuda_preflight.sh`

- [ ] **Step 1: Write the preflight script**

Create `scripts/thepc_cuda_preflight.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "== Host =="
uname -a
if grep -qi microsoft /proc/version; then
  echo "wsl=true"
else
  echo "wsl=false"
fi

echo "== NVIDIA driver =="
command -v nvidia-smi
nvidia-smi --query-gpu=name,driver_version,compute_cap,memory.total --format=csv

echo "== Docker GPU access =="
if command -v docker >/dev/null 2>&1; then
  docker run --rm --gpus all nvidia/cuda:13.2.1-runtime-ubuntu24.04 nvidia-smi
else
  echo "docker=missing"
fi

echo "== Python CuPy CUDA smoke =="
python3 - <<'PY'
import importlib.util

if importlib.util.find_spec("cupy") is None:
    raise SystemExit("cupy=missing")

import cupy as cp

count = cp.cuda.runtime.getDeviceCount()
if count < 1:
    raise SystemExit("cuda_device_count=0")

props = cp.cuda.runtime.getDeviceProperties(0)
name = props.get("name", b"unknown")
if isinstance(name, bytes):
    name = name.decode("utf-8", "replace")

arr = cp.arange(8, dtype=cp.float64)
out = cp.asnumpy(arr * 2.0)
assert out.tolist() == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0]

print(f"cupy={cp.__version__}")
print(f"cuda_runtime={cp.cuda.runtime.runtimeGetVersion()}")
print(f"device={name}")
print("cupy_smoke=ok")
PY
```

- [ ] **Step 2: Make the script executable**

Run:

```bash
chmod +x scripts/thepc_cuda_preflight.sh
```

- [ ] **Step 3: Run preflight on THEPC**

Run from the Mac:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd ~/polymarket && ./scripts/thepc_cuda_preflight.sh"'
```

Expected after the GPU image is available:

```text
cupy_smoke=ok
```

- [ ] **Step 4: Commit**

```bash
git add scripts/thepc_cuda_preflight.sh
git commit -m "Add THEPC CUDA preflight"
```

## Task 2: Add CUDA Monte Carlo Backend

**Files:**

- Create: `src/polymarket_engine/probability/cuda_monte_carlo.py`
- Test: `tests/probability/test_cuda_monte_carlo.py`

- [ ] **Step 1: Write CUDA backend tests**

Create `tests/probability/test_cuda_monte_carlo.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from polymarket_engine.probability.cuda_monte_carlo import (
    CudaUnavailable,
    require_cuda_device,
    run_cuda_monte_carlo,
)
from polymarket_engine.probability.schema import ProbabilityInput


def _input(side: str = "UP", comparison_operator: str | None = None) -> ProbabilityInput:
    if comparison_operator is None:
        comparison_operator = ">=" if side == "UP" else "<"
    return ProbabilityInput(
        state_id=f"state-{side}",
        asof_ts=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
        asset="BTC",
        side=side,
        comparison_operator=comparison_operator,
        seconds_left=60.0,
        settlement_price=100.0,
        threshold=100.0,
        sigma_tau=0.01,
        executable_price=0.5,
        source_age_ms=10,
        book_age_ms=10,
        z_path=0.0,
    )


def test_require_cuda_device_reports_unavailable_without_cupy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = __import__

    def fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "cupy":
            raise ImportError("cupy missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    with pytest.raises(CudaUnavailable, match="cupy"):
        require_cuda_device()


def test_cuda_backend_smoke_on_thepc() -> None:
    pytest.importorskip("cupy")
    require_cuda_device()

    output = run_cuda_monte_carlo(
        _input(),
        path_count=4096,
        steps=16,
        seed=123,
    )

    assert 0.0 <= output.p_finish <= 1.0
    assert 0.0 <= output.p_no_touch <= 1.0
    assert output.model_version == "cuda-cupy-lognormal-chainlink-sigma-v1"
    assert output.seed == 123
    assert output.diagnostics["backend"] == "cuda_cupy"
    assert output.diagnostics["path_count"] == 4096
    assert output.diagnostics["steps"] == 16
    assert output.diagnostics["cuda_device"]


def test_cuda_backend_handles_down_contract_on_thepc() -> None:
    pytest.importorskip("cupy")
    require_cuda_device()

    output = run_cuda_monte_carlo(
        _input(side="DOWN", comparison_operator="<"),
        path_count=4096,
        steps=16,
        seed=456,
    )

    assert 0.0 <= output.p_finish <= 1.0
    assert 0.0 <= output.p_no_touch <= 1.0
    assert output.diagnostics["backend"] == "cuda_cupy"
```

- [ ] **Step 2: Run the import-only test on Mac and verify CUDA tests skip**

Run:

```bash
uv run pytest tests/probability/test_cuda_monte_carlo.py::test_require_cuda_device_reports_unavailable_without_cupy -q
```

Expected:

```text
1 passed
```

- [ ] **Step 3: Implement CUDA backend**

Create `src/polymarket_engine/probability/cuda_monte_carlo.py`:

```python
from __future__ import annotations

import math
from typing import Any

from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput


class CudaUnavailable(RuntimeError):
    """Raised when NVIDIA CUDA Monte Carlo cannot run on this host."""


def require_cuda_device() -> dict[str, Any]:
    cp = _cupy()
    try:
        count = int(cp.cuda.runtime.getDeviceCount())
    except Exception as exc:
        raise CudaUnavailable(f"CUDA device query failed: {type(exc).__name__}: {exc}") from exc
    if count < 1:
        raise CudaUnavailable("CUDA device query returned zero devices")

    props = cp.cuda.runtime.getDeviceProperties(0)
    name = props.get("name", b"unknown")
    if isinstance(name, bytes):
        name = name.decode("utf-8", "replace")
    return {
        "device_count": count,
        "device_id": 0,
        "name": str(name),
        "runtime_version": int(cp.cuda.runtime.runtimeGetVersion()),
        "cupy_version": str(cp.__version__),
    }


def run_cuda_monte_carlo(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
) -> ProbabilityOutput:
    _require_positive_int(path_count, "path_count")
    _require_positive_int(steps, "steps")
    device_info = require_cuda_device()
    cp = _cupy()

    with cp.cuda.Device(0):
        start = cp.cuda.Event()
        end = cp.cuda.Event()
        start.record()

        rng = cp.random.default_rng(seed)
        per_step_sigma = probability_input.sigma_tau / math.sqrt(steps)
        log_returns = rng.normal(
            0.0,
            per_step_sigma,
            size=(path_count, steps),
        ).astype(cp.float64, copy=False)
        cumulative_returns = cp.cumsum(log_returns, axis=1)
        simulated_prices = probability_input.settlement_price * cp.exp(cumulative_returns)
        valid_prices = cp.isfinite(simulated_prices) & (simulated_prices > 0.0)
        if not bool(cp.all(valid_prices).get()):
            raise ValueError("CUDA path prices must be positive and finite")

        terminal_prices = simulated_prices[:, -1]
        terminal_wins = _satisfies_contract(cp, probability_input, terminal_prices)
        step_wins = _satisfies_contract(cp, probability_input, simulated_prices)
        start_win = bool(
            _satisfies_contract(
                cp,
                probability_input,
                cp.asarray([probability_input.settlement_price], dtype=cp.float64),
            )[0].get()
        )
        no_touch_wins = cp.all(step_wins, axis=1)
        if not start_win:
            no_touch_wins = cp.zeros(path_count, dtype=cp.bool_)

        finish_count = int(cp.count_nonzero(terminal_wins).get())
        no_touch_count = int(cp.count_nonzero(no_touch_wins).get())

        end.record()
        end.synchronize()
        elapsed_ms = float(cp.cuda.get_elapsed_time(start, end))
        cp.get_default_memory_pool().free_all_blocks()

    return ProbabilityOutput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        p_finish=finish_count / path_count,
        p_no_touch=no_touch_count / path_count,
        z_path=probability_input.z_path,
        model_version="cuda-cupy-lognormal-chainlink-sigma-v1",
        seed=seed,
        diagnostics={
            "backend": "cuda_cupy",
            "path_count": path_count,
            "steps": steps,
            "elapsed_ms": elapsed_ms,
            "per_step_sigma": per_step_sigma,
            "cuda_device": device_info["name"],
            "cuda_runtime_version": device_info["runtime_version"],
            "cupy_version": device_info["cupy_version"],
        },
    )


def _satisfies_contract(cp: Any, probability_input: ProbabilityInput, prices: Any) -> Any:
    threshold = probability_input.threshold
    if probability_input.comparison_operator == ">":
        return prices > threshold
    if probability_input.comparison_operator == ">=":
        return prices >= threshold
    if probability_input.comparison_operator == "<":
        return prices < threshold
    if probability_input.comparison_operator == "<=":
        return prices <= threshold
    raise ValueError("unsupported comparison_operator")


def _cupy() -> Any:
    try:
        import cupy as cp
    except ImportError as exc:
        raise CudaUnavailable("cupy is not installed; install cupy-cuda13x on THEPC") from exc
    return cp


def _require_positive_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
```

- [ ] **Step 4: Run local non-CUDA verification**

Run:

```bash
uv run pytest tests/probability/test_cuda_monte_carlo.py::test_require_cuda_device_reports_unavailable_without_cupy -q
uv run ruff check src/polymarket_engine/probability/cuda_monte_carlo.py tests/probability/test_cuda_monte_carlo.py
```

Expected:

```text
1 passed
All checks passed!
```

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/probability/cuda_monte_carlo.py tests/probability/test_cuda_monte_carlo.py
git commit -m "Add CUDA Monte Carlo backend"
```

## Task 3: Add CUDA Probability Worker

**Files:**

- Create: `src/polymarket_engine/probability/gpu_worker.py`
- Test: `tests/probability/test_gpu_worker.py`

- [ ] **Step 1: Write worker test**

Create `tests/probability/test_gpu_worker.py`:

```python
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import DecisionState
from polymarket_engine.probability.gpu_worker import CudaProbabilityWorkerConfig
from polymarket_engine.probability.gpu_worker import run_cuda_probability_worker_once
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def test_cuda_probability_worker_persists_outputs_and_writes_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    status_path = tmp_path / "live" / "probabilities.json"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = _decision_state()
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)

    def fake_cuda(
        probability_input: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
    ) -> ProbabilityOutput:
        return ProbabilityOutput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            p_finish=0.61,
            p_no_touch=0.57,
            z_path=probability_input.z_path,
            model_version="cuda-cupy-lognormal-chainlink-sigma-v1",
            seed=seed,
            diagnostics={
                "backend": "cuda_cupy",
                "path_count": path_count,
                "steps": steps,
                "elapsed_ms": 2.5,
                "cuda_device": "fixture-gpu",
            },
        )

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_cuda_monte_carlo",
        fake_cuda,
    )

    payload = run_cuda_probability_worker_once(
        CudaProbabilityWorkerConfig(
            duckdb_path=db_path,
            status_path=status_path,
            limit=4,
            path_count=10000,
            max_state_age_seconds=600.0,
            active_only=True,
        )
    )

    assert payload["schema_version"] == "polymarket-probability-runtime-v1"
    assert payload["ok"] is True
    assert payload["state"] == "OK"
    assert payload["backend"] == "cuda_cupy"
    assert payload["rows"][0]["contract"] == "BTC 5m UP"
    assert payload["rows"][0]["p_finish"] == 0.61
    assert payload["rows"][0]["diagnostics"]["backend"] == "cuda_cupy"
    assert payload["rows"][0]["path_count"] == 10000
    saved = json.loads(status_path.read_text(encoding="utf-8"))
    assert saved["rows"][0]["output_id"] == payload["rows"][0]["output_id"]
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from features.probability_outputs").fetchone() == (1,)


def _decision_state() -> DecisionState:
    asof_ts = datetime(2026, 6, 5, 12, 2, tzinfo=UTC)
    start_ts = datetime(2026, 6, 5, 12, 0, tzinfo=UTC)
    expiry_ts = datetime(2026, 6, 5, 12, 5, tzinfo=UTC)
    contract = ContractSpec(
        contract_id="btc-up",
        market_id="market-btc",
        token_id="token-btc-up",
        asset="BTC",
        side="UP",
        interval="5m",
        start_ts=start_ts,
        expiry_ts=expiry_ts,
        threshold=100.0,
        threshold_type="above",
        comparison_operator=">=",
        settlement_source_name="chainlink_data_streams",
        settlement_source_url="https://data.chain.link/streams/btc-usd",
        settlement_symbol="BTC/USD",
        rule_text="BTC resolves up if final settlement price is above 100.",
        rule_hash="rule-hash",
    )
    return DecisionState(
        state_id="state-btc-up",
        asof_ts=asof_ts,
        contract=contract,
        seconds_left=180.0,
        threshold=100.0,
        threshold_event_ts=asof_ts,
        threshold_observed_ts=asof_ts,
        settlement_price=101.0,
        settlement_source_key="polymarket_rtds_chainlink",
        settlement_event_ts=asof_ts,
        settlement_observed_ts=asof_ts,
        book_event_ts=asof_ts,
        book_observed_ts=asof_ts,
        best_bid=0.5,
        best_ask=0.52,
        bid_size_top=10.0,
        ask_size_top=11.0,
        spread=0.02,
        executable_price=0.52,
        source_age_ms=100,
        book_age_ms=120,
        sigma_tau=0.01,
        proxy_prices={},
        data_quality_flags=(),
    )
```

- [ ] **Step 2: Run test and verify it fails because module is missing**

Run:

```bash
uv run pytest tests/probability/test_gpu_worker.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'polymarket_engine.probability.gpu_worker'
```

- [ ] **Step 3: Implement worker**

Create `src/polymarket_engine/probability/gpu_worker.py`:

```python
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from polymarket_engine.probability.cuda_monte_carlo import CudaUnavailable
from polymarket_engine.probability.cuda_monte_carlo import run_cuda_monte_carlo
from polymarket_engine.probability.runtime import (
    _output_id,
    _runtime_row,
    _seed_for_input,
    _steps_for_input,
    latest_probability_inputs,
)
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


PROBABILITY_STATUS_SCHEMA_VERSION = "polymarket-probability-runtime-v1"


@dataclass(frozen=True)
class CudaProbabilityWorkerConfig:
    duckdb_path: Path
    status_path: Path
    limit: int = 8
    path_count: int = 10000
    max_state_age_seconds: float = 600.0
    active_only: bool = True
    interval_seconds: float = 1.0


def run_cuda_probability_worker_once(config: CudaProbabilityWorkerConfig) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc)
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    skipped = 0
    try:
        inputs, skipped = latest_probability_inputs(
            duckdb_path=config.duckdb_path,
            limit=config.limit,
            max_state_age_seconds=config.max_state_age_seconds,
            active_only=config.active_only,
        )
        store = DuckDbIngestStore(config.duckdb_path)
        for runtime_input in inputs:
            probability_input = runtime_input.probability_input
            try:
                output = run_cuda_monte_carlo(
                    probability_input,
                    path_count=config.path_count,
                    steps=_steps_for_input(probability_input),
                    seed=_seed_for_input(probability_input),
                )
                output_id = _output_id(probability_input, output)
                store.insert_probability_output(
                    output_id=output_id,
                    probability_input=probability_input,
                    output=output,
                )
                row = _runtime_row(runtime_input, output=output, output_id=output_id)
                row["diagnostics"] = dict(output.diagnostics)
                row["backend"] = output.diagnostics.get("backend")
                row["path_count"] = output.diagnostics.get("path_count")
                row["steps"] = output.diagnostics.get("steps")
                rows.append(row)
            except (CudaUnavailable, ValueError) as exc:
                errors.append(f"{probability_input.state_id}: {type(exc).__name__}: {exc}")
    except Exception as exc:
        errors.append(f"worker: {type(exc).__name__}: {exc}")

    state = "OK"
    if errors and rows:
        state = "PARTIAL"
    elif errors:
        state = "CUDA_UNAVAILABLE" if any("CudaUnavailable" in error for error in errors) else "ERROR"

    payload = {
        "schema_version": PROBABILITY_STATUS_SCHEMA_VERSION,
        "ok": not errors,
        "state": state,
        "backend": "cuda_cupy",
        "generated_at": generated_at.isoformat(),
        "cached": False,
        "model_version": rows[0]["model_version"] if rows else None,
        "rows": rows,
        "skipped": skipped,
        "errors": errors,
    }
    _write_atomic_json(config.status_path, payload)
    return payload


def run_cuda_probability_worker_loop(config: CudaProbabilityWorkerConfig) -> None:
    while True:
        started = time.monotonic()
        payload = run_cuda_probability_worker_once(config)
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)
        elapsed = time.monotonic() - started
        time.sleep(max(0.0, config.interval_seconds - elapsed))


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    tmp_path.replace(path)
```

- [ ] **Step 4: Run worker tests**

Run:

```bash
uv run pytest tests/probability/test_gpu_worker.py -q
uv run ruff check src/polymarket_engine/probability/gpu_worker.py tests/probability/test_gpu_worker.py
```

Expected:

```text
1 passed
All checks passed!
```

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/probability/gpu_worker.py tests/probability/test_gpu_worker.py
git commit -m "Add CUDA probability worker"
```

## Task 4: Add CLI Command For CUDA Worker

**Files:**

- Modify: `src/polymarket_engine/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add CLI parser test**

Append to `tests/test_cli.py`:

```python
def test_parse_run_cuda_probability_worker_args() -> None:
    args = parse_args(
        [
            "run-cuda-probability-worker",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--probability-status-path",
            "data/live/probabilities.json",
            "--path-count",
            "10000",
            "--interval-seconds",
            "1.0",
            "--limit",
            "8",
            "--once",
        ]
    )

    assert args.command == "run-cuda-probability-worker"
    assert str(args.duckdb_path) == "data/db/polymarket.duckdb"
    assert str(args.probability_status_path) == "data/live/probabilities.json"
    assert args.path_count == 10000
    assert args.interval_seconds == 1.0
    assert args.limit == 8
    assert args.once is True
```

- [ ] **Step 2: Run parser test and verify it fails**

Run:

```bash
uv run pytest tests/test_cli.py::test_parse_run_cuda_probability_worker_args -q
```

Expected:

```text
invalid choice: 'run-cuda-probability-worker'
```

- [ ] **Step 3: Add parser branch**

Modify `src/polymarket_engine/cli.py` inside `parse_args(...)`, after the normalizer sidecar parser:

```python
    cuda_worker = subparsers.add_parser("run-cuda-probability-worker")
    cuda_worker.add_argument("--duckdb-path", type=Path, required=True)
    cuda_worker.add_argument(
        "--probability-status-path",
        type=Path,
        default=Path("data/live/probabilities.json"),
    )
    cuda_worker.add_argument("--interval-seconds", type=float, default=1.0)
    cuda_worker.add_argument("--limit", type=int, default=8)
    cuda_worker.add_argument("--path-count", type=int, default=10000)
    cuda_worker.add_argument(
        "--max-state-age-seconds",
        type=float,
        default=600.0,
    )
    cuda_worker.add_argument(
        "--include-expired",
        action="store_true",
        help="Include expired rows; live deployment should leave this disabled.",
    )
    cuda_worker.add_argument(
        "--once",
        action="store_true",
        help="Run one CUDA probability worker cycle and exit.",
    )
```

Modify `main(...)` command dispatch:

```python
    if args.command == "run-cuda-probability-worker":
        return _run_cuda_probability_worker(args)
```

Add this function below `_run_rust_normalizer_sidecar(...)`:

```python
def _run_cuda_probability_worker(args: argparse.Namespace) -> int:
    from polymarket_engine.probability.gpu_worker import CudaProbabilityWorkerConfig
    from polymarket_engine.probability.gpu_worker import (
        run_cuda_probability_worker_loop,
        run_cuda_probability_worker_once,
    )

    config = CudaProbabilityWorkerConfig(
        duckdb_path=args.duckdb_path,
        status_path=args.probability_status_path,
        limit=args.limit,
        path_count=args.path_count,
        max_state_age_seconds=args.max_state_age_seconds,
        active_only=not args.include_expired,
        interval_seconds=args.interval_seconds,
    )
    if args.once:
        payload = run_cuda_probability_worker_once(config)
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0 if payload.get("ok") else 1
    run_cuda_probability_worker_loop(config)
    return 0
```

- [ ] **Step 4: Run CLI test**

Run:

```bash
uv run pytest tests/test_cli.py::test_parse_run_cuda_probability_worker_args -q
uv run ruff check src/polymarket_engine/cli.py tests/test_cli.py
```

Expected:

```text
1 passed
All checks passed!
```

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/cli.py tests/test_cli.py
git commit -m "Add CUDA probability worker CLI"
```

## Task 5: Disable API CPU Probability Fallback In PC Runtime

**Files:**

- Modify: `src/polymarket_engine/runtime_api.py`
- Modify: `src/polymarket_engine/app.py`
- Test: `tests/test_runtime_api.py`

- [ ] **Step 1: Add API fallback-disabled test**

Append to `tests/test_runtime_api.py`:

```python
def test_runtime_probabilities_can_disable_cpu_compute_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = _decision_state()
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)

    def fail_compute(*_: object, **__: object) -> NoReturn:
        raise AssertionError("API must not CPU-compute probabilities when fallback is disabled")

    monkeypatch.setattr(
        "polymarket_engine.probability.runtime._compute_and_persist_rows",
        fail_compute,
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=db_path,
        probability_status_path=tmp_path / "missing-probabilities.json",
        enable_runtime_probabilities=True,
        allow_runtime_probability_compute=False,
    )

    response = TestClient(app).get("/api/runtime/probabilities?limit=4")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "MISSING"
    assert payload["rows"] == []
    assert "probability status file missing" in payload["errors"][0]


def test_runtime_probabilities_marks_stale_status_file_when_fallback_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probability_status_path = tmp_path / "live" / "probabilities.json"
    probability_status_path.parent.mkdir()
    probability_status_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-probability-runtime-v1",
                "ok": True,
                "state": "OK",
                "backend": "cuda_cupy",
                "generated_at": datetime.now(UTC).isoformat(),
                "cached": False,
                "model_version": "cuda-cupy-lognormal-chainlink-sigma-v1",
                "rows": [{"contract": "BTC 5m UP", "output_id": "btc-up"}],
                "skipped": 0,
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "polymarket_engine.runtime_api.time.time",
        lambda: probability_status_path.stat().st_mtime + 30.0,
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=tmp_path / "missing.duckdb",
        probability_status_path=probability_status_path,
        enable_runtime_probabilities=True,
        allow_runtime_probability_compute=False,
    )

    response = TestClient(app).get("/api/runtime/probabilities?limit=4")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "STALE"
    assert payload["backend"] == "cuda_cupy"
    assert payload["rows"] == [{"contract": "BTC 5m UP", "output_id": "btc-up"}]
    assert "probability status file stale" in payload["errors"][0]
```

- [ ] **Step 2: Run test and verify it fails on unexpected keyword**

Run:

```bash
uv run pytest tests/test_runtime_api.py::test_runtime_probabilities_can_disable_cpu_compute_fallback tests/test_runtime_api.py::test_runtime_probabilities_marks_stale_status_file_when_fallback_disabled -q
```

Expected:

```text
TypeError: create_app() got an unexpected keyword argument 'allow_runtime_probability_compute'
```

- [ ] **Step 3: Add fallback flag to runtime API**

Modify `build_runtime_router(...)` in `src/polymarket_engine/runtime_api.py`:

```python
    enable_runtime_probabilities: bool = False,
    allow_probability_compute_fallback: bool = True,
    probability_status_max_age_seconds: float = 10.0,
) -> APIRouter:
```

Modify the status-file branch in the `/probabilities` route after `rows` is confirmed to be a list:

```python
            status_age_seconds = max(0.0, time.time() - probability_status_path.stat().st_mtime)
            limited = dict(payload)
            limited["rows"] = rows[:limit]
            limited["cached"] = False
            limited["status_age_seconds"] = status_age_seconds
            if status_age_seconds > probability_status_max_age_seconds:
                error = (
                    "probability status file stale: "
                    f"{status_age_seconds:.1f}s old at {probability_status_path}"
                )
                limited["ok"] = False
                limited["state"] = "STALE"
                limited["error"] = error
                existing_errors = limited.get("errors", [])
                limited["errors"] = [error, *existing_errors] if isinstance(existing_errors, list) else [error]
            return limited
```

Remove the old status-file return block:

```python
            limited = dict(payload)
            limited["rows"] = rows[:limit]
            limited["cached"] = False
            return limited
```

Modify the `/probabilities` route just before `try: return probability_cache.payload(...)`:

```python
        if not allow_probability_compute_fallback:
            error = f"probability status file missing: {probability_status_path}"
            return {
                "ok": False,
                "state": "MISSING",
                "error": error,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "cached": False,
                "model_version": None,
                "rows": [],
                "skipped": 0,
                "errors": [error],
            }
```

- [ ] **Step 4: Add env helper and app plumbing**

Modify imports from `polymarket_engine.runtime_api` in `src/polymarket_engine/app.py`:

```python
from polymarket_engine.runtime_api import (
    build_runtime_router,
    container_status_enabled_from_env,
    runtime_probabilities_enabled_from_env,
    runtime_probability_compute_fallback_enabled_from_env,
)
```

Modify `create_app(...)` signature:

```python
    enable_runtime_probabilities: bool | None = None,
    allow_runtime_probability_compute: bool | None = None,
) -> FastAPI:
```

Pass the flag to `build_runtime_router(...)`:

```python
            allow_probability_compute_fallback=runtime_probability_compute_fallback_enabled_from_env()
            if allow_runtime_probability_compute is None
            else allow_runtime_probability_compute,
```

Add this helper to `src/polymarket_engine/runtime_api.py` near the existing env helpers:

```python
def runtime_probability_compute_fallback_enabled_from_env() -> bool:
    return os.getenv("POLYMARKET_ALLOW_RUNTIME_PROBABILITY_COMPUTE", "1") == "1"
```

- [ ] **Step 5: Run fallback tests**

Run:

```bash
uv run pytest tests/test_runtime_api.py::test_runtime_probabilities_can_disable_cpu_compute_fallback tests/test_runtime_api.py::test_runtime_probabilities_marks_stale_status_file_when_fallback_disabled tests/test_runtime_api.py::test_runtime_probabilities_runs_cached_read_only_mc_and_persists_output -q
uv run ruff check src/polymarket_engine/runtime_api.py src/polymarket_engine/app.py tests/test_runtime_api.py
```

Expected:

```text
3 passed
All checks passed!
```

- [ ] **Step 6: Commit**

```bash
git add src/polymarket_engine/runtime_api.py src/polymarket_engine/app.py tests/test_runtime_api.py
git commit -m "Allow disabling runtime CPU probability fallback"
```

## Task 6: Add CUDA Worker Container And Compose Service

**Files:**

- Create: `deploy/gpu/Dockerfile`
- Create: `deploy/gpu/probability-worker-entrypoint.sh`
- Modify: `deploy/normalizer/normalizer-entrypoint.sh`
- Modify: `deploy/collector/docker-compose.yml`

- [ ] **Step 1: Add GPU Dockerfile**

Create `deploy/gpu/Dockerfile`:

```dockerfile
FROM nvidia/cuda:13.2.1-runtime-ubuntu24.04

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 \
        python3-pip \
        python3-venv \
        tini \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /venv
ENV PATH="/venv/bin:${PATH}"

COPY pyproject.toml README.md ./
COPY src ./src
COPY deploy/gpu/probability-worker-entrypoint.sh /usr/local/bin/probability-worker-entrypoint.sh

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir "cupy-cuda13x[ctk]" . \
    && chmod 755 /usr/local/bin/probability-worker-entrypoint.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/probability-worker-entrypoint.sh"]
```

- [ ] **Step 2: Add GPU worker entrypoint**

Create `deploy/gpu/probability-worker-entrypoint.sh`:

```sh
#!/usr/bin/env sh
set -eu

DB_PATH="${POLYMARKET_DUCKDB_PATH:-/var/lib/polymarket/db/polymarket.duckdb}"
PROBABILITY_STATUS_PATH="${POLYMARKET_PROBABILITY_STATUS_PATH:-/var/lib/polymarket/live/probabilities.json}"
INTERVAL_SECONDS="${POLYMARKET_CUDA_PROBABILITY_INTERVAL_SECONDS:-1.0}"
LIMIT="${POLYMARKET_CUDA_PROBABILITY_LIMIT:-8}"
PATH_COUNT="${POLYMARKET_CUDA_PROBABILITY_PATH_COUNT:-10000}"
MAX_STATE_AGE_SECONDS="${POLYMARKET_CUDA_PROBABILITY_MAX_STATE_AGE_SECONDS:-600.0}"

mkdir -p "$(dirname "$DB_PATH")" "$(dirname "$PROBABILITY_STATUS_PATH")"

exec polymarket-engine run-cuda-probability-worker \
  --duckdb-path "$DB_PATH" \
  --probability-status-path "$PROBABILITY_STATUS_PATH" \
  --interval-seconds "$INTERVAL_SECONDS" \
  --limit "$LIMIT" \
  --path-count "$PATH_COUNT" \
  --max-state-age-seconds "$MAX_STATE_AGE_SECONDS"
```

- [ ] **Step 3: Disable normalizer CPU probability default**

Modify `deploy/normalizer/normalizer-entrypoint.sh`:

```sh
ENABLE_PROBABILITIES="${POLYMARKET_NORMALIZER_ENABLE_PROBABILITIES:-0}"
```

- [ ] **Step 4: Add compose service and runtime env**

Modify `deploy/collector/docker-compose.yml`.

In `normalizer.environment`, add:

```yaml
      POLYMARKET_NORMALIZER_ENABLE_PROBABILITIES: ${POLYMARKET_NORMALIZER_ENABLE_PROBABILITIES:-0}
```

Add this service after `normalizer`:

```yaml
  gpu-probability:
    build:
      context: ../..
      dockerfile: deploy/gpu/Dockerfile
    image: ${POLYMARKET_GPU_PROBABILITY_IMAGE:-polymarket-gpu-probability:latest}
    restart: unless-stopped
    user: "${POLYMARKET_UID:-1000}:${POLYMARKET_GID:-1000}"
    depends_on:
      - normalizer
    gpus: all
    environment:
      TZ: ${POLYMARKET_DISPLAY_TZ:-America/Chicago}
      NVIDIA_VISIBLE_DEVICES: all
      NVIDIA_DRIVER_CAPABILITIES: compute,utility
      POLYMARKET_DUCKDB_PATH: /var/lib/polymarket/db/polymarket.duckdb
      POLYMARKET_PROBABILITY_STATUS_PATH: /var/lib/polymarket/live/probabilities.json
      POLYMARKET_CUDA_PROBABILITY_INTERVAL_SECONDS: ${POLYMARKET_CUDA_PROBABILITY_INTERVAL_SECONDS:-1.0}
      POLYMARKET_CUDA_PROBABILITY_LIMIT: ${POLYMARKET_CUDA_PROBABILITY_LIMIT:-8}
      POLYMARKET_CUDA_PROBABILITY_PATH_COUNT: ${POLYMARKET_CUDA_PROBABILITY_PATH_COUNT:-10000}
      POLYMARKET_CUDA_PROBABILITY_MAX_STATE_AGE_SECONDS: ${POLYMARKET_CUDA_PROBABILITY_MAX_STATE_AGE_SECONDS:-600.0}
    volumes:
      - ${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}/raw:/var/lib/polymarket/raw
      - ${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}/db:/var/lib/polymarket/db
      - ${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}/live:/var/lib/polymarket/live
      - ${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}/logs:/var/lib/polymarket/logs
    healthcheck:
      test:
        [
          "CMD",
          "python",
          "-c",
          "import json, os, time; p='/var/lib/polymarket/live/probabilities.json'; payload=json.load(open(p)); assert payload.get('schema_version')=='polymarket-probability-runtime-v1'; assert payload.get('backend')=='cuda_cupy'; assert time.time()-os.stat(p).st_mtime < 30",
        ]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s
```

In `api.environment`, add:

```yaml
      POLYMARKET_ALLOW_RUNTIME_PROBABILITY_COMPUTE: ${POLYMARKET_ALLOW_RUNTIME_PROBABILITY_COMPUTE:-0}
```

- [ ] **Step 5: Validate compose config locally**

Run:

```bash
docker compose -f deploy/collector/docker-compose.yml config >/tmp/polymarket-compose.yml
```

Expected:

```text
no output and exit code 0
```

- [ ] **Step 6: Commit**

```bash
git add deploy/gpu deploy/normalizer/normalizer-entrypoint.sh deploy/collector/docker-compose.yml
git commit -m "Add PC CUDA probability worker service"
```

## Task 7: Verify CUDA On THEPC

**Files:**

- No source edits.

- [ ] **Step 1: Deploy branch to THEPC**

Run from Mac:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd ~/polymarket && git fetch origin && git checkout <branch-name> && git pull --ff-only"'
```

Expected:

```text
Already up to date.
```

- [ ] **Step 2: Build GPU worker image on THEPC**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd ~/polymarket/deploy/collector && docker compose build gpu-probability"'
```

Expected:

```text
writing image
```

- [ ] **Step 3: Run THEPC CUDA preflight**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd ~/polymarket && ./scripts/thepc_cuda_preflight.sh"'
```

Expected:

```text
cupy_smoke=ok
```

- [ ] **Step 4: Run one CUDA probability worker cycle**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd ~/polymarket/deploy/collector && docker compose run --rm --entrypoint polymarket-engine gpu-probability run-cuda-probability-worker --duckdb-path /var/lib/polymarket/db/polymarket.duckdb --probability-status-path /var/lib/polymarket/live/probabilities.json --path-count 10000 --limit 8 --once"'
```

Expected:

```text
"backend":"cuda_cupy"
```

- [ ] **Step 5: Start deployed services**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd ~/polymarket/deploy/collector && POLYMARKET_ENABLE_RUNTIME_PROBABILITIES=1 POLYMARKET_ALLOW_RUNTIME_PROBABILITY_COMPUTE=0 docker compose up -d --build"'
```

Expected:

```text
Started
```

- [ ] **Step 6: Verify API reports CUDA rows**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "python3 - <<'"'"'PY'"'"'
import json, urllib.request
payload = json.loads(urllib.request.urlopen(\"http://127.0.0.1:8000/api/runtime/probabilities?limit=8\", timeout=10).read().decode())
print(json.dumps({\"state\": payload.get(\"state\"), \"backend\": payload.get(\"backend\"), \"rows\": len(payload.get(\"rows\", [])), \"errors\": payload.get(\"errors\")}, sort_keys=True))
for row in payload.get(\"rows\", []):
    print(row.get(\"asset\"), row.get(\"side\"), row.get(\"path_count\"), row.get(\"diagnostics\", {}).get(\"backend\"), row.get(\"diagnostics\", {}).get(\"elapsed_ms\"))
PY"'
```

Expected:

```text
"backend": "cuda_cupy"
```

- [ ] **Step 7: Verify CPU load is lower and GPU is active**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "docker stats --no-stream && nvidia-smi --query-gpu=name,utilization.gpu,memory.used,memory.total --format=csv"'
```

Expected:

```text
polymarket-rust-collector-normalizer-1
polymarket-rust-collector-gpu-probability-1
```

The normalizer CPU should be lower than the pre-change 200-400% spikes, and GPU utilization should show activity during worker cycles.

## Final Verification

Run local non-GPU checks:

```bash
uv run pytest tests/probability/test_cuda_monte_carlo.py::test_require_cuda_device_reports_unavailable_without_cupy tests/probability/test_gpu_worker.py tests/test_cli.py::test_parse_run_cuda_probability_worker_args tests/test_runtime_api.py::test_runtime_probabilities_can_disable_cpu_compute_fallback -q
uv run pytest tests/test_runtime_api.py::test_runtime_probabilities_marks_stale_status_file_when_fallback_disabled -q
uv run ruff check src/polymarket_engine/probability/cuda_monte_carlo.py src/polymarket_engine/probability/gpu_worker.py src/polymarket_engine/cli.py src/polymarket_engine/runtime_api.py src/polymarket_engine/app.py tests/probability/test_cuda_monte_carlo.py tests/probability/test_gpu_worker.py tests/test_cli.py tests/test_runtime_api.py
docker compose -f deploy/collector/docker-compose.yml config >/tmp/polymarket-compose.yml
```

Run THEPC GPU checks:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd ~/polymarket && ./scripts/thepc_cuda_preflight.sh"'
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd ~/polymarket/deploy/collector && docker compose exec gpu-probability python - <<'"'"'PY'"'"'
from datetime import UTC, datetime
from polymarket_engine.probability.cuda_monte_carlo import require_cuda_device, run_cuda_monte_carlo
from polymarket_engine.probability.schema import ProbabilityInput
print(require_cuda_device())
output = run_cuda_monte_carlo(
    ProbabilityInput(
        state_id=\"smoke\",
        asof_ts=datetime(2026, 6, 5, 12, 0, tzinfo=UTC),
        asset=\"BTC\",
        side=\"UP\",
        comparison_operator=\">\",
        seconds_left=60.0,
        settlement_price=100.0,
        threshold=100.0,
        sigma_tau=0.01,
        executable_price=0.5,
        source_age_ms=10,
        book_age_ms=10,
        z_path=0.0,
    ),
    path_count=4096,
    steps=16,
    seed=123,
)
print(output.model_version, output.diagnostics)
PY"'
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "curl -fsS http://127.0.0.1:8000/api/runtime/probabilities?limit=8"'
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "docker stats --no-stream && nvidia-smi"'
```

## Self-Review

- Spec coverage: the plan moves live PC Monte Carlo to CUDA, disables normalizer CPU MC, prevents API CPU fallback, and verifies on THEPC only.
- Incomplete markers: none found.
- Type consistency: `CudaProbabilityWorkerConfig`, `run_cuda_probability_worker_once`, `run_cuda_monte_carlo`, and `POLYMARKET_ALLOW_RUNTIME_PROBABILITY_COMPUTE` are named consistently across tests, CLI, deployment, and API.
- Risk: CuPy and the NVIDIA container runtime are only verified on THEPC. The Mac verification intentionally covers import safety, worker control flow, API fallback behavior, and Docker Compose shape without running CUDA.
