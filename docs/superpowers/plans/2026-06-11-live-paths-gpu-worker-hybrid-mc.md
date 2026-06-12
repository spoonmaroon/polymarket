# Live Paths, GPU Worker, And Hybrid MC Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the browser UI show truthful live path status, restore GPU Monte Carlo rows for eligible live inputs on THEPC, and fold `docs/observations pt 2` into the offline hybrid Monte Carlo calibration roadmap.

**Architecture:** Split global runtime readiness from per-input Monte Carlo eligibility. The worker should keep NOWCAST rows visible for every live input, run MC only for rows that pass per-input freshness and safety checks, and publish exact offload diagnostics for blocked rows. The browser UI should draw `simulation_preview` when present and explain why live paths are blocked or partial when MC rows are absent. The ML/BART work stays documented as offline calibration and uncertainty research, not live decision logic.

**Tech Stack:** Python 3.11 with `uv`, FastAPI runtime API, DuckDB-backed live artifacts, React/Vite/TypeScript browser UI, Docker Compose on THEPC, pytest, TypeScript build checks.

---

## Debug Evidence

- THEPC containers were running in WSL Docker, not Docker Desktop. The API and `gpu-probability-worker` were up and the API was healthy.
- `/api/runtime/probabilities?limit=8` returned `state="OFFLOAD_BLOCKED"` or NOWCAST-only rows. `rows_written` was `0`, `allocated_total_paths` was `0`, and rows had no `simulation_preview`.
- `nvidia-smi` showed the RTX 5060 Ti available but mostly idle. That is expected while offload is blocked.
- Worker logs showed `lanes={"NOWCAST":4}` or `{"NOWCAST":8}` with no MC lane.
- The live blockers were `probability_inputs_stale` and sometimes `price_stale`.
- `src/polymarket_engine/probability/gpu_worker.py` computes one global offload decision from max input ages across all inputs. One stale input can block MC for every input.
- `ui/src/App.tsx` already has `MonteCarloCanvas`, but it only draws when the API row includes `simulation_preview`. The UI cannot draw live paths from NOWCAST rows alone.
- `docs/observations pt 2` is an offline hybrid MC plus ML calibration and BART uncertainty roadmap. It must not become live trading or live decision-gate behavior.

## File Structure

- Modify `src/polymarket_engine/probability/gpu_worker.py`
  - Add per-input MC eligibility helpers.
  - Keep global recovery/system blockers as global blockers.
  - Write offload input diagnostics.
  - Allow partial MC when some inputs are fresh and safe.
- Modify `src/polymarket_engine/runtime_api.py`
  - Preserve new offload diagnostic fields in `/api/runtime/live` and `/api/runtime/offload`.
- Modify `ui/src/App.tsx`
  - Add typed offload diagnostic fields.
  - Add a live path status panel and per-row fallback copy.
  - Keep the existing SVG path renderer for rows with `simulation_preview`.
- Modify `ui/src/styles.css`
  - Add compact styles for live path status and blocked input chips.
- Modify `tests/probability/test_gpu_worker.py`
  - Add regressions for partial MC, expired input blocking, and offload diagnostics.
- Modify `tests/test_runtime_api.py`
  - Add compact offload diagnostic API regression.
- Modify `tests/ui/probability_rows_test.ts` or add `tests/ui/live_path_status_test.ts`
  - Add pure helpers for path status so UI behavior is testable without a browser.
- Modify `docs/observations.md`
  - Merge the hybrid MC calibration roadmap from `docs/observations pt 2`.
- Delete `docs/observations pt 2`
  - Remove the untracked duplicate after merge.
- Modify `docs/superpowers/plans/2026-06-11-observations-runtime-recovery.md`
  - Remove the deferred first-calibrator tombstone section and execution-order references.
- Modify `docs/SPOON_DEPLOYMENT.md`
  - Document live path/offload diagnostics and THEPC verification commands.

## Delegation Plan

- Subagent A: Worker regressions and worker implementation in `gpu_worker.py`.
- Subagent B: Runtime API and browser UI status rendering.
- Subagent C: Documentation merge and stale plan cleanup.
- Main session: THEPC deployment, live checks, commit, push, and PR/GitHub update.

---

## Task 1: Add Worker Regressions For Partial MC And Data Safety

**Files:**
- Modify: `tests/probability/test_gpu_worker.py`

- [ ] **Step 1: Add test helpers near the existing worker tests**

Add these helpers after `_write_ready_recovery_status`:

```python
def _runtime_input_snapshot_row(
    *,
    asof_ts: datetime,
    state_id: str,
    asset: str,
    side: str,
    source_age_ms: int = 100,
    book_age_ms: int = 100,
    seconds_left: float = 300.0,
    expiry_offset_seconds: float = 300.0,
    probability_state: str = "READY",
    offload_allowed: bool = True,
    block_reasons: list[str] | None = None,
) -> dict[str, object]:
    probability_input = ProbabilityInput(
        state_id=state_id,
        asof_ts=asof_ts,
        asset=asset,
        side=side,
        comparison_operator=">=" if side == "UP" else "<",
        seconds_left=seconds_left,
        settlement_price=70_100.0 if asset == "BTC" else 3_600.0,
        threshold=70_000.0 if asset == "BTC" else 3_580.0,
        sigma_tau=0.012,
        executable_price=0.52 if side == "UP" else 0.48,
        source_age_ms=source_age_ms,
        book_age_ms=book_age_ms,
        z_path=0.12,
    )
    return {
        "contract": f"{asset} 5m {side}",
        "contract_id": f"{asset.lower()}-{side.lower()}",
        "market_slug": f"{asset.lower()}-updown-5m",
        "start_ts": asof_ts.isoformat(),
        "expiry_ts": (asof_ts + timedelta(seconds=expiry_offset_seconds)).isoformat(),
        "flags": ["OK"] if probability_state == "READY" else ["BLOCKED"],
        "probability_state": probability_state,
        "offload_allowed": offload_allowed,
        "block_reasons": block_reasons or [],
        "probability_input": probability_input.to_json_dict(),
        "volatility_regime": "normal",
    }
```

- [ ] **Step 2: Add a failing test for one stale sibling not blocking every row**

Append this test:

```python
def test_worker_runs_mc_for_fresh_input_when_sibling_source_is_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    asof_ts = now - timedelta(seconds=4)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_event_path = tmp_path / "probability-events.jsonl"
    offload_status_path = tmp_path / "offload_status.json"
    _write_ready_recovery_status(probability_status_path.with_name("recovery_status.json"))
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": now.isoformat(),
                "rows": [
                    _runtime_input_snapshot_row(
                        asof_ts=asof_ts,
                        state_id="state-btc-stale",
                        asset="BTC",
                        side="UP",
                        source_age_ms=1300,
                    ),
                    _runtime_input_snapshot_row(
                        asof_ts=asof_ts,
                        state_id="state-eth-fresh",
                        asset="ETH",
                        side="UP",
                        source_age_ms=100,
                    ),
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    def fake_ensemble(
        probability_input: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
        history_fragments: object | None,
    ) -> ProbabilityOutput:
        return ProbabilityOutput(
            state_id=probability_input.state_id,
            asof_ts=probability_input.asof_ts,
            p_finish=0.62,
            p_no_touch=0.58,
            z_path=probability_input.z_path,
            model_version="ensemble-v1",
            seed=seed,
            diagnostics={
                "path_count": path_count,
                "paths_per_generator": path_count,
                "generator_count": 4,
                "simulation_preview": {
                    "sampled_paths": [
                        {
                            "index": 0,
                            "terminal_win": True,
                            "no_touch_win": True,
                            "points": [probability_input.settlement_price, probability_input.threshold],
                        }
                    ],
                    "start_price": probability_input.settlement_price,
                    "threshold": probability_input.threshold,
                    "steps": steps,
                    "terminal_win_count": 1,
                },
            },
        )

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fake_ensemble,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        offload_status_path=offload_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_event_path=probability_event_path,
        budget=ProbabilityWorkerBudget(max_total_paths=80_000),
    )

    rows_by_state = {row["state_id"]: row for row in payload["rows"]}
    assert rows_by_state["state-eth-fresh"]["probability_kind"] == "MC"
    assert rows_by_state["state-eth-fresh"]["simulation_preview"]["sampled_paths"]
    assert rows_by_state["state-btc-stale"]["probability_kind"] == "NOWCAST"
    assert rows_by_state["state-btc-stale"]["block_reasons"] == ["price_stale"]
    assert payload["offload"]["offload_allowed"] is True
    assert payload["offload"]["mc_eligible_input_count"] == 1
    assert payload["offload"]["blocked_input_count"] == 1
```

- [ ] **Step 3: Add a failing test for expired stale input staying blocked**

Append this test:

```python
def test_worker_blocks_expired_probability_input_even_when_snapshot_is_fresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    asof_ts = now - timedelta(seconds=25)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_event_path = tmp_path / "probability-events.jsonl"
    _write_ready_recovery_status(probability_status_path.with_name("recovery_status.json"))
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": now.isoformat(),
                "rows": [
                    _runtime_input_snapshot_row(
                        asof_ts=asof_ts,
                        state_id="state-expired",
                        asset="BTC",
                        side="UP",
                        seconds_left=0.0,
                        expiry_offset_seconds=-1.0,
                    )
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )

    def fail_if_mc_runs(*_: object, **__: object) -> ProbabilityOutput:
        raise AssertionError("MC must not run for expired probability input")

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
    )

    assert payload["state"] == "OFFLOAD_BLOCKED"
    assert payload["rows"][0]["probability_kind"] == "NOWCAST"
    assert "probability_input_expired" in payload["offload"]["reason_codes"]
    assert payload["offload"]["mc_eligible_input_count"] == 0
```

- [ ] **Step 4: Run the focused tests and confirm they fail**

Run:

```bash
uv run pytest -q tests/probability/test_gpu_worker.py::test_worker_runs_mc_for_fresh_input_when_sibling_source_is_stale tests/probability/test_gpu_worker.py::test_worker_blocks_expired_probability_input_even_when_snapshot_is_fresh
```

Expected: both fail before implementation. The first failure should show a global `OFFLOAD_BLOCKED` or missing `mc_eligible_input_count`. The second failure may show `probability_inputs_stale` without the new expired reason.

---

## Task 2: Implement Per-Input MC Eligibility And Offload Diagnostics

**Files:**
- Modify: `src/polymarket_engine/probability/gpu_worker.py`

- [ ] **Step 1: Add constants and a per-input result dataclass near the worker defaults**

```python
DEFAULT_MAX_INPUT_STATE_LAG_MS = 10_000
DEFAULT_MIN_SECONDS_LEFT_FOR_MC = 20.0


@dataclass(frozen=True)
class _InputMcEligibility:
    runtime_input: ProbabilityRuntimeInput
    allowed: bool
    reason_codes: tuple[str, ...]
    input_state_lag_ms: int
```

- [ ] **Step 2: Add data eligibility helpers after `_runtime_input_age_ms`**

```python
def _input_mc_eligibility(
    runtime_input: ProbabilityRuntimeInput,
    *,
    generated_at: datetime,
    gate_config: OffloadGateConfig,
) -> _InputMcEligibility:
    probability_input = runtime_input.probability_input
    input_state_lag_ms = _runtime_input_age_ms(runtime_input, generated_at)
    reasons: list[str] = list(_runtime_input_mc_block_reasons(runtime_input))
    if runtime_input.expiry_ts <= generated_at or probability_input.seconds_left <= 0:
        reasons.append("probability_input_expired")
    elif probability_input.seconds_left < DEFAULT_MIN_SECONDS_LEFT_FOR_MC:
        reasons.append("near_expiry")
    if input_state_lag_ms > DEFAULT_MAX_INPUT_STATE_LAG_MS:
        reasons.append("probability_inputs_stale")
    if probability_input.source_age_ms > gate_config.max_price_age_ms:
        reasons.append("price_stale")
    if probability_input.book_age_ms > gate_config.max_orderbook_age_ms:
        reasons.append("orderbook_stale")
    if runtime_input.sigma_age_ms > gate_config.max_sigma_tau_age_ms:
        reasons.append("sigma_stale")
    return _InputMcEligibility(
        runtime_input=runtime_input,
        allowed=not reasons,
        reason_codes=_dedupe_reasons(tuple(reasons)),
        input_state_lag_ms=input_state_lag_ms,
    )


def _input_mc_eligibilities(
    inputs: Sequence[ProbabilityRuntimeInput],
    *,
    generated_at: datetime,
    gate_config: OffloadGateConfig,
) -> tuple[_InputMcEligibility, ...]:
    return tuple(
        _input_mc_eligibility(
            runtime_input,
            generated_at=generated_at,
            gate_config=gate_config,
        )
        for runtime_input in inputs
    )


def _eligible_mc_inputs(
    eligibilities: Sequence[_InputMcEligibility],
) -> tuple[ProbabilityRuntimeInput, ...]:
    blocked_contract_ids = {
        eligibility.runtime_input.contract_id
        for eligibility in eligibilities
        if not eligibility.allowed
    }
    return tuple(
        eligibility.runtime_input
        for eligibility in eligibilities
        if eligibility.allowed
        and eligibility.runtime_input.contract_id not in blocked_contract_ids
    )
```

- [ ] **Step 3: Add offload diagnostic payload helpers**

```python
def _offload_input_diagnostics(
    eligibilities: Sequence[_InputMcEligibility],
) -> dict[str, Any]:
    blocked = [eligibility for eligibility in eligibilities if not eligibility.allowed]
    return {
        "input_count": len(eligibilities),
        "mc_eligible_input_count": len(eligibilities) - len(blocked),
        "blocked_input_count": len(blocked),
        "max_input_state_lag_ms": max(
            (eligibility.input_state_lag_ms for eligibility in eligibilities),
            default=None,
        ),
        "max_source_age_ms": max(
            (
                eligibility.runtime_input.probability_input.source_age_ms
                for eligibility in eligibilities
            ),
            default=None,
        ),
        "max_book_age_ms": max(
            (
                eligibility.runtime_input.probability_input.book_age_ms
                for eligibility in eligibilities
            ),
            default=None,
        ),
        "min_seconds_left": min(
            (
                eligibility.runtime_input.probability_input.seconds_left
                for eligibility in eligibilities
            ),
            default=None,
        ),
        "blocked_inputs": [
            {
                "state_id": eligibility.runtime_input.probability_input.state_id,
                "contract_id": eligibility.runtime_input.contract_id,
                "market_slug": eligibility.runtime_input.market_slug,
                "asset": eligibility.runtime_input.probability_input.asset,
                "side": eligibility.runtime_input.probability_input.side,
                "reason_codes": list(eligibility.reason_codes),
                "input_state_lag_ms": eligibility.input_state_lag_ms,
                "source_age_ms": eligibility.runtime_input.probability_input.source_age_ms,
                "book_age_ms": eligibility.runtime_input.probability_input.book_age_ms,
                "seconds_left": eligibility.runtime_input.probability_input.seconds_left,
            }
            for eligibility in blocked[:12]
        ],
    }


def _with_offload_input_diagnostics(
    payload: dict[str, Any],
    eligibilities: Sequence[_InputMcEligibility],
) -> dict[str, Any]:
    diagnostics = _offload_input_diagnostics(eligibilities)
    merged = dict(payload)
    merged.update(diagnostics)
    merged["input_diagnostics"] = diagnostics
    return merged
```

- [ ] **Step 4: Change the global offload decision to ignore per-input data ages**

In `_offload_decision_from_inputs`, keep recovery/runtime checks global, but do not let max source age, book age, or input state lag from one row block every row. Replace these three `OffloadGateInputs` fields:

```python
price_age_ms=0,
orderbook_age_ms=0,
probability_input_age_ms=0,
```

Keep `volatility_age_ms`, `sigma_tau_valid`, `sigma_tau_age_ms`, and `k_stable` in the global gate only if the implementation deliberately wants one invalid sigma/K to block all MC. For this pass, prefer per-input handling and pass safe global values:

```python
volatility_age_ms=0,
sigma_tau_valid=True,
sigma_tau_age_ms=0,
k_stable=True,
```

The per-input helper added above remains responsible for `sigma_invalid`, `sigma_stale`, and `k_unstable`.

- [ ] **Step 5: Update worker control flow after reading inputs**

Replace the current `mc_inputs = _mc_eligible_inputs(inputs)` block with:

```python
gate_config = OffloadGateConfig()
eligibilities = _input_mc_eligibilities(
    inputs,
    generated_at=generated_at,
    gate_config=gate_config,
)
offload_decision = _with_offload_input_diagnostics(offload_decision, eligibilities)
_write_offload_status(offload_status_path, offload_decision)

global_offload_allowed = bool(offload_decision["offload_allowed"])
mc_inputs = _eligible_mc_inputs(eligibilities) if global_offload_allowed else ()
if len(mc_inputs) > budget.max_total_paths:
    mc_input_skipped = len(mc_inputs) - budget.max_total_paths
    mc_inputs = mc_inputs[: budget.max_total_paths]
path_budget_per_input = _path_budget_per_input(input_count=len(mc_inputs), budget=budget)
```

Move `_write_offload_status` to this point so the persisted status includes diagnostics. Do not write the old offload payload before eligibility is known.

- [ ] **Step 6: Stamp NOWCAST rows with per-input block reasons**

Before appending each nowcast row:

```python
eligibility_by_state_id = {
    eligibility.runtime_input.probability_input.state_id: eligibility
    for eligibility in eligibilities
}
```

Then, inside the nowcast row loop:

```python
eligibility = eligibility_by_state_id.get(runtime_input.probability_input.state_id)
if eligibility is not None and not eligibility.allowed:
    nowcast_row["offload_allowed"] = False
    nowcast_row["block_reasons"] = list(eligibility.reason_codes)
```

- [ ] **Step 7: Only return early when no MC can run**

Replace the current `if not offload_decision["offload_allowed"]:` early return with:

```python
if not global_offload_allowed or not mc_inputs:
    blocked_rows, _ = _merge_missing_retained_mc_rows(
        fresh_rows=retained_mc_rows,
        previous_rows=previous_rows,
        now=generated_at,
        enabled=True,
    )
    reason_codes = tuple(offload_decision.get("reason_codes") or ())
    if not reason_codes:
        reason_codes = _dedupe_reasons(
            tuple(
                reason
                for eligibility in eligibilities
                for reason in eligibility.reason_codes
            )
        )
        offload_decision["reason_codes"] = list(reason_codes)
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
        budget=_budget_diagnostics(
            budget=budget,
            cycle_started_monotonic=cycle_started_monotonic,
            cycle_started_process=cycle_started_process,
            requested_total_paths=requested_total_paths,
            allocated_total_paths=allocated_total_paths,
            clamped_inputs=clamped_inputs,
            mc_input_skipped=mc_input_skipped,
            path_budget_per_input=path_budget_per_input,
        ),
    )
    payload["offload"] = offload_decision
    _write_status(probability_status_path, payload)
    return payload
```

- [ ] **Step 8: Preserve diagnostics in the successful payload**

Before writing the final payload, add:

```python
payload["offload"] = offload_decision
```

This gives `/api/runtime/probabilities` the same offload truth as `/api/runtime/live`.

- [ ] **Step 9: Run worker tests**

Run:

```bash
uv run pytest -q tests/probability/test_gpu_worker.py
```

Expected: all pass.

---

## Task 3: Preserve Offload Diagnostics In Runtime API

**Files:**
- Modify: `src/polymarket_engine/runtime_api.py`
- Modify: `tests/test_runtime_api.py`

- [ ] **Step 1: Add an API regression**

Add a test that writes `offload_status.json` with the new fields and checks `/api/runtime/live` preserves them:

```python
def test_runtime_live_preserves_offload_input_diagnostics(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    normalized_health_path = tmp_path / "normalized_health.json"
    probability_status_path = tmp_path / "probabilities.json"
    offload_status_path = tmp_path / "offload_status.json"
    recovery_status_path = tmp_path / "recovery_status.json"
    target_cache_path = tmp_path / "targets.json"
    volatility_status_path = tmp_path / "volatility.json"
    now = datetime.now(timezone.utc).isoformat()
    status_path.write_text(
        json.dumps({"schema_version": "x", "ok": True, "generated_at": now, "counts": {}}),
        encoding="utf-8",
    )
    normalized_health_path.write_text(
        json.dumps({"schema_version": NORMALIZED_HEALTH_SCHEMA_VERSION, "generated_at": now, "tables": []}),
        encoding="utf-8",
    )
    probability_status_path.write_text(
        json.dumps({"schema_version": "polymarket-probability-runtime-v1", "generated_at": now, "rows": []}),
        encoding="utf-8",
    )
    recovery_status_path.write_text(
        json.dumps({"runtime_phase": "READY", "ready": True, "reasons": [], "generated_at": now}),
        encoding="utf-8",
    )
    offload_status_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-offload-runtime-v1",
                "generated_at": now,
                "offload_allowed": True,
                "reason_codes": [],
                "recommended_worker_mode": "gpu_mc",
                "recommended_max_total_paths": 80000,
                "mc_eligible_input_count": 1,
                "blocked_input_count": 1,
                "max_input_state_lag_ms": 4200,
                "blocked_inputs": [{"state_id": "state-btc", "reason_codes": ["price_stale"]}],
            }
        ),
        encoding="utf-8",
    )

    payload = _runtime_live_payload(
        status_path=status_path,
        duckdb_path=tmp_path / "missing.duckdb",
        normalized_health_path=normalized_health_path,
        probability_status_path=probability_status_path,
        probability_inputs_path=None,
        target_cache_path=target_cache_path,
        volatility_status_path=volatility_status_path,
        recovery_status_path=recovery_status_path,
        offload_status_path=offload_status_path,
        limit=8,
    )

    assert payload["offload"]["mc_eligible_input_count"] == 1
    assert payload["offload"]["blocked_input_count"] == 1
    assert payload["offload"]["max_input_state_lag_ms"] == 4200
    assert payload["offload"]["blocked_inputs"][0]["reason_codes"] == ["price_stale"]
```

- [ ] **Step 2: Extend `_compact_offload_status`**

Return these fields when present:

```python
"recommended_max_total_paths": payload.get("recommended_max_total_paths"),
"input_count": payload.get("input_count"),
"mc_eligible_input_count": payload.get("mc_eligible_input_count"),
"blocked_input_count": payload.get("blocked_input_count"),
"max_input_state_lag_ms": payload.get("max_input_state_lag_ms"),
"max_source_age_ms": payload.get("max_source_age_ms"),
"max_book_age_ms": payload.get("max_book_age_ms"),
"min_seconds_left": payload.get("min_seconds_left"),
"blocked_inputs": _dict_list(payload.get("blocked_inputs")),
"input_diagnostics": payload.get("input_diagnostics") if isinstance(payload.get("input_diagnostics"), dict) else None,
```

Add this helper near `_string_list`:

```python
def _dict_list(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]
```

- [ ] **Step 3: Run the API regression**

Run:

```bash
uv run pytest -q tests/test_runtime_api.py::test_runtime_live_preserves_offload_input_diagnostics
```

Expected: pass.

---

## Task 4: Add Browser Live Path Status Helpers

**Files:**
- Modify: `ui/src/probabilityRows.ts`
- Add: `tests/ui/live_path_status_test.ts`

- [ ] **Step 1: Add exported helper types and summary function**

Append to `ui/src/probabilityRows.ts`:

```ts
export type LivePathStatusInput = {
  probabilityState?: string;
  rows?: ProbabilityValueRow[];
  offload?: {
    offload_allowed?: boolean;
    reason_codes?: unknown[];
    recommended_worker_mode?: string;
    mc_eligible_input_count?: number;
    blocked_input_count?: number;
    max_input_state_lag_ms?: number;
  };
};

export function livePathStatus(input: LivePathStatusInput) {
  const rows = Array.isArray(input.rows) ? input.rows : [];
  const mcRows = rows.filter((row) => row.probability_kind !== "NOWCAST");
  const previewRows = mcRows.filter((row) => parsePreview(row.simulation_preview));
  const nowcastRows = rows.filter((row) => row.probability_kind === "NOWCAST");
  const reasons = Array.isArray(input.offload?.reason_codes)
    ? input.offload.reason_codes.map(String).filter(Boolean)
    : [];
  if (previewRows.length > 0) {
    return {
      state: nowcastRows.length > 0 ? "PARTIAL_PATHS" : "LIVE_PATHS",
      label: nowcastRows.length > 0 ? "Partial paths" : "Live paths",
      detail: `${previewRows.length} preview row${previewRows.length === 1 ? "" : "s"}`,
      reasons,
    };
  }
  if (input.offload?.offload_allowed === false || input.probabilityState === "OFFLOAD_BLOCKED") {
    return {
      state: "PATHS_BLOCKED",
      label: "Live paths blocked",
      detail: reasons.length > 0 ? reasons.join(", ") : "offload blocked",
      reasons,
    };
  }
  if (nowcastRows.length > 0) {
    return {
      state: "NOWCAST_ONLY",
      label: "Nowcast only",
      detail: "MC preview pending",
      reasons,
    };
  }
  return {
    state: "PATHS_PENDING",
    label: "Paths pending",
    detail: "waiting for probability rows",
    reasons,
  };
}
```

- [ ] **Step 2: Add a TypeScript helper test**

Create `tests/ui/live_path_status_test.ts`:

```ts
import assert from "node:assert/strict";
import { livePathStatus } from "../../ui/src/probabilityRows";

assert.equal(
  livePathStatus({
    rows: [
      {
        contract_id: "eth-up",
        probability_kind: "MC",
        simulation_preview: { sampled_paths: [{ index: 0, terminal_win: true, no_touch_win: true, points: [1, 2] }] },
      },
    ],
    offload: { offload_allowed: true, mc_eligible_input_count: 1, blocked_input_count: 0 },
  }).state,
  "LIVE_PATHS",
);

assert.deepEqual(
  livePathStatus({
    probabilityState: "OFFLOAD_BLOCKED",
    rows: [{ contract_id: "btc-up", probability_kind: "NOWCAST" }],
    offload: { offload_allowed: false, reason_codes: ["probability_inputs_stale"] },
  }),
  {
    state: "PATHS_BLOCKED",
    label: "Live paths blocked",
    detail: "probability_inputs_stale",
    reasons: ["probability_inputs_stale"],
  },
);

assert.equal(
  livePathStatus({
    rows: [{ contract_id: "btc-up", probability_kind: "NOWCAST" }],
    offload: { offload_allowed: true, mc_eligible_input_count: 0, blocked_input_count: 1 },
  }).state,
  "NOWCAST_ONLY",
);
```

- [ ] **Step 3: Run the helper test**

Run:

```bash
npx tsx tests/ui/live_path_status_test.ts
```

Expected: pass after the helper is implemented.

---

## Task 5: Render Live Path Status In Browser UI

**Files:**
- Modify: `ui/src/App.tsx`
- Modify: `ui/src/styles.css`

- [ ] **Step 1: Extend `RuntimeOffload` type**

In `ui/src/App.tsx`, add fields:

```ts
  recommended_max_total_paths?: number;
  input_count?: number;
  mc_eligible_input_count?: number;
  blocked_input_count?: number;
  max_input_state_lag_ms?: number;
  max_source_age_ms?: number;
  max_book_age_ms?: number;
  min_seconds_left?: number;
  blocked_inputs?: JsonRecord[];
```

- [ ] **Step 2: Import and use `livePathStatus`**

Update the import from `./probabilityRows` to include:

```ts
  livePathStatus,
```

Inside `StatusStrip`, compute:

```ts
  const pathStatus = livePathStatus({
    probabilityState: probabilityPayload?.state,
    rows,
    offload: livePayload?.offload,
  });
```

Add this metric after `Paths`:

```tsx
      <Metric label="Live paths" value={pathStatus.label} tone={pathStatusTone(pathStatus.state)} />
```

Add this status note after the offload note:

```tsx
      {pathStatus.state !== "LIVE_PATHS" ? (
        <div className="status-note status-note-info">
          Paths: {pathStatus.detail}
        </div>
      ) : null}
```

Add this formatter:

```ts
function pathStatusTone(state: string): Tone {
  if (state === "LIVE_PATHS") {
    return "good";
  }
  if (state === "PARTIAL_PATHS" || state === "NOWCAST_ONLY") {
    return "warn";
  }
  if (state === "PATHS_BLOCKED") {
    return "bad";
  }
  return "neutral";
}
```

- [ ] **Step 3: Add a compact offload diagnostics panel**

In `SelectedDetails`, after the `hero-metrics` block, render:

```tsx
      <LivePathDiagnostics offload={probabilities?.offload} row={row} />
```

If `ProbabilityPayload` does not include `offload`, add it to the type near the probability payload definition:

```ts
  offload?: RuntimeOffload;
```

Add the component near `SelectedDetails`:

```tsx
function LivePathDiagnostics({
  offload,
  row,
}: {
  offload?: RuntimeOffload;
  row: ProbabilityRow;
}) {
  const blockedInputs = Array.isArray(offload?.blocked_inputs) ? offload.blocked_inputs : [];
  const rowReasons = statusReasons(row.block_reasons);
  if (!offload && !rowReasons) {
    return null;
  }
  return (
    <section className="live-path-diagnostics" aria-label="Live path diagnostics">
      <Metric label="MC eligible" value={formatInteger(offload?.mc_eligible_input_count)} />
      <Metric label="Blocked inputs" value={formatInteger(offload?.blocked_input_count)} />
      <Metric label="Input lag" value={formatAgeMs(offload?.max_input_state_lag_ms)} />
      <Metric label="Source age" value={formatAgeMs(offload?.max_source_age_ms)} />
      {rowReasons ? <span className="blocked-chip">Row: {rowReasons}</span> : null}
      {blockedInputs.slice(0, 4).map((item, index) => (
        <span className="blocked-chip" key={`${String(item.state_id ?? index)}-${index}`}>
          {String(item.asset ?? "?")} {String(item.side ?? "?")}: {statusReasons(item.reason_codes)}
        </span>
      ))}
    </section>
  );
}
```

Add the age formatter near other formatters:

```ts
function formatAgeMs(value: unknown) {
  const numeric = toFiniteNumber(value);
  if (numeric === undefined) {
    return "-";
  }
  if (numeric >= 1000) {
    return `${(numeric / 1000).toFixed(1)}s`;
  }
  return `${Math.round(numeric)}ms`;
}
```

- [ ] **Step 4: Make missing path previews explicit**

In `MonteCarloCanvas`, replace the fallback branch with:

```tsx
  if (!preview || !geometry) {
    return (
      <div className="path-chart path-chart-fallback">
        <ProbabilityFallbackChart row={row} />
        <div className="path-blocked-caption">
          {row.probability_kind === "NOWCAST"
            ? `No sampled paths for NOWCAST row${statusReasons(row.block_reasons) ? `: ${statusReasons(row.block_reasons)}` : ""}`
            : "No sampled paths in latest MC payload"}
        </div>
      </div>
    );
  }
```

- [ ] **Step 5: Add CSS**

Append these styles to `ui/src/styles.css` near the status/detail styles:

```css
.live-path-diagnostics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 4px;
  margin: 6px 0;
}

.blocked-chip {
  min-width: 0;
  border: 1px solid #4a3c25;
  border-radius: 4px;
  padding: 4px 6px;
  color: #dfca90;
  background: #17130d;
  font-size: 10px;
  font-weight: 750;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.path-chart-fallback {
  position: relative;
}

.path-blocked-caption {
  border-top: 1px solid #2a2f34;
  padding: 5px 8px;
  color: #dfca90;
  font-size: 11px;
}

@media (max-width: 720px) {
  .live-path-diagnostics {
    grid-template-columns: 1fr 1fr;
  }
}
```

- [ ] **Step 6: Build the UI**

Run:

```bash
npm --prefix ui run build
```

Expected: Vite build succeeds.

---

## Task 6: Document And Verify CPU/GPU Offload Behavior

**Files:**
- Modify: `docs/SPOON_DEPLOYMENT.md`

- [ ] **Step 1: Add runtime commands to docs**

Add a section named `Live Path And GPU Worker Checks` with:

```markdown
### Live Path And GPU Worker Checks

On THEPC WSL:

```bash
cd ~/polymarket
docker compose -f deploy/collector/docker-compose.yml ps
curl -sS 'http://127.0.0.1:8000/api/runtime/offload' | jq .
curl -sS 'http://127.0.0.1:8000/api/runtime/probabilities?limit=8' | jq '.state,.lanes,.budget,.offload'
docker logs --tail=80 gpu-probability-worker
nvidia-smi
```

Expected healthy MC:

```text
offload.offload_allowed is true
offload.mc_eligible_input_count is greater than 0
probabilities.lanes includes MC
probabilities.budget.allocated_total_paths is greater than 0
at least one row has simulation_preview.sampled_paths
```

Expected blocked or partial state:

```text
NOWCAST rows remain visible
blocked rows list block_reasons
offload.blocked_inputs names stale assets and reasons
GPU utilization can be low while offload is blocked
```
```

- [ ] **Step 2: Run docs test**

Run:

```bash
uv run pytest -q tests/docs/test_active_runtime_docs.py
```

Expected: pass after updating any doc assertions that check the deployment section text.

---

## Task 7: Merge The Hybrid MC Calibration Roadmap

**Files:**
- Modify: `docs/observations.md`
- Delete: `docs/observations pt 2`
- Modify: `docs/superpowers/plans/2026-06-11-observations-runtime-recovery.md`

- [ ] **Step 1: Merge `docs/observations pt 2` into `docs/observations.md`**

Append a new top-level section near the ML roadmap area:

```markdown
# Hybrid Monte Carlo, ML Calibration, And BART Research Plan

Monte Carlo remains the base probability engine. ML is a calibration and meta-model layer. BART is an offline uncertainty-aware research benchmark.

The target architecture is:

```text
as-of market state
  -> Monte Carlo path engine
  -> p_finish_MC, p_no_touch_MC, z_path, sigma_tau, generator dispersion
  -> ML calibration / meta-model
  -> p_finish_final plus uncertainty
  -> executable edge calculation
  -> paper decision only after validation
```

The first supervised target is final contract win/loss. The dataset must include only values timestamped at or before `asof_ts`. Future settlement is a label only.

Calibration must be measured by bucket: TTE, z_path, threshold distance, volatility regime, side, asset, spread/depth, order-book imbalance, final-window state, threshold congestion, and source-quality state.

Model order:

1. Runtime stability and clean data logging.
2. Raw MC calibration reports.
3. Logistic regression calibrator.
4. Gradient-boosted calibrator.
5. BART offline uncertainty benchmark.
6. Uncertainty-aware edge rule.
7. Paper-trade-only validation.

BART outputs should be kept offline at first: `p_mean`, `p_median`, posterior quantiles, posterior width, and uncertainty score. The live decision layer must not consume BART output until the replay, calibration, and latency contracts are proven.
```

- [ ] **Step 2: Remove the temporary source file**

Run:

```bash
rm "docs/observations pt 2"
```

- [ ] **Step 3: Remove stale deferred-calibrator tombstone from the old plan**

Edit `docs/superpowers/plans/2026-06-11-observations-runtime-recovery.md`:

```text
Delete the deferred first-calibrator tombstone section.
Rename the final verification section to "## Final Verification And Deploy Check".
Remove execution-order bullets and self-review references that point to the deleted calibrator task.
Keep the calibration dataset and calibration report tasks intact.
```

- [ ] **Step 4: Verify the cleanup**

Run:

```bash
rg -n "First ML Calibrator|Calibrator_LogReg" docs/superpowers/plans/2026-06-11-observations-runtime-recovery.md docs/observations.md
test ! -e "docs/observations pt 2"
```

Expected: `rg` returns no matches and the file-existence test passes.

---

## Task 8: Focused Verification

**Files:**
- No file edits in this task.

- [ ] **Step 1: Run focused Python tests**

Run:

```bash
uv run pytest -q tests/probability/test_gpu_worker.py tests/test_runtime_api.py tests/docs/test_active_runtime_docs.py
```

Expected: all pass.

- [ ] **Step 2: Run UI checks**

Run:

```bash
npx tsx tests/ui/live_path_status_test.ts
npm --prefix ui run build
```

Expected: both pass.

- [ ] **Step 3: Run a single broader smoke pass**

Run:

```bash
uv run ruff check .
uv run mypy src tests
```

Expected: pass, or document exact pre-existing failures with file and error names before proceeding.

---

## Task 9: Deploy And Verify On THEPC

**Files:**
- Modify only if verification reveals a deployment script mismatch.

- [ ] **Step 1: Build/deploy to THEPC using existing deploy path**

Run from the Mac repo:

```bash
./scripts/deploy_pc.sh
```

Expected: deployment completes and THEPC containers restart with the updated API, worker, and UI assets.

- [ ] **Step 2: Check THEPC runtime**

Run:

```bash
ssh ender@100.72.104.49 'cd ~/polymarket && docker compose -f deploy/collector/docker-compose.yml ps && curl -sS "http://127.0.0.1:8000/api/runtime/offload" | jq "{offload_allowed, reason_codes, mc_eligible_input_count, blocked_input_count, max_input_state_lag_ms, blocked_inputs}" && curl -sS "http://127.0.0.1:8000/api/runtime/probabilities?limit=8" | jq "{state, lanes, rows_written, budget, offload, first_row: .rows[0]}" && nvidia-smi'
```

Expected:

```text
gpu-probability-worker is running
api is healthy
offload diagnostics include mc_eligible_input_count and blocked_input_count
if any input is eligible, probabilities.lanes includes MC and budget.allocated_total_paths > 0
if no input is eligible, blocked_inputs explains why and NOWCAST rows remain visible
```

- [ ] **Step 3: Check browser UI locally through the current THEPC access route**

Use the working local tunnel or Windows localhost route and open:

```text
http://127.0.0.1:8000/
```

Expected:

```text
Status strip shows Live paths.
If MC is running, selected row shows sampled paths.
If MC is blocked or partial, UI shows blocker reasons and does not pretend paths are live.
```

---

## Task 10: GitHub Update

**Files:**
- Stage only files changed by this plan and the already-intended launcher/status work in this branch.

- [ ] **Step 1: Review dirty state**

Run:

```bash
git status --short
git diff --stat
```

Expected: dirty files are limited to runtime/UI/docs/scripts/tests in this branch.

- [ ] **Step 2: Commit restored launchers/status UI work if still uncommitted**

Run:

```bash
git add docs/SPOON_DEPLOYMENT.md scripts/deploy_pc.sh scripts/install_thepc_spoon_artifact_sync.sh scripts/open_duckdb_ui_mac.sh scripts/open_tui_mac.sh scripts/install_spoon_duckdb_ui.sh tests/docs/test_active_runtime_docs.py tests/scripts/test_deploy_script.py tests/scripts/test_duckdb_ui_launcher.py tests/scripts/test_mac_tui_launcher.py ui/src/App.tsx
git commit -m "ops: restore THEPC launchers and live status UI"
```

Expected: commit succeeds, or Git says there is nothing to commit because these changes were already committed.

- [ ] **Step 3: Commit live path worker/UI/docs work**

Run:

```bash
git add src/polymarket_engine/probability/gpu_worker.py src/polymarket_engine/runtime_api.py tests/probability/test_gpu_worker.py tests/test_runtime_api.py ui/src/App.tsx ui/src/probabilityRows.ts ui/src/styles.css tests/ui/live_path_status_test.ts docs/observations.md docs/SPOON_DEPLOYMENT.md docs/superpowers/plans/2026-06-11-live-paths-gpu-worker-hybrid-mc.md docs/superpowers/plans/2026-06-11-observations-runtime-recovery.md
git add -u -- "docs/observations pt 2"
git commit -m "probability: restore live MC paths with offload diagnostics"
```

Expected: commit succeeds.

- [ ] **Step 4: Push branch to GitHub**

Run:

```bash
git push -u origin codex/observations-runtime-recovery
```

Expected: push succeeds. If the remote branch already has an upstream, `git push` is enough.

- [ ] **Step 5: Open or update PR**

Run:

```bash
gh pr status
gh pr create --fill --base main --head codex/observations-runtime-recovery
```

Expected: if a PR already exists, `gh pr status` shows it and no new PR is created. If no PR exists, a PR is created from `codex/observations-runtime-recovery` to `main`.

---

## Risk Controls

- Do not loosen safety globally just to make paths appear.
- Do not allow expired or near-expiry stale inputs to run MC.
- Do not let one stale asset suppress MC for unrelated fresh rows.
- Do not replace Monte Carlo with ML.
- Do not use future settlement or outcome information as model features.
- Do not add live trading, signing, order placement, or automatic buy/sell behavior.
- Do not hide NOWCAST rows; they are useful for continuity when MC is blocked.
- Do not claim GPU success from container uptime alone. The proof is MC rows, allocated paths, sampled previews, and offload diagnostics.

## Self-Review

- Spec coverage: Browser UI live path visibility is covered by Tasks 4 and 5. GPU worker behavior on THEPC is covered by Tasks 1, 2, 6, and 9. CPU/offload visibility is covered by Tasks 2, 3, 5, and 6. The new Monte Carlo calibration idea is covered by Task 7. GitHub update is covered by Task 10.
- Placeholder scan: The plan avoids placeholder implementation steps and includes concrete commands, file paths, and code snippets for each code task.
- Type consistency: Worker diagnostics use `RuntimeOffload` in the UI, JSON dicts in the API, and `ProbabilityRuntimeInput` in the worker.
- Scope check: This is one deployable lane because the browser path status depends on the worker producing truthful offload diagnostics and MC rows.
