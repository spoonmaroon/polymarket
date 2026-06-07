# Probability Latency And Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce visible probability lag after fast contract repricing and persist enough forward-only simulation metadata to measure whether Monte Carlo caught, predicted, or missed each move.

**Architecture:** Split probability handling into a fast nowcast lane and a slower confirmation lane. The UI/API can show a sub-second nowcast from the latest as-of state while GPU/CPU Monte Carlo refreshes cached probabilities asynchronously; compact probability events and simulation summaries are persisted through the normalizer-owned DuckDB writer path so historical analysis no longer depends on transient JSON files. Preserve as-of safety by storing future movement only as labels, never as model input.

**Tech Stack:** Python 3.11+/DuckDB/FastAPI, existing normalizer sidecar, existing probability runtime, existing Rust collector/TUI, THEPC Docker Compose runtime, pytest/mypy/ruff.

---

## Pause And Drift Note

This plan records the current intended direction only. We are waiting before implementation. The probability/GPU/runtime lane has active commits and features landing quickly, so the exact file-level tasks below may need small adjustments before execution. Before any agent starts coding from this plan, re-check the latest branch, THEPC runtime state, and current probability persistence behavior.

## Current Evidence

- THEPC live DB path is `/home/ender/polymarket-data/db/polymarket.duckdb`.
- The live DuckDB is held by the normalizer writer, so direct read-only opens can fail with a DuckDB lock conflict.
- Disposable DB/WAL snapshots showed:
  - `features.probability_outputs`: 50,996 rows, latest persisted `asof_ts` around 2026-06-05 08:40:53 CDT.
  - `features.probability_grid_cache`: 11,131 rows from `cached-grid-v1`.
  - `validation.market_outcome_history`: roughly 770 rows.
- Live `/home/ender/polymarket-data/live/probabilities.json` showed newer CUDA-sourced rows than the persisted probability tables.
- Accuracy read:
  - All persisted probability rows: Brier around 0.1477, directional around 77.5%.
  - Lognormal Monte Carlo/cached-grid rows: Brier around 0.1580, directional around 75.2%.
  - Latest pre-expiry rows look very accurate, but early rows around 180 seconds left are much weaker.

## Non-Goals

- No live trading, signing, private keys, or order placement.
- No backfilling new features into old rows.
- No storing every simulated path in DuckDB.
- No making the UI block on Monte Carlo completion.
- No making the GPU worker and normalizer fight over the same DuckDB write lock.

## File Structure

Create:

- `src/polymarket_engine/probability/latency.py`
  - Owns `ProbabilityLatencyTrace` and serialization for queue/runtime/UI timing fields.
- `src/polymarket_engine/probability/fast_nowcast.py`
  - Computes cheap `fast-nowcast-v1` probability from as-of state fields.
- `src/polymarket_engine/probability/path_count_policy.py`
  - Selects path count from seconds-left, z-path, price regime, and wave phase.
- `src/polymarket_engine/probability/event_log.py`
  - Owns typed rows for `features.probability_event_log` and compact `features.simulation_artifacts`.
- `tests/probability/test_latency.py`
- `tests/probability/test_fast_nowcast.py`
- `tests/probability/test_path_count_policy.py`
- `tests/probability/test_probability_event_log.py`

Modify:

- `src/polymarket_engine/storage/schema.sql`
  - Add forward-only probability event log and compact simulation artifact tables.
- `src/polymarket_engine/storage/duckdb_store.py`
  - Add insert methods for event log and simulation artifacts.
- `src/polymarket_engine/probability/runtime.py`
  - Emit latency fields, nowcast rows, dynamic path counts, and compact simulation summaries.
- `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
  - Include latency/persistence status in `probabilities.json`.
  - Drain optional external probability event JSONL if the GPU worker writes status faster than DB persistence.
- `src/polymarket_engine/runtime_api.py`
  - Surface nowcast/confirmed rows distinctly and preserve stale/missing status envelopes.
- `tests/storage/test_schema.py`
- `tests/storage/test_normalized_writes.py`
- `tests/ingestion/test_rust_normalizer_sidecar.py`
- `tests/test_runtime_api.py`
- `docs/SPOON_DEPLOYMENT.md`
  - Document THEPC probability persistence and latency checks.

## Data Contracts

### Probability Event Log

`features.probability_event_log` is append-only. It captures every live probability row the operator saw or should have been able to see.

```sql
CREATE TABLE IF NOT EXISTS features.probability_event_log (
    event_id VARCHAR PRIMARY KEY,
    output_id VARCHAR,
    state_id VARCHAR NOT NULL,
    contract_id VARCHAR NOT NULL,
    market_slug VARCHAR NOT NULL,
    asset VARCHAR NOT NULL,
    side VARCHAR NOT NULL,
    start_ts TIMESTAMPTZ NOT NULL,
    expiry_ts TIMESTAMPTZ NOT NULL,
    asof_ts TIMESTAMPTZ NOT NULL,
    probability_kind VARCHAR NOT NULL,
    backend VARCHAR NOT NULL,
    model_version VARCHAR NOT NULL,
    generator_version VARCHAR,
    cache_key VARCHAR,
    cache_status VARCHAR,
    p_finish DOUBLE NOT NULL,
    p_no_touch DOUBLE NOT NULL,
    z_path DOUBLE NOT NULL,
    sigma_tau DOUBLE,
    executable_price DOUBLE,
    spread DOUBLE,
    seconds_left DOUBLE NOT NULL,
    wave_phase VARCHAR NOT NULL,
    wave_score DOUBLE NOT NULL,
    path_count UBIGINT,
    seed BIGINT,
    queue_ms DOUBLE,
    runtime_ms DOUBLE,
    state_to_status_ms DOUBLE,
    total_lag_ms DOUBLE,
    generated_at TIMESTAMPTZ NOT NULL,
    valid_from TIMESTAMPTZ NOT NULL,
    valid_until TIMESTAMPTZ NOT NULL,
    diagnostics_json VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
```

`probability_kind` must be one of:

- `NOWCAST`: cheap, immediate row for operator display.
- `MC`: Monte Carlo confirmation row.
- `CACHE`: cached-grid row reused without a fresh simulation.

### Simulation Artifacts

`features.simulation_artifacts` stores compact summaries, not full path matrices.

```sql
CREATE TABLE IF NOT EXISTS features.simulation_artifacts (
    artifact_id VARCHAR PRIMARY KEY,
    output_id VARCHAR,
    state_id VARCHAR NOT NULL,
    asof_ts TIMESTAMPTZ NOT NULL,
    model_version VARCHAR NOT NULL,
    backend VARCHAR NOT NULL,
    path_count UBIGINT NOT NULL,
    terminal_win_count UBIGINT NOT NULL,
    no_touch_win_count UBIGINT NOT NULL,
    terminal_price_quantiles_json VARCHAR NOT NULL,
    crossing_count_quantiles_json VARCHAR NOT NULL,
    sampled_paths_json VARCHAR NOT NULL,
    diagnostics_json VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
```

Sampled paths should be capped at 64 paths and 32 points per path for UI/debug. The full Monte Carlo matrix stays out of DuckDB.

---

## Task 1: Add Latency Trace Types

**Files:**

- Create: `src/polymarket_engine/probability/latency.py`
- Test: `tests/probability/test_latency.py`

- [ ] **Step 1: Write the failing latency serialization test**

Create `tests/probability/test_latency.py`:

```python
from __future__ import annotations

from datetime import datetime
from datetime import timezone

from polymarket_engine.probability.latency import ProbabilityLatencyTrace


def test_probability_latency_trace_computes_segment_ms() -> None:
    base = datetime(2026, 6, 5, 17, 0, 0, tzinfo=timezone.utc)
    trace = ProbabilityLatencyTrace(
        state_asof_ts=base,
        tick_observed_ts=base,
        worker_received_ts=base.replace(microsecond=100_000),
        mc_started_ts=base.replace(microsecond=150_000),
        mc_finished_ts=base.replace(microsecond=450_000),
        status_written_ts=base.replace(microsecond=500_000),
        ui_seen_ts=None,
    )

    payload = trace.to_json_dict()

    assert payload["queue_ms"] == 50.0
    assert payload["runtime_ms"] == 300.0
    assert payload["state_to_status_ms"] == 500.0
    assert payload["total_lag_ms"] == 500.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest -q tests/probability/test_latency.py
```

Expected:

```text
ModuleNotFoundError: No module named 'polymarket_engine.probability.latency'
```

- [ ] **Step 3: Implement the latency trace type**

Create `src/polymarket_engine/probability/latency.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


def _ms_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, round((end - start).total_seconds() * 1000.0, 3))


@dataclass(frozen=True)
class ProbabilityLatencyTrace:
    state_asof_ts: datetime
    tick_observed_ts: datetime | None
    worker_received_ts: datetime | None
    mc_started_ts: datetime | None
    mc_finished_ts: datetime | None
    status_written_ts: datetime | None
    ui_seen_ts: datetime | None = None

    def queue_ms(self) -> float | None:
        return _ms_between(self.worker_received_ts, self.mc_started_ts)

    def runtime_ms(self) -> float | None:
        return _ms_between(self.mc_started_ts, self.mc_finished_ts)

    def state_to_status_ms(self) -> float | None:
        return _ms_between(self.state_asof_ts, self.status_written_ts)

    def total_lag_ms(self) -> float | None:
        end = self.ui_seen_ts or self.status_written_ts
        return _ms_between(self.state_asof_ts, end)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "state_asof_ts": self.state_asof_ts.isoformat(),
            "tick_observed_ts": self.tick_observed_ts.isoformat()
            if self.tick_observed_ts
            else None,
            "worker_received_ts": self.worker_received_ts.isoformat()
            if self.worker_received_ts
            else None,
            "mc_started_ts": self.mc_started_ts.isoformat() if self.mc_started_ts else None,
            "mc_finished_ts": self.mc_finished_ts.isoformat()
            if self.mc_finished_ts
            else None,
            "status_written_ts": self.status_written_ts.isoformat()
            if self.status_written_ts
            else None,
            "ui_seen_ts": self.ui_seen_ts.isoformat() if self.ui_seen_ts else None,
            "queue_ms": self.queue_ms(),
            "runtime_ms": self.runtime_ms(),
            "state_to_status_ms": self.state_to_status_ms(),
            "total_lag_ms": self.total_lag_ms(),
        }
```

- [ ] **Step 4: Run the latency test**

Run:

```bash
uv run pytest -q tests/probability/test_latency.py
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/probability/latency.py tests/probability/test_latency.py
git commit -m "feat: add probability latency trace type"
```

## Task 2: Add Dynamic Path Count Policy

**Files:**

- Create: `src/polymarket_engine/probability/path_count_policy.py`
- Test: `tests/probability/test_path_count_policy.py`

- [ ] **Step 1: Write the failing policy tests**

Create `tests/probability/test_path_count_policy.py`:

```python
from __future__ import annotations

from polymarket_engine.probability.path_count_policy import path_count_for_state


def test_path_count_uses_small_count_for_calm_far_from_entry() -> None:
    assert (
        path_count_for_state(
            seconds_left=260.0,
            z_path=2.1,
            executable_price=0.18,
            wave_phase="none",
        )
        == 2_000
    )


def test_path_count_increases_near_threshold() -> None:
    assert (
        path_count_for_state(
            seconds_left=140.0,
            z_path=0.18,
            executable_price=0.51,
            wave_phase="forming",
        )
        == 20_000
    )


def test_path_count_uses_high_count_for_breaking_wave_before_miss() -> None:
    assert (
        path_count_for_state(
            seconds_left=38.0,
            z_path=0.72,
            executable_price=0.94,
            wave_phase="breaking",
        )
        == 50_000
    )


def test_path_count_caps_missed_wave() -> None:
    assert (
        path_count_for_state(
            seconds_left=22.0,
            z_path=2.8,
            executable_price=0.985,
            wave_phase="missed",
        )
        == 2_000
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
uv run pytest -q tests/probability/test_path_count_policy.py
```

Expected:

```text
ModuleNotFoundError: No module named 'polymarket_engine.probability.path_count_policy'
```

- [ ] **Step 3: Implement the path count policy**

Create `src/polymarket_engine/probability/path_count_policy.py`:

```python
from __future__ import annotations


def path_count_for_state(
    *,
    seconds_left: float,
    z_path: float,
    executable_price: float | None,
    wave_phase: str,
) -> int:
    price = executable_price if executable_price is not None else 0.5
    phase = wave_phase.lower()

    if phase == "missed" or price >= 0.96:
        return 2_000
    if phase == "breaking" and 0.90 <= price < 0.96:
        return 50_000
    if phase == "forming" or abs(z_path) <= 0.35:
        return 20_000
    if seconds_left <= 60.0 and 0.75 <= price < 0.90:
        return 10_000
    return 2_000
```

- [ ] **Step 4: Run the policy tests**

Run:

```bash
uv run pytest -q tests/probability/test_path_count_policy.py
```

Expected:

```text
4 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/probability/path_count_policy.py tests/probability/test_path_count_policy.py
git commit -m "feat: add dynamic probability path count policy"
```

## Task 3: Add Fast Nowcast Probability

**Files:**

- Create: `src/polymarket_engine/probability/fast_nowcast.py`
- Test: `tests/probability/test_fast_nowcast.py`

- [ ] **Step 1: Write the failing nowcast tests**

Create `tests/probability/test_fast_nowcast.py`:

```python
from __future__ import annotations

from datetime import datetime
from datetime import timezone

from polymarket_engine.probability.fast_nowcast import FastNowcastInput
from polymarket_engine.probability.fast_nowcast import compute_fast_nowcast


def test_fast_nowcast_moves_probability_with_z_path() -> None:
    nowcast = compute_fast_nowcast(
        FastNowcastInput(
            state_id="btc:UP:1",
            asof_ts=datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc),
            asset="BTC",
            side="UP",
            z_path=1.0,
            seconds_left=120.0,
            executable_price=0.62,
            sigma_tau=0.001,
        )
    )

    assert nowcast.model_version == "fast-nowcast-v1"
    assert 0.83 < nowcast.p_finish < 0.85
    assert nowcast.p_no_touch == 0.0


def test_fast_nowcast_marks_high_price_as_missed() -> None:
    nowcast = compute_fast_nowcast(
        FastNowcastInput(
            state_id="btc:UP:2",
            asof_ts=datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc),
            asset="BTC",
            side="UP",
            z_path=2.0,
            seconds_left=20.0,
            executable_price=0.985,
            sigma_tau=0.001,
        )
    )

    assert nowcast.wave_phase == "missed"
    assert nowcast.wave_score == 0.0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
uv run pytest -q tests/probability/test_fast_nowcast.py
```

Expected:

```text
ModuleNotFoundError: No module named 'polymarket_engine.probability.fast_nowcast'
```

- [ ] **Step 3: Implement fast nowcast**

Create `src/polymarket_engine/probability/fast_nowcast.py`:

```python
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FastNowcastInput:
    state_id: str
    asof_ts: datetime
    asset: str
    side: str
    z_path: float
    seconds_left: float
    executable_price: float | None
    sigma_tau: float | None


@dataclass(frozen=True)
class FastNowcastOutput:
    state_id: str
    asof_ts: datetime
    model_version: str
    p_finish: float
    p_no_touch: float
    z_path: float
    wave_phase: str
    wave_score: float


def _normal_cdf(value: float) -> float:
    return 0.5 * math.erfc(-value / math.sqrt(2.0))


def _wave_phase(*, executable_price: float | None, z_path: float) -> tuple[str, float]:
    price = executable_price if executable_price is not None else 0.5
    if price >= 0.96:
        return "missed", 0.0
    if price >= 0.90:
        return "breaking", min(1.0, max(0.0, (price - 0.90) / 0.06))
    if abs(z_path) <= 0.75 and 0.55 <= price < 0.90:
        return "forming", min(1.0, max(0.0, (price - 0.55) / 0.35))
    return "none", 0.0


def compute_fast_nowcast(input_: FastNowcastInput) -> FastNowcastOutput:
    p_finish = min(1.0, max(0.0, _normal_cdf(input_.z_path)))
    phase, score = _wave_phase(
        executable_price=input_.executable_price,
        z_path=input_.z_path,
    )
    return FastNowcastOutput(
        state_id=input_.state_id,
        asof_ts=input_.asof_ts,
        model_version="fast-nowcast-v1",
        p_finish=p_finish,
        p_no_touch=0.0,
        z_path=input_.z_path,
        wave_phase=phase,
        wave_score=score,
    )
```

- [ ] **Step 4: Run the nowcast tests**

Run:

```bash
uv run pytest -q tests/probability/test_fast_nowcast.py
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/probability/fast_nowcast.py tests/probability/test_fast_nowcast.py
git commit -m "feat: add fast probability nowcast"
```

## Task 4: Add Forward-Only Probability Event Storage

**Files:**

- Modify: `src/polymarket_engine/storage/schema.sql`
- Modify: `src/polymarket_engine/storage/duckdb_store.py`
- Test: `tests/storage/test_schema.py`
- Test: `tests/storage/test_normalized_writes.py`
- Create: `src/polymarket_engine/probability/event_log.py`
- Test: `tests/probability/test_probability_event_log.py`

- [ ] **Step 1: Write schema and insert tests**

Append these tests to `tests/probability/test_probability_event_log.py`:

```python
from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone

from polymarket_engine.probability.event_log import ProbabilityEventLogRow


def test_probability_event_log_row_serializes_stable_fields() -> None:
    asof = datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc)
    row = ProbabilityEventLogRow(
        event_id="event-1",
        output_id="prob-1",
        state_id="state-1",
        contract_id="btc-updown-5m-1:UP",
        market_slug="btc-updown-5m-1",
        asset="BTC",
        side="UP",
        start_ts=asof,
        expiry_ts=asof + timedelta(minutes=5),
        asof_ts=asof,
        probability_kind="MC",
        backend="cuda",
        model_version="cached-grid-v1",
        generator_version="cuda-lognormal-chainlink-sigma-v1",
        cache_key="cache-1",
        cache_status="REFRESH",
        p_finish=0.71,
        p_no_touch=0.22,
        z_path=0.55,
        sigma_tau=0.001,
        executable_price=0.63,
        spread=0.01,
        seconds_left=180.0,
        wave_phase="forming",
        wave_score=0.4,
        path_count=20_000,
        seed=123,
        queue_ms=4.0,
        runtime_ms=18.0,
        state_to_status_ms=35.0,
        total_lag_ms=42.0,
        generated_at=asof,
        valid_from=asof,
        valid_until=asof + timedelta(seconds=30),
        diagnostics={"reason": "unit-test"},
    )

    payload = row.to_json_dict()

    assert payload["market_slug"] == "btc-updown-5m-1"
    assert payload["probability_kind"] == "MC"
    assert payload["diagnostics"] == {"reason": "unit-test"}
```

Append this test to `tests/storage/test_normalized_writes.py`:

```python
def test_insert_probability_event_log_row_round_trips(tmp_path: Path) -> None:
    from datetime import datetime
    from datetime import timedelta
    from datetime import timezone

    from polymarket_engine.probability.event_log import ProbabilityEventLogRow

    db_path = tmp_path / "events.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()

    asof = datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc)
    row = ProbabilityEventLogRow(
        event_id="event-1",
        output_id=None,
        state_id="state-1",
        contract_id="btc-updown-5m-1:UP",
        market_slug="btc-updown-5m-1",
        asset="BTC",
        side="UP",
        start_ts=asof,
        expiry_ts=asof + timedelta(minutes=5),
        asof_ts=asof,
        probability_kind="NOWCAST",
        backend="analytic",
        model_version="fast-nowcast-v1",
        generator_version=None,
        cache_key=None,
        cache_status=None,
        p_finish=0.62,
        p_no_touch=0.0,
        z_path=0.31,
        sigma_tau=0.001,
        executable_price=0.59,
        spread=0.01,
        seconds_left=210.0,
        wave_phase="none",
        wave_score=0.0,
        path_count=None,
        seed=None,
        queue_ms=None,
        runtime_ms=0.2,
        state_to_status_ms=12.0,
        total_lag_ms=12.0,
        generated_at=asof,
        valid_from=asof,
        valid_until=asof + timedelta(seconds=2),
        diagnostics={"source": "unit-test"},
    )

    store.insert_probability_event(row)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        saved = conn.execute(
            """
            select probability_kind, backend, model_version, p_finish, diagnostics_json
            from features.probability_event_log
            where event_id = 'event-1'
            """
        ).fetchone()

    assert saved == (
        "NOWCAST",
        "analytic",
        "fast-nowcast-v1",
        0.62,
        '{"source":"unit-test"}',
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q tests/probability/test_probability_event_log.py tests/storage/test_normalized_writes.py::test_insert_probability_event_log_row_round_trips
```

Expected:

```text
ModuleNotFoundError: No module named 'polymarket_engine.probability.event_log'
```

- [ ] **Step 3: Implement event row type**

Create `src/polymarket_engine/probability/event_log.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ProbabilityEventLogRow:
    event_id: str
    output_id: str | None
    state_id: str
    contract_id: str
    market_slug: str
    asset: str
    side: str
    start_ts: datetime
    expiry_ts: datetime
    asof_ts: datetime
    probability_kind: str
    backend: str
    model_version: str
    generator_version: str | None
    cache_key: str | None
    cache_status: str | None
    p_finish: float
    p_no_touch: float
    z_path: float
    sigma_tau: float | None
    executable_price: float | None
    spread: float | None
    seconds_left: float
    wave_phase: str
    wave_score: float
    path_count: int | None
    seed: int | None
    queue_ms: float | None
    runtime_ms: float | None
    state_to_status_ms: float | None
    total_lag_ms: float | None
    generated_at: datetime
    valid_from: datetime
    valid_until: datetime
    diagnostics: dict[str, Any]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "output_id": self.output_id,
            "state_id": self.state_id,
            "contract_id": self.contract_id,
            "market_slug": self.market_slug,
            "asset": self.asset,
            "side": self.side,
            "start_ts": self.start_ts.isoformat(),
            "expiry_ts": self.expiry_ts.isoformat(),
            "asof_ts": self.asof_ts.isoformat(),
            "probability_kind": self.probability_kind,
            "backend": self.backend,
            "model_version": self.model_version,
            "generator_version": self.generator_version,
            "cache_key": self.cache_key,
            "cache_status": self.cache_status,
            "p_finish": self.p_finish,
            "p_no_touch": self.p_no_touch,
            "z_path": self.z_path,
            "sigma_tau": self.sigma_tau,
            "executable_price": self.executable_price,
            "spread": self.spread,
            "seconds_left": self.seconds_left,
            "wave_phase": self.wave_phase,
            "wave_score": self.wave_score,
            "path_count": self.path_count,
            "seed": self.seed,
            "queue_ms": self.queue_ms,
            "runtime_ms": self.runtime_ms,
            "state_to_status_ms": self.state_to_status_ms,
            "total_lag_ms": self.total_lag_ms,
            "generated_at": self.generated_at.isoformat(),
            "valid_from": self.valid_from.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "diagnostics": self.diagnostics,
        }
```

- [ ] **Step 4: Add schema tables**

In `src/polymarket_engine/storage/schema.sql`, add the two SQL tables from the Data Contracts section after `features.probability_outputs`.

Also update `tests/storage/test_schema.py` expected table list to include:

```python
"features.probability_event_log",
"features.simulation_artifacts",
```

- [ ] **Step 5: Add store insertion method**

In `src/polymarket_engine/storage/duckdb_store.py`, import `ProbabilityEventLogRow` and add:

```python
    def insert_probability_event(self, row: ProbabilityEventLogRow) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                insert or replace into features.probability_event_log
                (event_id, output_id, state_id, contract_id, market_slug, asset, side,
                 start_ts, expiry_ts, asof_ts, probability_kind, backend, model_version,
                 generator_version, cache_key, cache_status, p_finish, p_no_touch,
                 z_path, sigma_tau, executable_price, spread, seconds_left, wave_phase,
                 wave_score, path_count, seed, queue_ms, runtime_ms, state_to_status_ms,
                 total_lag_ms, generated_at, valid_from, valid_until, diagnostics_json,
                 created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    row.event_id,
                    row.output_id,
                    row.state_id,
                    row.contract_id,
                    row.market_slug,
                    row.asset,
                    row.side,
                    row.start_ts,
                    row.expiry_ts,
                    row.asof_ts,
                    row.probability_kind,
                    row.backend,
                    row.model_version,
                    row.generator_version,
                    row.cache_key,
                    row.cache_status,
                    row.p_finish,
                    row.p_no_touch,
                    row.z_path,
                    row.sigma_tau,
                    row.executable_price,
                    row.spread,
                    row.seconds_left,
                    row.wave_phase,
                    row.wave_score,
                    row.path_count,
                    row.seed,
                    row.queue_ms,
                    row.runtime_ms,
                    row.state_to_status_ms,
                    row.total_lag_ms,
                    row.generated_at,
                    row.valid_from,
                    row.valid_until,
                    _strict_json(row.diagnostics),
                    datetime.now(timezone.utc),
                ],
            )
```

- [ ] **Step 6: Run storage tests**

Run:

```bash
uv run pytest -q tests/probability/test_probability_event_log.py tests/storage/test_schema.py tests/storage/test_normalized_writes.py::test_insert_probability_event_log_row_round_trips
```

Expected:

```text
passed
```

- [ ] **Step 7: Commit**

```bash
git add src/polymarket_engine/probability/event_log.py src/polymarket_engine/storage/schema.sql src/polymarket_engine/storage/duckdb_store.py tests/probability/test_probability_event_log.py tests/storage/test_schema.py tests/storage/test_normalized_writes.py
git commit -m "feat: persist probability event log rows"
```

## Task 5: Include Nowcast And Latency In Runtime Status

**Files:**

- Modify: `src/polymarket_engine/probability/runtime.py`
- Modify: `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
- Test: `tests/ingestion/test_rust_normalizer_sidecar.py`
- Test: `tests/test_runtime_api.py`

- [ ] **Step 1: Add status test for latency and lane separation**

Add this test to `tests/ingestion/test_rust_normalizer_sidecar.py`:

```python
def test_probability_status_includes_latency_and_lanes(tmp_path: Path) -> None:
    from polymarket_engine.ingestion.rust_normalizer_sidecar import _write_probability_status

    out_path = tmp_path / "probabilities.json"
    rows = [
        {
            "contract": "BTC 5m UP",
            "contract_id": "btc-updown-5m-1:UP",
            "model_version": "fast-nowcast-v1",
            "probability_kind": "NOWCAST",
            "p_finish": 0.61,
            "latency": {"total_lag_ms": 35.0},
        },
        {
            "contract": "BTC 5m UP",
            "contract_id": "btc-updown-5m-1:UP",
            "model_version": "cached-grid-v1",
            "probability_kind": "MC",
            "p_finish": 0.64,
            "latency": {"total_lag_ms": 940.0},
        },
    ]

    _write_probability_status(out_path=out_path, rows=rows, skipped=0, errors=())

    payload = json.loads(out_path.read_text())
    assert payload["schema_version"] == "polymarket-probability-runtime-v1"
    assert payload["latency"]["max_total_lag_ms"] == 940.0
    assert payload["lanes"] == {"NOWCAST": 1, "MC": 1}
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
uv run pytest -q tests/ingestion/test_rust_normalizer_sidecar.py::test_probability_status_includes_latency_and_lanes
```

Expected:

```text
KeyError: 'latency'
```

- [ ] **Step 3: Update probability status writer**

Modify `_write_probability_status` in `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`:

```python
def _probability_status_latency(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    lags = [
        row.get("latency", {}).get("total_lag_ms")
        for row in rows
        if isinstance(row.get("latency"), dict)
        and row.get("latency", {}).get("total_lag_ms") is not None
    ]
    return {
        "max_total_lag_ms": max(lags) if lags else None,
        "avg_total_lag_ms": round(sum(lags) / len(lags), 3) if lags else None,
    }


def _probability_status_lanes(rows: list[dict[str, Any]]) -> dict[str, int]:
    lanes: dict[str, int] = {}
    for row in rows:
        lane = str(row.get("probability_kind") or "MC")
        lanes[lane] = lanes.get(lane, 0) + 1
    return lanes
```

Then include in the payload:

```python
"latency": _probability_status_latency(rows),
"lanes": _probability_status_lanes(rows),
```

- [ ] **Step 4: Add nowcast rows in runtime output**

In `src/polymarket_engine/probability/runtime.py`, use `compute_fast_nowcast(...)` before or alongside Monte Carlo rows. Do not insert nowcast rows into `features.probability_outputs`; only include them in status/event-log flow.

Use this shape when building nowcast rows:

```python
nowcast = compute_fast_nowcast(
    FastNowcastInput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        asset=probability_input.asset,
        side=probability_input.side,
        z_path=probability_input.z_path,
        seconds_left=probability_input.seconds_left,
        executable_price=probability_input.executable_price,
        sigma_tau=probability_input.sigma_tau,
    )
)
```

The row returned to status must include:

```python
{
    "probability_kind": "NOWCAST",
    "backend": "analytic",
    "model_version": nowcast.model_version,
    "p_finish": nowcast.p_finish,
    "p_no_touch": nowcast.p_no_touch,
    "wave_phase": nowcast.wave_phase,
    "wave_score": nowcast.wave_score,
}
```

- [ ] **Step 5: Run status tests**

Run:

```bash
uv run pytest -q tests/ingestion/test_rust_normalizer_sidecar.py::test_probability_status_includes_latency_and_lanes tests/test_runtime_api.py::test_runtime_probabilities_reads_live_probability_status_file
```

Expected:

```text
passed
```

- [ ] **Step 6: Commit**

```bash
git add src/polymarket_engine/probability/runtime.py src/polymarket_engine/ingestion/rust_normalizer_sidecar.py tests/ingestion/test_rust_normalizer_sidecar.py tests/test_runtime_api.py
git commit -m "feat: expose nowcast and probability latency status"
```

## Task 6: Persist MC Events And Compact Simulation Artifacts

**Files:**

- Modify: `src/polymarket_engine/probability/runtime.py`
- Modify: `src/polymarket_engine/storage/duckdb_store.py`
- Test: `tests/storage/test_normalized_writes.py`
- Test: `tests/probability/test_probability_event_log.py`

- [ ] **Step 1: Add artifact insert test**

Append to `tests/storage/test_normalized_writes.py`:

```python
def test_insert_simulation_artifact_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "artifact.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()

    store.insert_simulation_artifact(
        artifact_id="artifact-1",
        output_id="prob-1",
        state_id="state-1",
        asof_ts=datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc),
        model_version="offline-lognormal-chainlink-sigma-v1",
        backend="cpu",
        path_count=2_000,
        terminal_win_count=1_200,
        no_touch_win_count=900,
        terminal_price_quantiles={"p05": 100.0, "p50": 101.0, "p95": 103.0},
        crossing_count_quantiles={"p50": 1.0, "p95": 4.0},
        sampled_paths=[{"index": 0, "points": [100.0, 101.0], "terminal_win": True}],
        diagnostics={"source": "unit-test"},
    )

    with duckdb.connect(str(db_path), read_only=True) as conn:
        saved = conn.execute(
            """
            select path_count, terminal_win_count, terminal_price_quantiles_json
            from features.simulation_artifacts
            where artifact_id = 'artifact-1'
            """
        ).fetchone()

    assert saved == (2_000, 1_200, '{"p05":100.0,"p50":101.0,"p95":103.0}')
```

- [ ] **Step 2: Run artifact test to verify it fails**

Run:

```bash
uv run pytest -q tests/storage/test_normalized_writes.py::test_insert_simulation_artifact_round_trips
```

Expected:

```text
AttributeError: 'DuckDbIngestStore' object has no attribute 'insert_simulation_artifact'
```

- [ ] **Step 3: Implement artifact insert method**

Add this method to `src/polymarket_engine/storage/duckdb_store.py`:

```python
    def insert_simulation_artifact(
        self,
        *,
        artifact_id: str,
        output_id: str | None,
        state_id: str,
        asof_ts: datetime,
        model_version: str,
        backend: str,
        path_count: int,
        terminal_win_count: int,
        no_touch_win_count: int,
        terminal_price_quantiles: dict[str, float],
        crossing_count_quantiles: dict[str, float],
        sampled_paths: list[dict[str, object]],
        diagnostics: dict[str, object],
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                insert or replace into features.simulation_artifacts
                (artifact_id, output_id, state_id, asof_ts, model_version, backend,
                 path_count, terminal_win_count, no_touch_win_count,
                 terminal_price_quantiles_json, crossing_count_quantiles_json,
                 sampled_paths_json, diagnostics_json, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    artifact_id,
                    output_id,
                    state_id,
                    asof_ts,
                    model_version,
                    backend,
                    path_count,
                    terminal_win_count,
                    no_touch_win_count,
                    _strict_json(terminal_price_quantiles),
                    _strict_json(crossing_count_quantiles),
                    _strict_json(sampled_paths),
                    _strict_json(diagnostics),
                    datetime.now(timezone.utc),
                ],
            )
```

- [ ] **Step 4: Persist MC event rows after output insert**

In `src/polymarket_engine/probability/runtime.py`, after `store.insert_probability_output(...)`, build and insert a `ProbabilityEventLogRow` with:

```python
probability_kind="MC"
backend="cpu"
model_version=output.model_version
generator_version=output.model_version
path_count=path_count_for_state(
    seconds_left=probability_input.seconds_left,
    z_path=probability_input.z_path,
    executable_price=probability_input.executable_price,
    wave_phase=nowcast.wave_phase,
)
```

Use deterministic IDs:

```python
event_id = f"prob-event-{hashlib.sha256((output_id + ':MC').encode()).hexdigest()[:24]}"
artifact_id = f"sim-artifact-{hashlib.sha256((output_id + ':artifact').encode()).hexdigest()[:24]}"
```

- [ ] **Step 5: Run persistence tests**

Run:

```bash
uv run pytest -q tests/storage/test_normalized_writes.py::test_insert_simulation_artifact_round_trips tests/ingestion/test_rust_normalizer_sidecar.py::test_sidecar_cycle_computes_probability_outputs_when_enabled
```

Expected:

```text
passed
```

- [ ] **Step 6: Commit**

```bash
git add src/polymarket_engine/probability/runtime.py src/polymarket_engine/storage/duckdb_store.py tests/storage/test_normalized_writes.py
git commit -m "feat: persist compact simulation artifacts"
```

## Task 7: Add Single-Writer Drain For External GPU Probability Events

**Files:**

- Modify: `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
- Test: `tests/ingestion/test_rust_normalizer_sidecar.py`
- Modify: `docs/SPOON_DEPLOYMENT.md`

- [ ] **Step 1: Add drain test**

Add this test to `tests/ingestion/test_rust_normalizer_sidecar.py`:

```python
def test_sidecar_drains_external_probability_events_once(tmp_path: Path) -> None:
    from polymarket_engine.ingestion.rust_normalizer_sidecar import _drain_probability_event_jsonl

    db_path = tmp_path / "events.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()

    event_path = tmp_path / "live" / "probability-events.jsonl"
    event_path.parent.mkdir()
    event_path.write_text(
        json.dumps(
            {
                "event_id": "event-1",
                "output_id": None,
                "state_id": "state-1",
                "contract_id": "btc-updown-5m-1:UP",
                "market_slug": "btc-updown-5m-1",
                "asset": "BTC",
                "side": "UP",
                "start_ts": "2026-06-05T17:00:00+00:00",
                "expiry_ts": "2026-06-05T17:05:00+00:00",
                "asof_ts": "2026-06-05T17:01:00+00:00",
                "probability_kind": "MC",
                "backend": "cuda",
                "model_version": "cached-grid-v1",
                "generator_version": "cuda-lognormal-chainlink-sigma-v1",
                "cache_key": "cache-1",
                "cache_status": "REFRESH",
                "p_finish": 0.71,
                "p_no_touch": 0.2,
                "z_path": 0.4,
                "sigma_tau": 0.001,
                "executable_price": 0.62,
                "spread": 0.01,
                "seconds_left": 240.0,
                "wave_phase": "forming",
                "wave_score": 0.3,
                "path_count": 20000,
                "seed": 123,
                "queue_ms": 2.0,
                "runtime_ms": 16.0,
                "state_to_status_ms": 40.0,
                "total_lag_ms": 44.0,
                "generated_at": "2026-06-05T17:01:00+00:00",
                "valid_from": "2026-06-05T17:01:00+00:00",
                "valid_until": "2026-06-05T17:01:30+00:00",
                "diagnostics": {"source": "gpu-worker"},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )

    drained = _drain_probability_event_jsonl(store=store, event_path=event_path)

    assert drained == 1
    assert event_path.read_text() == ""
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from features.probability_event_log").fetchone() == (1,)
```

- [ ] **Step 2: Run drain test to verify it fails**

Run:

```bash
uv run pytest -q tests/ingestion/test_rust_normalizer_sidecar.py::test_sidecar_drains_external_probability_events_once
```

Expected:

```text
ImportError: cannot import name '_drain_probability_event_jsonl'
```

- [ ] **Step 3: Implement JSONL drain**

Add to `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`:

```python
def _parse_event_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("event datetime must be a string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _event_row_from_payload(payload: dict[str, Any]) -> ProbabilityEventLogRow:
    return ProbabilityEventLogRow(
        event_id=str(payload["event_id"]),
        output_id=payload.get("output_id"),
        state_id=str(payload["state_id"]),
        contract_id=str(payload["contract_id"]),
        market_slug=str(payload["market_slug"]),
        asset=str(payload["asset"]),
        side=str(payload["side"]),
        start_ts=_parse_event_datetime(payload["start_ts"]),
        expiry_ts=_parse_event_datetime(payload["expiry_ts"]),
        asof_ts=_parse_event_datetime(payload["asof_ts"]),
        probability_kind=str(payload["probability_kind"]),
        backend=str(payload["backend"]),
        model_version=str(payload["model_version"]),
        generator_version=payload.get("generator_version"),
        cache_key=payload.get("cache_key"),
        cache_status=payload.get("cache_status"),
        p_finish=float(payload["p_finish"]),
        p_no_touch=float(payload["p_no_touch"]),
        z_path=float(payload["z_path"]),
        sigma_tau=payload.get("sigma_tau"),
        executable_price=payload.get("executable_price"),
        spread=payload.get("spread"),
        seconds_left=float(payload["seconds_left"]),
        wave_phase=str(payload["wave_phase"]),
        wave_score=float(payload["wave_score"]),
        path_count=payload.get("path_count"),
        seed=payload.get("seed"),
        queue_ms=payload.get("queue_ms"),
        runtime_ms=payload.get("runtime_ms"),
        state_to_status_ms=payload.get("state_to_status_ms"),
        total_lag_ms=payload.get("total_lag_ms"),
        generated_at=_parse_event_datetime(payload["generated_at"]),
        valid_from=_parse_event_datetime(payload["valid_from"]),
        valid_until=_parse_event_datetime(payload["valid_until"]),
        diagnostics=dict(payload.get("diagnostics", {})),
    )


def _drain_probability_event_jsonl(
    *,
    store: DuckDbIngestStore,
    event_path: Path,
) -> int:
    if not event_path.exists():
        return 0
    lines = event_path.read_text(encoding="utf-8").splitlines()
    drained = 0
    for line in lines:
        if not line.strip():
            continue
        row = _event_row_from_payload(json.loads(line))
        store.insert_probability_event(row)
        drained += 1
    event_path.write_text("", encoding="utf-8")
    return drained
```

- [ ] **Step 4: Wire drain into sidecar loop**

Call `_drain_probability_event_jsonl(...)` once per sidecar cycle after normalized writes and before status health write. Use:

```python
event_path = probability_status_path.with_name("probability-events.jsonl")
probability_events_drained = _drain_probability_event_jsonl(
    store=store,
    event_path=event_path,
)
```

Include `probability_events_drained` in the sidecar result/status payload.

- [ ] **Step 5: Document single-writer policy**

In `docs/SPOON_DEPLOYMENT.md`, add:

```markdown
### Probability Persistence Policy

THEPC probability display may update from `/home/ender/polymarket-data/live/probabilities.json`
before the historical DuckDB tables are checkpointed. The normalizer remains the
DuckDB writer owner. External GPU probability workers should write compact event
rows to `/home/ender/polymarket-data/live/probability-events.jsonl`; the
normalizer drains that file into `features.probability_event_log`. This avoids
DuckDB writer-lock contention while preserving forward-only probability evidence
for calibration.
```

- [ ] **Step 6: Run drain tests**

Run:

```bash
uv run pytest -q tests/ingestion/test_rust_normalizer_sidecar.py::test_sidecar_drains_external_probability_events_once
```

Expected:

```text
passed
```

- [ ] **Step 7: Commit**

```bash
git add src/polymarket_engine/ingestion/rust_normalizer_sidecar.py tests/ingestion/test_rust_normalizer_sidecar.py docs/SPOON_DEPLOYMENT.md
git commit -m "feat: drain external probability event log"
```

## Task 8: Add Runtime Verification Script For Probability Lag

**Files:**

- Create: `scripts/check_probability_latency.py`
- Test: `tests/scripts/test_check_probability_latency.py`

- [ ] **Step 1: Write failing script tests**

Create `tests/scripts/test_check_probability_latency.py`:

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_check_probability_latency_passes_for_fresh_payload(tmp_path: Path) -> None:
    path = tmp_path / "probabilities.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-probability-runtime-v1",
                "latency": {"max_total_lag_ms": 850.0},
                "lanes": {"NOWCAST": 4, "MC": 4},
                "rows": [{"contract_id": "btc:UP"}],
            }
        )
    )

    result = subprocess.run(
        [
            "python3",
            "scripts/check_probability_latency.py",
            "--path",
            str(path),
            "--max-total-lag-ms",
            "1000",
            "--require-lane",
            "NOWCAST",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "probability_latency=ok" in result.stdout


def test_check_probability_latency_fails_when_lag_is_high(tmp_path: Path) -> None:
    path = tmp_path / "probabilities.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-probability-runtime-v1",
                "latency": {"max_total_lag_ms": 1500.0},
                "lanes": {"NOWCAST": 4},
                "rows": [{"contract_id": "btc:UP"}],
            }
        )
    )

    result = subprocess.run(
        [
            "python3",
            "scripts/check_probability_latency.py",
            "--path",
            str(path),
            "--max-total-lag-ms",
            "1000",
        ],
        check=False,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "probability_lag_too_high" in result.stderr
```

- [ ] **Step 2: Run script tests to verify they fail**

Run:

```bash
uv run pytest -q tests/scripts/test_check_probability_latency.py
```

Expected:

```text
can't open file
```

- [ ] **Step 3: Implement latency check script**

Create `scripts/check_probability_latency.py`:

```python
#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--max-total-lag-ms", type=float, required=True)
    parser.add_argument("--require-lane", action="append", default=[])
    args = parser.parse_args()

    payload = json.loads(args.path.read_text())
    if payload.get("schema_version") != "polymarket-probability-runtime-v1":
        print("invalid_probability_schema", file=sys.stderr)
        return 1

    lanes = payload.get("lanes", {})
    for lane in args.require_lane:
        if int(lanes.get(lane, 0)) <= 0:
            print(f"missing_probability_lane lane={lane}", file=sys.stderr)
            return 1

    max_lag = payload.get("latency", {}).get("max_total_lag_ms")
    if max_lag is None:
        print("missing_probability_latency", file=sys.stderr)
        return 1
    if float(max_lag) > args.max_total_lag_ms:
        print(
            f"probability_lag_too_high max_total_lag_ms={max_lag}",
            file=sys.stderr,
        )
        return 1

    print(f"probability_latency=ok max_total_lag_ms={max_lag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run script tests**

Run:

```bash
uv run pytest -q tests/scripts/test_check_probability_latency.py
```

Expected:

```text
2 passed
```

- [ ] **Step 5: Commit**

```bash
git add scripts/check_probability_latency.py tests/scripts/test_check_probability_latency.py
git commit -m "feat: add probability latency check script"
```

## Task 9: THEPC Verification

**Files:**

- No source changes.

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
uv run pytest -q \
  tests/probability/test_latency.py \
  tests/probability/test_fast_nowcast.py \
  tests/probability/test_path_count_policy.py \
  tests/probability/test_probability_event_log.py \
  tests/storage/test_schema.py \
  tests/storage/test_normalized_writes.py \
  tests/ingestion/test_rust_normalizer_sidecar.py \
  tests/scripts/test_check_probability_latency.py \
  tests/test_runtime_api.py
```

Expected:

```text
passed
```

- [ ] **Step 2: Run static checks**

Run:

```bash
uv run ruff check .
uv run mypy src tests
```

Expected:

```text
All checks passed
Success: no issues found
```

- [ ] **Step 3: Deploy only after explicit operator approval**

Run only after the user approves deployment:

```bash
cd /Users/goon/polymarket
./scripts/deploy_pc.sh
```

Expected:

```text
THEPC deployed <sha>
```

- [ ] **Step 4: Verify live probability status from the Mac**

Run:

```bash
curl -fsS http://100.72.104.49:8000/api/runtime/probabilities?limit=8 | python3 -m json.tool | head -80
```

Expected signs:

```text
"lanes": {
    "NOWCAST": ...
    "MC": ...
}
"latency": {
    "max_total_lag_ms": ...
}
```

- [ ] **Step 5: Verify THEPC probability latency file**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd /home/ender/polymarket && python3 scripts/check_probability_latency.py --path /home/ender/polymarket-data/live/probabilities.json --max-total-lag-ms 1000 --require-lane NOWCAST"'
```

Expected:

```text
probability_latency=ok max_total_lag_ms=<value>
```

- [ ] **Step 6: Verify forward-only DB persistence without stopping services**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "
SRC=/home/ender/polymarket-data/db/polymarket.duckdb
SNAP=/home/ender/polymarket-data/db/probability-latency-check-\$\$.duckdb
cp --reflink=auto \"\$SRC\" \"\$SNAP\"
trap \"rm -f \\\"\$SNAP\\\"\" EXIT
docker exec -i polymarket-rust-collector-normalizer-1 python3 - <<PY
import duckdb
con = duckdb.connect(\"/var/lib/polymarket/db/$(basename "$SNAP")\", read_only=True)
print(con.execute(\"select probability_kind, backend, count(*) from features.probability_event_log group by 1,2 order by 1,2\").fetchall())
print(con.execute(\"select count(*), max(created_at) from features.simulation_artifacts\").fetchall())
PY
"'
```

Expected:

```text
[('MC', ...), ('NOWCAST', ...)]
[(<positive count>, <recent timestamp>)]
```

- [ ] **Step 7: Commit verification notes if docs changed**

If deployment docs were updated in Task 7, commit was already done there. If live verification exposed a needed doc correction, make only that doc correction and commit:

```bash
git add docs/SPOON_DEPLOYMENT.md
git commit -m "docs: clarify probability latency verification"
```

## Risk Areas

- DuckDB writer lock: keep historical writes owned by the normalizer, or use an explicit drain file. Do not let multiple long-lived services write the same DuckDB concurrently.
- Metrics contamination: do not insert `NOWCAST` rows into `features.probability_outputs`, because prior scoring treats that table as model-output history.
- UI confusion: display nowcast and MC confirmation as separate lanes. Do not make the operator think a cheap nowcast is a settled GPU MC result.
- Storage bloat: cap sampled paths and store quantiles/counts instead of full simulations.
- As-of safety: all event rows must use `asof_ts`, `state_id`, source timestamps, and generated timestamps from the decision time. Future outcome data only joins later for labels.

## Self-Review

- Spec coverage: latency tracing, fast updates, dynamic path counts, compact simulation persistence, THEPC single-writer persistence, and runtime verification are each covered by tasks.
- Placeholder scan: no `TBD`, generic `TODO`, or unspecified "add tests" steps remain.
- Type consistency: `ProbabilityLatencyTrace`, `FastNowcastInput`, `FastNowcastOutput`, and `ProbabilityEventLogRow` are introduced before use in later tasks.
- Scope check: this is one implementation unit because latency, nowcast, and persistence share the same probability status contract. GPU kernel/model rewrite work remains outside this plan.
