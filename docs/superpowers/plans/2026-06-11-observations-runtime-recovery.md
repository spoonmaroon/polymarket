# Observations Runtime Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `docs/observations.md` into implemented runtime safety, recovery, offload gating, diagnostics, and later calibration work without changing live trading authority.

**Architecture:** Stabilize the live system in layers. First clean the observations document and add evidence capture, then add explicit runtime phases, then enforce an active `OffloadReadinessGate` that allows nowcast/last-good display but blocks expensive Monte Carlo/GPU work until the system is READY. Expose recovery/offload/bug-report state through the FastAPI runtime API and TUI, then add calibration datasets and reports only after runtime reliability is covered.

**Tech Stack:** Python 3.11+, pytest, FastAPI, DuckDB, Rust TUI with reqwest/ratatui, Docker Compose runtime artifacts, existing probability worker/status JSON files.

---

## Scope

This plan covers every workstream in `docs/observations.md`, but not as one giant patch. Runtime correctness comes first; ML is explicitly last.

In scope:

- Clean and normalize `docs/observations.md` so it renders as a usable planning document.
- Add root-cause evidence capture for the current runtime bugs before behavior changes.
- Add a runtime recovery model with phases: `BOOTING`, `WARMING`, `RECOVERING`, `DEGRADED`, `READY`, `BLOCKED`.
- Add an active offload gate that blocks expensive MC/GPU work until READY and preserves nowcast/last-good output.
- Add structured bug reports for API decode failures, TUI receive lag, offload blocked/running mismatch, K mutation, sigma invalidity, and service-health mismatch.
- Expose recovery/offload/bug report state through API and TUI.
- Add K immutability diagnostics and sigma validity diagnostics.
- Add calibration dataset logging and reports after runtime reliability is covered.
- Defer calibration model implementation to a separate plan after replay-safe
  data exists.

Out of scope:

- Live trading.
- Private keys, signing, funded-account flows, or real order placement.
- Letting an LLM auto-apply patches to the deployed runtime.
- Neural networks.
- Replacing Monte Carlo as the base probability engine.
- Broad storage migrations unrelated to the safety gates.

## Current Repo Anchors

- `src/polymarket_engine/ops/runtime_keeper.py` already checks Docker/API/UI/live/probability health and writes `runtime_keeper.json`.
- `src/polymarket_engine/runtime_gates.py` already evaluates status and normalized-health freshness.
- `src/polymarket_engine/probability/cpu_budget.py` already adapts total paths based on measured CPU.
- `src/polymarket_engine/probability/gpu_worker.py` already publishes nowcast rows before MC finishes and preserves retained rows for stale inputs.
- `src/polymarket_engine/runtime_api.py` already exposes `/api/runtime/live`, `/api/runtime/gates`, `/api/runtime/probabilities`, and probability event helpers.
- `rust/crates/polymarket-cockpit-tui/src/client.rs` already uses request timeouts, but JSON/status decode errors still need clearer classification and UI surfacing.

## File Structure

- Modify: `docs/observations.md`
  - Remove paste artifact, close malformed code fence, normalize bug headings, preserve content.

- Create: `src/polymarket_engine/ops/recovery_manager.py`
  - Own runtime phase computation, boot ID, startup timestamp, consecutive healthy cycles, recovery attempts, and bug report writing.

- Modify: `src/polymarket_engine/runtime_gates.py`
  - Expand gate output to include price/orderbook/probability/volatility/target/API freshness reasons usable by recovery/offload logic.

- Create: `src/polymarket_engine/probability/offload_gate.py`
  - Pure active gate deciding `offload_allowed`, reason codes, recommended worker mode, and max path budget.

- Modify: `src/polymarket_engine/probability/gpu_worker.py`
  - Call `OffloadReadinessGate` before expensive MC.
  - Keep nowcast rows and retained last-good MC rows.
  - Write `state="OFFLOAD_BLOCKED"` when blocked.

- Modify: `src/polymarket_engine/probability/cpu_budget.py`
  - Add ramp-stage helpers if they are not cleanly isolated in `offload_gate.py`.

- Modify: `src/polymarket_engine/runtime_api.py`
  - Add `/api/runtime/recovery`, `/api/runtime/offload`, and `/api/runtime/bug-reports`.
  - Include compact recovery/offload summary in `/api/runtime/live`.

- Modify: `src/polymarket_engine/domain/contract_rules.py`
- Modify: `src/polymarket_engine/domain/contracts.py`
- Modify: `src/polymarket_engine/features/state_builder.py`
- Modify: `src/polymarket_engine/probability/runtime_inputs.py`
  - Add threshold/K source diagnostics and mutation blocking where runtime K is resolved.

- Modify: `src/polymarket_engine/features/volatility.py`
- Modify: `src/polymarket_engine/probability/hot_inputs.py`
  - Add sigma validity diagnostics and block reasons into probability inputs.

- Modify: `rust/crates/polymarket-cockpit-tui/src/status.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/client.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/systems.rs`
  - Parse and display runtime phase, offload state, recovery attempts, boot ID, last bug ID, and API decode classifications.

- Create: `src/polymarket_engine/diagnostics/bug_report.py`
  - Defines bug report schema and LLM-ready prompt rendering.

- Create: `src/polymarket_engine/calibration/dataset.py`
- Create: `src/polymarket_engine/calibration/reports.py`
  - Replay-safe dataset export and calibration reports.

- Tests:
  - Create: `tests/ops/test_recovery_manager.py`
  - Create: `tests/probability/test_offload_gate.py`
  - Modify: `tests/probability/test_gpu_worker.py`
  - Modify: `tests/test_runtime_api.py`
  - Modify: `tests/test_runtime_gates.py`
  - Modify: `tests/domain/test_contract_rules.py`
  - Modify: `tests/probability/test_hot_inputs.py`
  - Create: `tests/diagnostics/test_bug_report.py`
  - Create: `tests/calibration/test_dataset.py`
  - Create: `tests/calibration/test_reports.py`
  - Rust tests inside `rust/crates/polymarket-cockpit-tui`.

---

## Task 0: Normalize The Observations Document

**Files:**
- Modify: `docs/observations.md`

- [ ] **Step 1: Remove the paste preface and close the malformed code fence**

Change the first line from:

```markdown
Paste this into docs/observations.md:
```

to nothing. Replace the `BUG-001` severity block:

```markdown
### Severity
```text
CRITICAL

Suspected causes
```

with:

```markdown
### Severity

CRITICAL

### Suspected Causes
```

- [ ] **Step 2: Normalize all bug headings**

Convert every bare bug heading like:

```markdown
BUG-002: API BLOCKED / Response Body Decode Error
```

to:

```markdown
## BUG-002: API BLOCKED / Response Body Decode Error
```

- [ ] **Step 3: Run markdown sanity checks**

Run:

```bash
rg -n "Paste this|```|^BUG-[0-9]" docs/observations.md
```

Expected:

- No `Paste this` line.
- Either zero code fences or balanced code fences.
- No bare `BUG-###` headings without `##`.

- [ ] **Step 4: Commit**

```bash
git add docs/observations.md
git commit -m "docs: normalize runtime observations backlog"
```

---

## Task 1: Capture Evidence Before Fixing Runtime Bugs

**Files:**
- Modify: `src/polymarket_engine/ops/runtime_keeper.py`
- Modify: `src/polymarket_engine/runtime_gates.py`
- Create: `tests/ops/test_runtime_evidence.py`

- [ ] **Step 1: Add failing tests for evidence payload shape**

Create `tests/ops/test_runtime_evidence.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from polymarket_engine.ops.runtime_keeper import KeeperCheck
from polymarket_engine.ops.runtime_keeper import report_payload


def test_keeper_report_includes_evidence_fields() -> None:
    payload = report_payload(
        checks=[
            KeeperCheck(
                name="api:/api/runtime/live",
                ok=False,
                detail="status=502 content_type=text/html body_prefix=<html",
            )
        ],
        actions=["compose up api"],
        generated_at=datetime(2026, 6, 11, 12, 0, tzinfo=UTC),
    )

    assert payload["ok"] is False
    assert payload["generated_at"] == "2026-06-11T12:00:00+00:00"
    assert payload["checks"][0]["name"] == "api:/api/runtime/live"
    assert payload["checks"][0]["ok"] is False
    assert "content_type=text/html" in payload["checks"][0]["detail"]
    assert payload["actions"] == ["compose up api"]
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
uv run pytest -q tests/ops/test_runtime_evidence.py
```

Expected: fail because `report_payload` does not accept `generated_at` or does not preserve detailed response metadata.

- [ ] **Step 3: Implement evidence fields without changing recovery behavior**

Modify `report_payload` in `src/polymarket_engine/ops/runtime_keeper.py` to accept an optional `generated_at` and preserve the check detail string as-is:

```python
def report_payload(
    checks: Sequence[KeeperCheck],
    actions: Sequence[str],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    now = generated_at or datetime.now(timezone.utc)
    return {
        "ok": all(check.ok for check in checks),
        "generated_at": now.isoformat(),
        "checks": [asdict(check) for check in checks],
        "actions": list(actions),
    }
```

- [ ] **Step 4: Add HTTP response metadata to failed checks**

When `evaluate_http_checks` handles non-200, empty, or non-JSON responses, include:

```text
status=<code> content_type=<content_type> body_prefix=<first 120 chars>
```

If `HttpResult` does not yet carry content type, extend it:

```python
@dataclass(frozen=True)
class HttpResult:
    status_code: int
    json_payload: dict[str, Any]
    text: str
    content_type: str = ""
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest -q tests/ops/test_runtime_keeper.py tests/ops/test_runtime_evidence.py tests/test_runtime_gates.py
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/polymarket_engine/ops/runtime_keeper.py src/polymarket_engine/runtime_gates.py tests/ops/test_runtime_evidence.py tests/ops/test_runtime_keeper.py
git commit -m "ops: preserve runtime failure evidence"
```

---

## Task 2: Add Runtime Recovery Manager

**Files:**
- Create: `src/polymarket_engine/ops/recovery_manager.py`
- Create: `tests/ops/test_recovery_manager.py`

- [ ] **Step 1: Write failing tests for phase transitions**

Create `tests/ops/test_recovery_manager.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from polymarket_engine.ops.recovery_manager import RecoveryConfig
from polymarket_engine.ops.recovery_manager import RecoveryInputs
from polymarket_engine.ops.recovery_manager import RuntimePhase
from polymarket_engine.ops.recovery_manager import evaluate_recovery_state


BASE = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)


def healthy_inputs(**overrides: object) -> RecoveryInputs:
    values = {
        "boot_id": "boot-1",
        "startup_ts": BASE - timedelta(minutes=10),
        "now": BASE,
        "status_ok": True,
        "normalized_health_ok": True,
        "api_ok": True,
        "price_fresh": True,
        "orderbook_fresh": True,
        "probability_inputs_fresh": True,
        "volatility_fresh": True,
        "target_fresh": True,
        "sigma_valid": True,
        "k_stable": True,
        "duckdb_ok": True,
        "cpu_percent": 12.0,
        "memory_mb": 300,
        "queue_length": 0,
        "recent_api_blocked": False,
        "recent_decode_error": False,
        "consecutive_healthy_cycles": 5,
        "recovery_attempts": 0,
    }
    values.update(overrides)
    return RecoveryInputs(**values)


def test_recovery_state_ready_when_all_gates_pass() -> None:
    state = evaluate_recovery_state(healthy_inputs(), RecoveryConfig())

    assert state.runtime_phase == RuntimePhase.READY
    assert state.ready is True
    assert state.reasons == []


def test_recovery_state_warming_during_startup_warmup() -> None:
    state = evaluate_recovery_state(
        healthy_inputs(startup_ts=BASE - timedelta(seconds=10)),
        RecoveryConfig(warmup_min_seconds=60),
    )

    assert state.runtime_phase == RuntimePhase.WARMING
    assert "warmup_active" in state.reasons


def test_recovery_state_blocked_for_k_mutation() -> None:
    state = evaluate_recovery_state(
        healthy_inputs(k_stable=False),
        RecoveryConfig(),
    )

    assert state.runtime_phase == RuntimePhase.BLOCKED
    assert "k_unstable" in state.reasons


def test_recovery_state_degraded_for_stale_orderbook() -> None:
    state = evaluate_recovery_state(
        healthy_inputs(orderbook_fresh=False),
        RecoveryConfig(),
    )

    assert state.runtime_phase == RuntimePhase.DEGRADED
    assert "orderbook_stale" in state.reasons
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
uv run pytest -q tests/ops/test_recovery_manager.py
```

Expected: fail because module does not exist.

- [ ] **Step 3: Implement pure recovery manager**

Create `src/polymarket_engine/ops/recovery_manager.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class RuntimePhase(StrEnum):
    BOOTING = "BOOTING"
    WARMING = "WARMING"
    RECOVERING = "RECOVERING"
    DEGRADED = "DEGRADED"
    READY = "READY"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class RecoveryConfig:
    warmup_min_seconds: int = 60
    required_healthy_cycles: int = 3
    cpu_soft_max_percent: float = 20.0
    memory_soft_max_mb: int = 512
    queue_soft_max: int = 100
    max_recovery_attempts: int = 3


@dataclass(frozen=True)
class RecoveryInputs:
    boot_id: str
    startup_ts: datetime
    now: datetime
    status_ok: bool
    normalized_health_ok: bool
    api_ok: bool
    price_fresh: bool
    orderbook_fresh: bool
    probability_inputs_fresh: bool
    volatility_fresh: bool
    target_fresh: bool
    sigma_valid: bool
    k_stable: bool
    duckdb_ok: bool
    cpu_percent: float | None
    memory_mb: int | None
    queue_length: int | None
    recent_api_blocked: bool
    recent_decode_error: bool
    consecutive_healthy_cycles: int
    recovery_attempts: int


@dataclass(frozen=True)
class RecoveryState:
    runtime_phase: RuntimePhase
    ready: bool
    reasons: tuple[str, ...]
    boot_id: str
    uptime_seconds: float
    consecutive_healthy_cycles: int
    recovery_attempts: int


def evaluate_recovery_state(
    inputs: RecoveryInputs,
    config: RecoveryConfig,
) -> RecoveryState:
    uptime = max(0.0, (inputs.now - inputs.startup_ts).total_seconds())
    reasons: list[str] = []

    if uptime < config.warmup_min_seconds:
        reasons.append("warmup_active")
    if inputs.consecutive_healthy_cycles < config.required_healthy_cycles:
        reasons.append("insufficient_healthy_cycles")
    if not inputs.status_ok:
        reasons.append("status_unhealthy")
    if not inputs.normalized_health_ok:
        reasons.append("normalized_health_unhealthy")
    if not inputs.api_ok:
        reasons.append("api_unhealthy")
    if not inputs.price_fresh:
        reasons.append("price_stale")
    if not inputs.orderbook_fresh:
        reasons.append("orderbook_stale")
    if not inputs.probability_inputs_fresh:
        reasons.append("probability_inputs_stale")
    if not inputs.volatility_fresh:
        reasons.append("volatility_stale")
    if not inputs.target_fresh:
        reasons.append("target_stale")
    if not inputs.sigma_valid:
        reasons.append("sigma_invalid")
    if not inputs.k_stable:
        reasons.append("k_unstable")
    if not inputs.duckdb_ok:
        reasons.append("duckdb_unhealthy")
    if inputs.recent_api_blocked:
        reasons.append("api_blocked_recent")
    if inputs.recent_decode_error:
        reasons.append("decode_error_recent")
    if inputs.cpu_percent is not None and inputs.cpu_percent > config.cpu_soft_max_percent:
        reasons.append("cpu_above_soft_max")
    if inputs.memory_mb is not None and inputs.memory_mb > config.memory_soft_max_mb:
        reasons.append("memory_above_soft_max")
    if inputs.queue_length is not None and inputs.queue_length > config.queue_soft_max:
        reasons.append("queue_above_soft_max")

    hard_blockers = {"k_unstable", "sigma_invalid", "duckdb_unhealthy"}
    if hard_blockers.intersection(reasons):
        phase = RuntimePhase.BLOCKED
    elif inputs.recovery_attempts > config.max_recovery_attempts:
        phase = RuntimePhase.BLOCKED
        reasons.append("recovery_attempts_exceeded")
    elif "warmup_active" in reasons or "insufficient_healthy_cycles" in reasons:
        phase = RuntimePhase.WARMING
    elif reasons:
        phase = RuntimePhase.DEGRADED
    else:
        phase = RuntimePhase.READY

    return RecoveryState(
        runtime_phase=phase,
        ready=phase == RuntimePhase.READY,
        reasons=tuple(reasons),
        boot_id=inputs.boot_id,
        uptime_seconds=uptime,
        consecutive_healthy_cycles=inputs.consecutive_healthy_cycles,
        recovery_attempts=inputs.recovery_attempts,
    )
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest -q tests/ops/test_recovery_manager.py
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/ops/recovery_manager.py tests/ops/test_recovery_manager.py
git commit -m "ops: add runtime recovery phase model"
```

---

## Task 3: Add Active Offload Readiness Gate

**Files:**
- Create: `src/polymarket_engine/probability/offload_gate.py`
- Create: `tests/probability/test_offload_gate.py`

- [ ] **Step 1: Write failing tests for active offload decisions**

Create `tests/probability/test_offload_gate.py`:

```python
from __future__ import annotations

from polymarket_engine.ops.recovery_manager import RuntimePhase
from polymarket_engine.probability.offload_gate import OffloadGateConfig
from polymarket_engine.probability.offload_gate import OffloadGateInputs
from polymarket_engine.probability.offload_gate import evaluate_offload_readiness


def base_inputs(**overrides: object) -> OffloadGateInputs:
    values = {
        "runtime_phase": RuntimePhase.READY,
        "uptime_seconds": 600.0,
        "consecutive_healthy_cycles": 5,
        "price_age_ms": 200,
        "orderbook_age_ms": 200,
        "probability_input_age_ms": 200,
        "volatility_age_ms": 200,
        "target_status_age_ms": 200,
        "sigma_tau_valid": True,
        "sigma_tau_age_ms": 200,
        "k_stable": True,
        "api_status": "OK",
        "normalized_health_status": "OK",
        "duckdb_status": "OK",
        "websocket_status": "OK",
        "cpu_percent": 10.0,
        "memory_mb": 250,
        "queue_length": 0,
        "recent_api_blocked": False,
        "recent_decode_error": False,
        "configured_max_total_paths": 80_000,
        "min_total_paths": 4_000,
    }
    values.update(overrides)
    return OffloadGateInputs(**values)


def test_offload_allowed_when_ready_and_fresh() -> None:
    decision = evaluate_offload_readiness(base_inputs(), OffloadGateConfig())

    assert decision.offload_allowed is True
    assert decision.reason_codes == ()
    assert decision.recommended_worker_mode == "gpu_mc"
    assert decision.recommended_max_total_paths == 80_000


def test_offload_blocked_during_warming() -> None:
    decision = evaluate_offload_readiness(
        base_inputs(runtime_phase=RuntimePhase.WARMING),
        OffloadGateConfig(),
    )

    assert decision.offload_allowed is False
    assert "runtime_not_ready" in decision.reason_codes
    assert decision.recommended_worker_mode == "nowcast_only"
    assert decision.recommended_max_total_paths == 0


def test_offload_blocked_when_sigma_invalid() -> None:
    decision = evaluate_offload_readiness(
        base_inputs(sigma_tau_valid=False),
        OffloadGateConfig(),
    )

    assert decision.offload_allowed is False
    assert "sigma_invalid" in decision.reason_codes


def test_offload_blocks_recent_decode_error() -> None:
    decision = evaluate_offload_readiness(
        base_inputs(recent_decode_error=True),
        OffloadGateConfig(),
    )

    assert decision.offload_allowed is False
    assert "decode_error_recent" in decision.reason_codes


def test_path_budget_ramps_after_startup() -> None:
    config = OffloadGateConfig(warmup_min_seconds=30, normal_after_seconds=300)

    early = evaluate_offload_readiness(
        base_inputs(uptime_seconds=120, configured_max_total_paths=80_000),
        config,
    )

    assert early.offload_allowed is True
    assert early.recommended_worker_mode == "min_mc"
    assert early.recommended_max_total_paths == 20_000
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
uv run pytest -q tests/probability/test_offload_gate.py
```

Expected: fail because module does not exist.

- [ ] **Step 3: Implement pure offload gate**

Create `src/polymarket_engine/probability/offload_gate.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from polymarket_engine.ops.recovery_manager import RuntimePhase


WorkerMode = Literal["disabled", "nowcast_only", "min_mc", "normal_mc", "gpu_mc"]


@dataclass(frozen=True)
class OffloadGateConfig:
    warmup_min_seconds: int = 30
    required_healthy_cycles: int = 3
    normal_after_seconds: int = 300
    max_price_age_ms: int = 2_000
    max_orderbook_age_ms: int = 2_000
    max_probability_input_age_ms: int = 2_000
    max_volatility_age_ms: int = 30_000
    max_target_status_age_ms: int = 30_000
    max_sigma_tau_age_ms: int = 30_000
    cpu_soft_max_percent: float = 20.0
    memory_soft_max_mb: int = 512
    queue_soft_max: int = 100


@dataclass(frozen=True)
class OffloadGateInputs:
    runtime_phase: RuntimePhase
    uptime_seconds: float
    consecutive_healthy_cycles: int
    price_age_ms: int | None
    orderbook_age_ms: int | None
    probability_input_age_ms: int | None
    volatility_age_ms: int | None
    target_status_age_ms: int | None
    sigma_tau_valid: bool
    sigma_tau_age_ms: int | None
    k_stable: bool
    api_status: str
    normalized_health_status: str
    duckdb_status: str
    websocket_status: str
    cpu_percent: float | None
    memory_mb: int | None
    queue_length: int | None
    recent_api_blocked: bool
    recent_decode_error: bool
    configured_max_total_paths: int
    min_total_paths: int


@dataclass(frozen=True)
class OffloadDecision:
    offload_allowed: bool
    reason_codes: tuple[str, ...]
    recommended_worker_mode: WorkerMode
    recommended_max_total_paths: int


def evaluate_offload_readiness(
    inputs: OffloadGateInputs,
    config: OffloadGateConfig,
) -> OffloadDecision:
    reasons: list[str] = []

    if inputs.runtime_phase != RuntimePhase.READY:
        reasons.append("runtime_not_ready")
    if inputs.uptime_seconds < config.warmup_min_seconds:
        reasons.append("warmup_active")
    if inputs.consecutive_healthy_cycles < config.required_healthy_cycles:
        reasons.append("insufficient_healthy_cycles")
    _max_age(reasons, "price_stale", inputs.price_age_ms, config.max_price_age_ms)
    _max_age(reasons, "orderbook_stale", inputs.orderbook_age_ms, config.max_orderbook_age_ms)
    _max_age(
        reasons,
        "probability_inputs_stale",
        inputs.probability_input_age_ms,
        config.max_probability_input_age_ms,
    )
    _max_age(
        reasons,
        "volatility_stale",
        inputs.volatility_age_ms,
        config.max_volatility_age_ms,
    )
    _max_age(
        reasons,
        "target_stale",
        inputs.target_status_age_ms,
        config.max_target_status_age_ms,
    )
    if not inputs.sigma_tau_valid:
        reasons.append("sigma_invalid")
    _max_age(reasons, "sigma_stale", inputs.sigma_tau_age_ms, config.max_sigma_tau_age_ms)
    if not inputs.k_stable:
        reasons.append("k_unstable")
    if inputs.api_status.upper() != "OK":
        reasons.append("api_unhealthy")
    if inputs.normalized_health_status.upper() != "OK":
        reasons.append("normalized_health_unhealthy")
    if inputs.duckdb_status.upper() != "OK":
        reasons.append("duckdb_unhealthy")
    if inputs.websocket_status.upper() not in {"OK", "CONNECTED"}:
        reasons.append("websocket_unhealthy")
    if inputs.cpu_percent is not None and inputs.cpu_percent > config.cpu_soft_max_percent:
        reasons.append("cpu_above_soft_max")
    if inputs.memory_mb is not None and inputs.memory_mb > config.memory_soft_max_mb:
        reasons.append("memory_above_soft_max")
    if inputs.queue_length is not None and inputs.queue_length > config.queue_soft_max:
        reasons.append("queue_above_soft_max")
    if inputs.recent_api_blocked:
        reasons.append("api_blocked_recent")
    if inputs.recent_decode_error:
        reasons.append("decode_error_recent")

    if reasons:
        mode: WorkerMode = "nowcast_only" if "runtime_not_ready" in reasons else "disabled"
        return OffloadDecision(False, tuple(reasons), mode, 0)

    max_paths = _ramped_paths(
        uptime_seconds=inputs.uptime_seconds,
        configured_max_total_paths=inputs.configured_max_total_paths,
        min_total_paths=inputs.min_total_paths,
        config=config,
    )
    mode = "gpu_mc" if max_paths == inputs.configured_max_total_paths else "min_mc"
    return OffloadDecision(True, (), mode, max_paths)


def _max_age(reasons: list[str], code: str, age_ms: int | None, max_age_ms: int) -> None:
    if age_ms is None or age_ms > max_age_ms:
        reasons.append(code)


def _ramped_paths(
    *,
    uptime_seconds: float,
    configured_max_total_paths: int,
    min_total_paths: int,
    config: OffloadGateConfig,
) -> int:
    if uptime_seconds >= config.normal_after_seconds:
        return configured_max_total_paths
    if uptime_seconds < 180:
        return max(min_total_paths, int(configured_max_total_paths * 0.25))
    return max(min_total_paths, int(configured_max_total_paths * 0.50))
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest -q tests/probability/test_offload_gate.py
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/probability/offload_gate.py tests/probability/test_offload_gate.py
git commit -m "probability: add active offload readiness gate"
```

---

## Task 4: Integrate Offload Gate Into The GPU Probability Worker

**Files:**
- Modify: `src/polymarket_engine/probability/gpu_worker.py`
- Modify: `tests/probability/test_gpu_worker.py`

- [ ] **Step 1: Add failing worker regression for OFFLOAD_BLOCKED**

Append to `tests/probability/test_gpu_worker.py`:

```python
def test_worker_blocks_expensive_mc_when_offload_gate_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asof_ts = datetime.now(UTC)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_event_path = tmp_path / "probability-events.jsonl"

    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": asof_ts.isoformat(),
                "rows": [
                    {
                        "contract": "BTC 5m UP",
                        "contract_id": "btc-up",
                        "market_slug": "btc-updown-5m",
                        "start_ts": asof_ts.isoformat(),
                        "expiry_ts": (asof_ts + timedelta(minutes=5)).isoformat(),
                        "flags": ["OK"],
                        "probability_input": ProbabilityInput(
                            state_id="state-btc-up",
                            asof_ts=asof_ts,
                            asset="BTC",
                            side="UP",
                            comparison_operator=">=",
                            seconds_left=300.0,
                            settlement_price=70_100.0,
                            threshold=70_000.0,
                            sigma_tau=0.012,
                            executable_price=0.52,
                            source_age_ms=100,
                            book_age_ms=100,
                            z_path=0.12,
                        ).to_json_dict(),
                        "volatility_regime": "normal",
                    }
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    def fail_if_mc_runs(*_: object, **__: object) -> ProbabilityOutput:
        raise AssertionError("MC should be blocked by offload gate")

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fail_if_mc_runs,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_event_path=probability_event_path,
        budget=ProbabilityWorkerBudget(max_total_paths=80_000),
        offload_decision_override={
            "offload_allowed": False,
            "reason_codes": ["runtime_not_ready"],
            "recommended_worker_mode": "nowcast_only",
            "recommended_max_total_paths": 0,
        },
    )

    assert payload["state"] == "OFFLOAD_BLOCKED"
    assert payload["offload"]["offload_allowed"] is False
    assert payload["offload"]["reason_codes"] == ["runtime_not_ready"]
    assert payload["rows"][0]["probability_kind"] == "NOWCAST"
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
uv run pytest -q tests/probability/test_gpu_worker.py::test_worker_blocks_expensive_mc_when_offload_gate_blocks
```

Expected: fail because worker does not accept or enforce an offload decision.

- [ ] **Step 3: Add an injectable gate result to the worker cycle**

Modify `run_cuda_probability_worker_cycle` signature:

```python
def run_cuda_probability_worker_cycle(
    *,
    duckdb_path: Path,
    probability_status_path: Path,
    probability_inputs_path: Path | None = None,
    probability_fragments_path: Path | None = None,
    limit: int = DEFAULT_GPU_PROBABILITY_LIMIT,
    valid_seconds: int = int(DEFAULT_PROBABILITY_GRID_VALID_SECONDS),
    max_state_age_seconds: float | None = DEFAULT_PROBABILITY_MAX_STATE_AGE_SECONDS,
    max_input_snapshot_age_seconds: float | None = DEFAULT_INPUT_SNAPSHOT_MAX_AGE_SECONDS,
    probability_event_path: Path | None = None,
    budget: ProbabilityWorkerBudget | None = None,
    offload_decision_override: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
```

- [ ] **Step 4: Enforce the gate after nowcast rows are written and before MC loop**

Insert after nowcast status write:

```python
offload_decision = _offload_decision_from_override(offload_decision_override)
if offload_decision is not None and not offload_decision["offload_allowed"]:
    blocked_rows, _ = _merge_missing_retained_mc_rows(
        fresh_rows=retained_mc_rows,
        previous_rows=previous_rows,
        now=generated_at,
        enabled=True,
    )
    payload = _status_payload(
        generated_at=generated_at,
        rows=blocked_rows or nowcast_rows,
        nowcast_rows=nowcast_rows,
        skipped=quality_skipped,
        errors=[],
        rows_seen=len(inputs),
        rows_written=0,
        last_good_rows=blocked_rows or previous_rows or None,
        state_override="OFFLOAD_BLOCKED",
        retained_mc_rows=len(blocked_rows),
        budget=_budget_diagnostics(...),
    )
    payload["offload"] = offload_decision
    _write_status(probability_status_path, payload)
    return payload
```

Use the existing `_budget_diagnostics(...)` arguments from the NOWCAST status block.

- [ ] **Step 5: Add helper to normalize gate override**

Add near worker helpers:

```python
def _offload_decision_from_override(
    value: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "offload_allowed": bool(value.get("offload_allowed")),
        "reason_codes": list(value.get("reason_codes") or []),
        "recommended_worker_mode": str(value.get("recommended_worker_mode") or "disabled"),
        "recommended_max_total_paths": int(value.get("recommended_max_total_paths") or 0),
    }
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run pytest -q tests/probability/test_offload_gate.py tests/probability/test_gpu_worker.py
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/polymarket_engine/probability/gpu_worker.py tests/probability/test_gpu_worker.py
git commit -m "probability: block expensive work when offload gate fails"
```

---

## Task 5: Persist Recovery And Offload Status Through Runtime API

**Files:**
- Modify: `src/polymarket_engine/runtime_api.py`
- Modify: `tests/test_runtime_api.py`

- [ ] **Step 1: Add failing API tests**

Append to `tests/test_runtime_api.py`:

```python
def test_runtime_api_exposes_recovery_status(tmp_path: Path) -> None:
    recovery_path = tmp_path / "live" / "recovery_status.json"
    recovery_path.parent.mkdir(parents=True)
    recovery_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-recovery-runtime-v1",
                "generated_at": "2026-06-11T12:00:00+00:00",
                "runtime_phase": "WARMING",
                "ready": False,
                "reasons": ["warmup_active"],
                "boot_id": "boot-1",
            }
        ),
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(build_runtime_router(data_dir=tmp_path, recovery_status_path=recovery_path))
    client = TestClient(app)

    response = client.get("/api/runtime/recovery")

    assert response.status_code == 200
    assert response.json()["runtime_phase"] == "WARMING"
    assert response.json()["reasons"] == ["warmup_active"]


def test_runtime_live_includes_compact_recovery_and_offload(tmp_path: Path) -> None:
    live_dir = tmp_path / "live"
    live_dir.mkdir(parents=True)
    recovery_path = live_dir / "recovery_status.json"
    offload_path = live_dir / "offload_status.json"
    recovery_path.write_text(
        json.dumps({"runtime_phase": "READY", "ready": True, "reasons": [], "boot_id": "boot-1"}),
        encoding="utf-8",
    )
    offload_path.write_text(
        json.dumps({"offload_allowed": True, "reason_codes": [], "recommended_worker_mode": "gpu_mc"}),
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(
        build_runtime_router(
            data_dir=tmp_path,
            recovery_status_path=recovery_path,
            offload_status_path=offload_path,
        )
    )
    client = TestClient(app)

    payload = client.get("/api/runtime/live").json()

    assert payload["recovery"]["runtime_phase"] == "READY"
    assert payload["offload"]["offload_allowed"] is True
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_runtime_api.py -k "recovery_status or compact_recovery"
```

Expected: fail because router does not accept these paths or expose routes.

- [ ] **Step 3: Add router path arguments**

Modify `build_runtime_router`:

```python
def build_runtime_router(
    *,
    status_path: Path = Path("data/live/status.json"),
    ...
    recovery_status_path: Path = Path("data/live/recovery_status.json"),
    offload_status_path: Path = Path("data/live/offload_status.json"),
    bug_report_dir: Path = Path("data/live/bug-reports"),
    ...
) -> APIRouter:
```

- [ ] **Step 4: Add routes**

Add:

```python
@router.get("/recovery")
def runtime_recovery() -> dict[str, Any]:
    return _read_optional_status_payload(
        recovery_status_path,
        missing_state="MISSING",
        default={"runtime_phase": "UNKNOWN", "ready": False, "reasons": ["recovery_status_missing"]},
    )


@router.get("/offload")
def runtime_offload() -> dict[str, Any]:
    return _read_optional_status_payload(
        offload_status_path,
        missing_state="MISSING",
        default={
            "offload_allowed": False,
            "reason_codes": ["offload_status_missing"],
            "recommended_worker_mode": "disabled",
        },
    )
```

- [ ] **Step 5: Include compact state in `/live`**

Inside `_runtime_live_payload`, add:

```python
"recovery": _compact_recovery_status(recovery_status_path),
"offload": _compact_offload_status(offload_status_path),
```

Use helpers that never raise if files are missing or invalid.

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run pytest -q tests/test_runtime_api.py tests/test_runtime_gates.py
```

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/polymarket_engine/runtime_api.py tests/test_runtime_api.py
git commit -m "api: expose recovery and offload runtime state"
```

---

## Task 6: Harden API Decode Classification In The TUI Client

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/client.rs`

- [ ] **Step 1: Add Rust regression for non-JSON API body**

In `rust/crates/polymarket-cockpit-tui/src/client.rs`, add a tokio test:

```rust
#[tokio::test]
async fn status_request_classifies_non_json_body() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = listener.local_addr().unwrap();
    let _server = thread::spawn(move || {
        let Ok((mut stream, _peer)) = listener.accept() else {
            return;
        };
        let mut buffer = [0; 512];
        let _ = stream.read(&mut buffer).unwrap();
        let body = "<html>blocked</html>";
        let response = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {}\r\n\r\n{}",
            body.len(),
            body
        );
        stream.write_all(response.as_bytes()).unwrap();
    });

    let client = EngineClient::with_request_timeout(
        format!("http://{address}"),
        Duration::from_millis(500),
    );

    let result = client.status().await;

    assert!(result.is_err());
    assert!(format!("{:#}", result.unwrap_err()).contains("API_BLOCKED"));
}
```

- [ ] **Step 2: Run Rust test and verify RED**

Run:

```bash
cargo test -p polymarket-cockpit-tui status_request_classifies_non_json_body
```

Expected: fail because error is generic decode failure.

- [ ] **Step 3: Check status and content type before JSON decode**

Modify `get_json` in `client.rs`:

```rust
let response = self.client.get(url).send().await?;
let status = response.status();
let content_type = response
    .headers()
    .get(reqwest::header::CONTENT_TYPE)
    .and_then(|value| value.to_str().ok())
    .unwrap_or("")
    .to_string();
let body = response.text().await?;
if !status.is_success() {
    anyhow::bail!(
        "API_BLOCKED status={} content_type={} body_prefix={}",
        status.as_u16(),
        content_type,
        body.chars().take(120).collect::<String>()
    );
}
if !content_type.contains("json") {
    anyhow::bail!(
        "API_BLOCKED status={} content_type={} body_prefix={}",
        status.as_u16(),
        content_type,
        body.chars().take(120).collect::<String>()
    );
}
Ok(serde_json::from_str(&body)?)
```

- [ ] **Step 4: Run Rust tests**

Run:

```bash
cargo test -p polymarket-cockpit-tui status_request_times_out_on_half_open_api monitor_request_includes_limit_and_parses_payload status_request_classifies_non_json_body
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add rust/crates/polymarket-cockpit-tui/src/client.rs
git commit -m "tui: classify blocked API responses before JSON decode"
```

---

## Task 7: Add TUI Runtime And Offload Visibility

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/status.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/systems.rs`

- [ ] **Step 1: Add status structs for recovery/offload**

In `status.rs`, add:

```rust
#[derive(Debug, Clone, Default, Deserialize)]
pub struct RuntimeRecoverySummary {
    #[serde(default)]
    pub runtime_phase: String,
    #[serde(default)]
    pub ready: bool,
    #[serde(default)]
    pub reasons: Vec<String>,
    #[serde(default)]
    pub boot_id: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct RuntimeOffloadSummary {
    #[serde(default)]
    pub offload_allowed: bool,
    #[serde(default)]
    pub reason_codes: Vec<String>,
    #[serde(default)]
    pub recommended_worker_mode: String,
}
```

Add optional fields to `RuntimeLive`:

```rust
#[serde(default)]
pub recovery: RuntimeRecoverySummary,
#[serde(default)]
pub offload: RuntimeOffloadSummary,
```

- [ ] **Step 2: Add render output in systems panel**

In `render/systems.rs`, include rows:

```rust
("Phase", live.recovery.runtime_phase.as_str()),
("Offload", if live.offload.offload_allowed { "ALLOWED" } else { "BLOCKED" }),
("Worker", live.offload.recommended_worker_mode.as_str()),
("Boot", live.recovery.boot_id.as_deref().unwrap_or("-")),
```

For reasons, join the first two reason codes:

```rust
let reasons = if live.offload.reason_codes.is_empty() {
    "-".to_string()
} else {
    live.offload.reason_codes.iter().take(2).cloned().collect::<Vec<_>>().join(",")
};
```

- [ ] **Step 3: Add deserialize/render tests**

Add a unit test in `status.rs` or `render/systems.rs` that deserializes `/api/runtime/live` with:

```json
{
  "recovery": {"runtime_phase": "WARMING", "ready": false, "reasons": ["warmup_active"], "boot_id": "boot-1"},
  "offload": {"offload_allowed": false, "reason_codes": ["runtime_not_ready"], "recommended_worker_mode": "nowcast_only"}
}
```

Expected: parsed struct has `runtime_phase == "WARMING"` and `offload_allowed == false`.

- [ ] **Step 4: Run Rust tests**

Run:

```bash
cargo test -p polymarket-cockpit-tui
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add rust/crates/polymarket-cockpit-tui/src/status.rs rust/crates/polymarket-cockpit-tui/src/event_loop.rs rust/crates/polymarket-cockpit-tui/src/render/systems.rs
git commit -m "tui: show recovery and offload state"
```

---

## Task 8: Add K Threshold Immutability Diagnostics

**Files:**
- Modify: `src/polymarket_engine/domain/contract_rules.py`
- Modify: `src/polymarket_engine/features/state_builder.py`
- Modify: `src/polymarket_engine/probability/runtime_inputs.py`
- Modify: `tests/domain/test_contract_rules.py`
- Modify: `tests/probability/test_hot_inputs.py`

- [ ] **Step 1: Add failing test for threshold mutation block**

In the relevant runtime-input or hot-input test file, create a state history with the same `contract_id` receiving two threshold values without a rule hash change.

Expected output:

```python
assert row["probability_state"] == "BLOCKED"
assert "THRESHOLD_MUTATION_ERROR" in row["flags"]
assert row["k_stable"] is False
```

- [ ] **Step 2: Add K assignment diagnostics**

When K/threshold is resolved, include:

```python
{
    "contract_id": contract_id,
    "market_slug": market_slug,
    "asset": asset,
    "side": side,
    "K": threshold,
    "K_source": threshold_source,
    "rule_hash": rule_hash,
    "timestamp": observed_ts.isoformat(),
    "previous_K": previous_threshold,
    "new_K": threshold,
    "reason_for_change": reason,
}
```

- [ ] **Step 3: Block unexpected mutation**

If `previous_K is not None`, `new_K != previous_K`, and `rule_hash` did not change, add:

```python
flags.append("THRESHOLD_MUTATION_ERROR")
probability_state = "BLOCKED"
k_stable = False
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest -q tests/domain/test_contract_rules.py tests/probability/test_hot_inputs.py
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/domain/contract_rules.py src/polymarket_engine/features/state_builder.py src/polymarket_engine/probability/runtime_inputs.py tests/domain/test_contract_rules.py tests/probability/test_hot_inputs.py
git commit -m "probability: block unexpected threshold mutation"
```

---

## Task 9: Add Sigma Validity Diagnostics

**Files:**
- Modify: `src/polymarket_engine/features/volatility.py`
- Modify: `src/polymarket_engine/probability/hot_inputs.py`
- Modify: `src/polymarket_engine/probability/runtime_inputs.py`
- Modify: `tests/probability/test_hot_inputs.py`

- [ ] **Step 1: Add failing tests for invalid sigma blocking**

Add cases where `sigma_tau` is missing, `NaN`, non-positive, or stale.

Expected:

```python
assert row["sigma_valid"] is False
assert row["probability_state"] == "BLOCKED_OR_STALE"
assert row["offload_allowed"] is False
assert "sigma_invalid" in row["block_reasons"]
```

- [ ] **Step 2: Emit sigma diagnostics**

Add fields to probability input rows:

```python
"sigma_tau": sigma_tau,
"sigma_valid": sigma_valid,
"sigma_age_ms": sigma_age_ms,
"last_sigma_update_ts": last_sigma_update_ts,
"short_vol": short_vol,
"medium_vol": medium_vol,
"long_vol": long_vol,
"volatility_floor_applied": volatility_floor_applied,
"regime_multiplier_applied": regime_multiplier_applied,
"failure_reason": failure_reason,
"input_sample_count": input_sample_count,
```

- [ ] **Step 3: Block offload on invalid sigma**

Ensure invalid sigma flows into recovery/offload inputs as:

```python
sigma_tau_valid=False
```

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest -q tests/probability/test_hot_inputs.py tests/probability/test_offload_gate.py
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/features/volatility.py src/polymarket_engine/probability/hot_inputs.py src/polymarket_engine/probability/runtime_inputs.py tests/probability/test_hot_inputs.py
git commit -m "probability: surface sigma validity diagnostics"
```

---

## Task 10: Add Structured Bug Report Pipeline

**Files:**
- Create: `src/polymarket_engine/diagnostics/__init__.py`
- Create: `src/polymarket_engine/diagnostics/bug_report.py`
- Create: `tests/diagnostics/test_bug_report.py`
- Modify: `src/polymarket_engine/runtime_api.py`
- Modify: `tests/test_runtime_api.py`

- [ ] **Step 1: Add failing tests for bug report schema and prompt**

Create `tests/diagnostics/test_bug_report.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime

from polymarket_engine.diagnostics.bug_report import BugReport
from polymarket_engine.diagnostics.bug_report import render_llm_prompt


def test_bug_report_prompt_is_llm_ready() -> None:
    report = BugReport(
        bug_id="bug-001",
        boot_id="boot-1",
        timestamp=datetime(2026, 6, 11, 12, 0, tzinfo=UTC),
        runtime_phase="DEGRADED",
        service="tui",
        severity="CRITICAL",
        contract_id="btc-up",
        market_slug="btc-updown-5m",
        asset="BTC",
        side="UP",
        tte_seconds=120.0,
        k=70_000.0,
        current_price=70_050.0,
        price_age_ms=100,
        orderbook_age_ms=100,
        sigma_tau=0.012,
        sigma_valid=True,
        probability_state="OFFLOAD_BLOCKED",
        offload_allowed=False,
        offload_block_reasons=("runtime_not_ready",),
        api_status="OK",
        websocket_status="OK",
        duckdb_status="OK",
        cpu_percent=35.0,
        memory_mb=450,
        queue_length=200,
        last_error="TUI receive lag exceeded threshold",
        stack_trace=None,
        recent_logs=("lag=5000ms",),
        suspected_module="rust/crates/polymarket-cockpit-tui/src/event_loop.rs",
        suggested_files_to_inspect=("rust/crates/polymarket-cockpit-tui/src/event_loop.rs",),
        suggested_tests_to_run=("cargo test -p polymarket-cockpit-tui",),
    )

    prompt = render_llm_prompt(report)

    assert "A runtime bug occurred in the Polymarket probability engine" in prompt
    assert "bug-001" in prompt
    assert "Do not change unrelated architecture" in prompt
```

- [ ] **Step 2: Run test and verify RED**

Run:

```bash
uv run pytest -q tests/diagnostics/test_bug_report.py
```

Expected: fail because module does not exist.

- [ ] **Step 3: Implement bug report dataclass and prompt**

Create `src/polymarket_engine/diagnostics/bug_report.py` with a frozen dataclass matching the test fields, a `to_json_dict()` method, and:

```python
def render_llm_prompt(report: BugReport) -> str:
    return (
        "A runtime bug occurred in the Polymarket probability engine. "
        "Diagnose the likely cause and propose a minimal safe patch. "
        "Use the bug report, stack trace, recent logs, and relevant source files. "
        "Do not change unrelated architecture. Add or update tests. "
        "Explain the root cause, the fix, and how to verify it.\n\n"
        f"Bug report:\n{json.dumps(report.to_json_dict(), indent=2, sort_keys=True)}\n"
    )
```

- [ ] **Step 4: Add bug report API list endpoint**

In `runtime_api.py`, add `/api/runtime/bug-reports` that returns the newest JSON reports from `bug_report_dir`.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest -q tests/diagnostics/test_bug_report.py tests/test_runtime_api.py
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/polymarket_engine/diagnostics tests/diagnostics src/polymarket_engine/runtime_api.py tests/test_runtime_api.py
git commit -m "diagnostics: add structured runtime bug reports"
```

---

## Task 11: Add Runtime Recovery Status Writer

**Files:**
- Modify: `src/polymarket_engine/ops/recovery_manager.py`
- Modify: `src/polymarket_engine/ops/runtime_keeper.py`
- Modify: `tests/ops/test_recovery_manager.py`
- Modify: `tests/ops/test_runtime_keeper.py`

- [ ] **Step 1: Add failing test that recovery status is written atomically**

Expected file:

```json
{
  "schema_version": "polymarket-recovery-runtime-v1",
  "runtime_phase": "WARMING",
  "ready": false,
  "reasons": ["warmup_active"],
  "boot_id": "boot-1"
}
```

- [ ] **Step 2: Implement `write_recovery_status`**

Use existing atomic replace helper if available; otherwise temp-write and replace:

```python
temp = path.with_suffix(".json.tmp")
temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
temp.replace(path)
```

- [ ] **Step 3: Runtime keeper writes recovery state from current checks**

Map failed checks into `RecoveryInputs`. If exact freshness values are missing, use conservative booleans:

```python
status_ok = check_by_name["api:/api/runtime/live"].ok
api_ok = check_by_name["api:/health"].ok
normalized_health_ok = not any("normalized" in failed.name for failed in failed_checks)
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest -q tests/ops/test_recovery_manager.py tests/ops/test_runtime_keeper.py tests/test_runtime_api.py
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/ops/recovery_manager.py src/polymarket_engine/ops/runtime_keeper.py tests/ops/test_recovery_manager.py tests/ops/test_runtime_keeper.py
git commit -m "ops: write runtime recovery status"
```

---

## Task 12: Add Calibration Dataset Logging

**Files:**
- Create: `src/polymarket_engine/calibration/__init__.py`
- Create: `src/polymarket_engine/calibration/dataset.py`
- Create: `tests/calibration/test_dataset.py`

- [ ] **Step 1: Add failing dataset row test**

Create a test that builds a row with:

```python
state_id
contract_id
market_slug
asset
side
asof_ts
expiry_ts
tte_seconds
k
current_price
distance_to_threshold
z_path
sigma_tau
p_finish_mc
p_no_touch_mc
spread
best_bid
best_ask
midpoint
visible_depth
orderbook_imbalance
quote_age_ms
source_age_ms
volatility_regime
probability_model_version
skip_or_block_reason
```

Assert `final_label`, `resolved_outcome`, and `settlement_price_at_expiry` default to `None` until labels are joined after expiry.

- [ ] **Step 2: Implement replay-safe dataset row schema**

Use a frozen dataclass and explicit `to_json_dict()` method. Do not read future labels in this task.

- [ ] **Step 3: Add append helper**

Write newline-delimited JSON to:

```text
data/research/calibration/asof_decision_states.jsonl
```

Do not replace raw runtime artifacts.

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest -q tests/calibration/test_dataset.py
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/calibration tests/calibration/test_dataset.py
git commit -m "calibration: add replay-safe decision dataset rows"
```

---

## Task 13: Add Calibration Reports Before ML

**Files:**
- Create: `src/polymarket_engine/calibration/reports.py`
- Create: `tests/calibration/test_reports.py`

- [ ] **Step 1: Add failing metric tests**

Test Brier score, log loss, ECE buckets, and per-slice sample counts with a small fixed dataset.

Expected:

```python
assert round(report.brier_score, 4) == 0.1825
assert report.bucket_counts["tte_0_60"] == 2
assert report.min_bucket_sample_count == 1
```

- [ ] **Step 2: Implement deterministic report helpers**

Support slices:

```text
TTE bucket
z_path bucket
distance bucket
volatility regime
asset
side
spread/depth bucket
orderbook imbalance bucket
final 30-60 second window
threshold congestion bucket
```

- [ ] **Step 3: Add CLI command only after pure tests pass**

Modify `src/polymarket_engine/cli.py` to add:

```bash
polymarket-engine calibration-report --input data/research/calibration/asof_decision_states.jsonl --out reports/calibration.json
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest -q tests/calibration/test_reports.py tests/test_cli.py
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/calibration/reports.py tests/calibration/test_reports.py src/polymarket_engine/cli.py tests/test_cli.py
git commit -m "calibration: add probability calibration reports"
```

---

## Task 15: End-To-End Verification And Deploy Check

**Files:**
- Modify only as needed based on verification failures.

- [ ] **Step 1: Run focused Python verification**

Run:

```bash
uv run pytest -q tests/ops/test_recovery_manager.py tests/probability/test_offload_gate.py tests/probability/test_gpu_worker.py tests/test_runtime_api.py tests/diagnostics/test_bug_report.py
```

Expected: all pass.

- [ ] **Step 2: Run Rust TUI verification**

Run:

```bash
cargo test -p polymarket-cockpit-tui
```

Expected: all pass.

- [ ] **Step 3: Run full repo checks once**

Run:

```bash
uv run ruff check .
uv run mypy src tests
uv run pytest -q
```

Expected: pass, except pre-existing unrelated failures must be documented with exact test names and error summaries.

- [ ] **Step 4: Runtime smoke check on deployed lane before claiming operational success**

After deployment to THEPC, run:

```bash
curl -sS http://127.0.0.1:8000/api/runtime/recovery | jq .
curl -sS http://127.0.0.1:8000/api/runtime/offload | jq .
curl -sS 'http://127.0.0.1:8000/api/runtime/live?limit=8' | jq '.recovery,.offload'
curl -sS 'http://127.0.0.1:8000/api/runtime/probabilities?limit=8' | jq '.state,.offload,.rows[0].probability_kind'
```

Expected during warmup:

```text
runtime_phase is WARMING or DEGRADED
offload_allowed is false
probability state is OFFLOAD_BLOCKED or NOWCAST
no confident MC rows are emitted from stale/invalid inputs
```

Expected after readiness:

```text
runtime_phase is READY
offload_allowed is true
probability worker mode is gpu_mc or normal_mc
MC rows resume
```

- [ ] **Step 5: TUI smoke check**

Run the cockpit and verify:

```text
TUI remains responsive if /api/runtime/probabilities returns non-JSON or 502
systems panel shows runtime phase
systems panel shows offload allowed/blocked
blocked reasons are visible
price/orderbook panels continue to update when probability is blocked
```

- [ ] **Step 6: Commit final docs update**

Update `docs/SPOON_DEPLOYMENT.md` or a runtime runbook with:

```text
How to read recovery status
How to read offload blocked reasons
Warmup behavior after restart
Why nowcast/last-good can display while MC is blocked
Where bug reports are written
What must be true before ML calibration work starts
```

Commit:

```bash
git add docs/SPOON_DEPLOYMENT.md
git commit -m "docs: document runtime recovery and offload gates"
```

---

## Execution Order

1. Task 0: normalize observations doc.
2. Task 1: preserve evidence before fixes.
3. Task 2: recovery phase model.
4. Task 3: pure offload gate.
5. Task 4: active worker blocking.
6. Task 5: API surfaces.
7. Task 6: TUI API decode classification.
8. Task 7: TUI visibility.
9. Task 8: K mutation blocking.
10. Task 9: sigma diagnostics.
11. Task 10: bug report pipeline.
12. Task 11: recovery writer integration.
13. Task 12: calibration dataset.
14. Task 13: calibration reports.
15. Task 15: verification and deployed smoke checks.

## Risk Controls

- Do not start with ML.
- Do not treat `docs/observations.md` suspected causes as confirmed root causes.
- Do not let the TUI block on probability availability.
- Do not let last-good output look fresh; label it stale/last-good.
- K mutation and invalid sigma are hard blockers.
- Offload blocking must never stop raw collection if collection itself is safe.
- API/TUI changes must be backward-compatible with missing recovery/offload files.
- Deployed verification is required before claiming the runtime behavior is fixed.

## Self-Review

- Spec coverage: every BUG section in `docs/observations.md` maps to at least one task. BUG-001 maps to Tasks 1, 6, 7, and 10. BUG-002 maps to Tasks 1, 6, and 10. BUG-003 maps to Tasks 2, 5, 11, and 15. BUG-004 maps to Tasks 3 and 4. BUG-005 maps to Task 9. BUG-006 maps to Task 8. BUG-007 maps to Tasks 12 and 13. BUG-008 maps to Tasks 2, 5, and 11. BUG-009 maps to Task 10.
- Placeholder scan: no `TODO`, `TBD`, or `implement later` placeholders are used as plan content.
- Type consistency: runtime phases use `RuntimePhase`; offload result uses `OffloadDecision`; API surfaces use recovery/offload status JSON names consistently.
- Scope check: this is a master plan with independent tasks. Runtime stability tasks should be implemented before calibration tasks.
