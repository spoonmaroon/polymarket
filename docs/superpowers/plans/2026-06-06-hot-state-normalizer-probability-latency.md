# Hot-State Normalizer Probability Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce live CPU, memory, and probability latency by separating raw-row normalization from hot live-state rebuilds, moving probability inputs off DuckDB polling, batching remaining DuckDB work, and moving outcome refresh out of the hot normalizer loop.

**Architecture:** The normalizer remains responsible for ingesting the same raw JSONL rows into DuckDB, but it stops treating every loop as a full live-state reporting pass. A compact hot-state snapshot generated from the Rust collector status becomes the live probability input source, while DuckDB remains the durable normalized/replay store. Outcome refresh moves to its own slower sidecar so settlement-history writes cannot block sub-second live state.

**Tech Stack:** Python 3.14, DuckDB, Polars, FastAPI runtime API, Rust state-manager JSON status, Docker Compose on THEPC WSL.

---

## File Structure

- Modify `src/polymarket_engine/features/rust_decision_snapshots.py`: add incremental hot-state build helpers that accept an already-read status payload, return unchanged signatures, and write only states whose contract inputs changed.
- Modify `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`: split raw normalization from hot-state/status/reporting work, add phase counters, and disable outcome refresh in the hot loop by default.
- Create `src/polymarket_engine/probability/hot_inputs.py`: serialize/parse compact live probability input snapshots without querying DuckDB.
- Modify `src/polymarket_engine/probability/runtime.py`: prefer hot input snapshots for live probability rows; keep DuckDB fallback for replay/API compatibility.
- Modify `src/polymarket_engine/cli.py`: add CLI flags for hot probability input paths and add a new `run-outcome-refresh-sidecar` command.
- Create `src/polymarket_engine/validation/outcome_sidecar.py`: run slow official-outcome refresh on a separate cadence.
- Modify `src/polymarket_engine/storage/duckdb_store.py`: apply per-connection DuckDB runtime settings and batch probability output inserts.
- Modify `deploy/normalizer/normalizer-entrypoint.sh`: pass hot probability input path and disable hot-loop outcome refresh.
- Modify `deploy/collector/docker-compose.yml`: add outcome sidecar service, add env vars for hot input paths and DuckDB settings.
- Add tests in `tests/features/test_rust_decision_snapshots.py`, `tests/ingestion/test_rust_normalizer_sidecar.py`, `tests/probability/test_hot_inputs.py`, `tests/probability/test_runtime.py`, `tests/validation/test_outcome_sidecar.py`, `tests/storage/test_duckdb_store_settings.py`, and `tests/test_cli.py`.

## Risk Areas

- As-of safety: all live probability inputs must use status/source/observed timestamps from the collector and must not read future outcome data.
- State freshness: skipping rebuilds must not hide token rollovers, threshold changes, side changes, or expired/current-window transitions.
- Local vs deployed drift: THEPC currently has probability-event drain behavior not present in this local tree. Before deploy, diff remote `/home/ender/polymarket` and reconcile that code into this branch or avoid overwriting it.
- DuckDB settings: `SET threads = 1` lowers CPU but can increase cycle time. Treat it as configurable, not hard-coded policy.
- Outcome sidecar: moving outcome refresh out of the hot loop must preserve `outcomes.json` and `validation.market_outcome_history` behavior for runtime API consumers.

## Suggested Subagents

- Subagent A: hot-state incremental rebuild and normalizer loop changes.
- Subagent B: hot probability input snapshot format and runtime worker/API changes.
- Subagent C: outcome refresh sidecar and Docker Compose wiring.
- Subagent D: DuckDB settings/batching and performance verification.

---

### Task 1: Add Hot State Signatures And Incremental Build Contract

**Files:**
- Modify: `src/polymarket_engine/features/rust_decision_snapshots.py`
- Test: `tests/features/test_rust_decision_snapshots.py`

- [ ] **Step 1: Write failing tests for stable hot-state signatures**

Add tests that prove `generated_at` alone does not change the semantic signature, but token/window/orderbook/price inputs do:

```python
def test_status_state_signature_ignores_generated_at_only(tmp_path: Path) -> None:
    first = _status_payload(asof_ts=datetime(2026, 6, 6, 12, 0, tzinfo=UTC))
    second = dict(first)
    second["generated_at"] = datetime(2026, 6, 6, 12, 0, 1, tzinfo=UTC).isoformat()

    assert hot_state_signature(first) == hot_state_signature(second)


def test_status_state_signature_changes_for_live_inputs(tmp_path: Path) -> None:
    first = _status_payload(asof_ts=datetime(2026, 6, 6, 12, 0, tzinfo=UTC))
    second = json.loads(json.dumps(first))
    second["current"][0]["up"]["token_id"] = "different-up-token"

    assert hot_state_signature(first) != hot_state_signature(second)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/features/test_rust_decision_snapshots.py::test_status_state_signature_ignores_generated_at_only tests/features/test_rust_decision_snapshots.py::test_status_state_signature_changes_for_live_inputs -q
```

Expected: fail because `hot_state_signature` does not exist.

- [ ] **Step 3: Implement semantic signature helper**

Add to `src/polymarket_engine/features/rust_decision_snapshots.py`:

```python
def hot_state_signature(payload: dict[str, Any]) -> str:
    semantic = {
        "current": payload.get("current", []),
        "next": payload.get("next", []),
        "orderbooks": payload.get("orderbooks", []),
        "prices": payload.get("prices", []),
        "websocket_status": payload.get("websocket_status", {}),
    }
    encoded = json.dumps(semantic, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
uv run pytest tests/features/test_rust_decision_snapshots.py::test_status_state_signature_ignores_generated_at_only tests/features/test_rust_decision_snapshots.py::test_status_state_signature_changes_for_live_inputs -q
```

Expected: pass.

---

### Task 2: Split Normalization From State/Status Reporting In The Loop

**Files:**
- Modify: `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
- Test: `tests/ingestion/test_rust_normalizer_sidecar.py`

- [ ] **Step 1: Write failing loop tests**

Add tests that assert changed raw rows still normalize, but state rebuild only runs when the status semantic signature changes:

```python
def test_sidecar_normalizes_raw_append_without_rebuilding_state_when_hot_status_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root, db_path, status_path, health_path, changed_path = _hot_loop_fixture(tmp_path)
    build_calls = 0

    def counting_build(*args: Any, **kwargs: Any) -> Any:
        nonlocal build_calls
        build_calls += 1
        return SimpleNamespace(contracts_upserted=0, states_written=0, unavailable=())

    def append_raw(_: float) -> None:
        _append_orderbook_row(changed_path, token_id="up-token", bid=0.63, ask=0.65)

    monkeypatch.setattr(rust_normalizer_sidecar, "build_current_decision_state_snapshots", counting_build)
    monkeypatch.setattr("polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep", append_raw)

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert build_calls == 1
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
uv run pytest tests/ingestion/test_rust_normalizer_sidecar.py::test_sidecar_normalizes_raw_append_without_rebuilding_state_when_hot_status_unchanged -q
```

Expected: fail if current loop rebuild behavior still follows raw append or mtime too broadly.

- [ ] **Step 3: Implement loop decision object**

Add a small internal dataclass:

```python
@dataclass(frozen=True)
class StateBuildDecision:
    should_build: bool
    reason: str
    signature: str | None
```

Add helper:

```python
def _state_build_decision(
    *,
    previous_signature: str | None,
    status_payload: dict[str, Any] | None,
    force_state_build: bool,
    reprocess_all: bool,
) -> StateBuildDecision:
    if status_payload is None:
        return StateBuildDecision(False, "missing_status", None)
    signature = hot_state_signature(status_payload)
    if force_state_build:
        return StateBuildDecision(True, "forced", signature)
    if reprocess_all:
        return StateBuildDecision(True, "reprocess_all", signature)
    if previous_signature != signature:
        return StateBuildDecision(True, "status_inputs_changed", signature)
    return StateBuildDecision(False, "status_inputs_unchanged", signature)
```

Use this helper in `_run_changed_rust_normalizer_cycle_with_store` and `_run_idle_rust_normalizer_cycle_with_store`. Raw normalization continues exactly as before; only state/reporting rebuild is gated.

- [ ] **Step 4: Extend cycle log**

Add `state_build_reason` to `RustNormalizerCycleResult.to_json_dict()` and `_cycle_log_line()`.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/ingestion/test_rust_normalizer_sidecar.py -q
```

Expected: pass.

---

### Task 3: Create Compact Hot Probability Input Snapshot

**Files:**
- Create: `src/polymarket_engine/probability/hot_inputs.py`
- Test: `tests/probability/test_hot_inputs.py`

- [ ] **Step 1: Write tests for snapshot schema**

Create tests:

```python
def test_write_hot_probability_inputs_from_decision_states_round_trips(tmp_path: Path) -> None:
    out_path = tmp_path / "probability_inputs.json"
    state = _decision_state()

    write_hot_probability_inputs(
        out_path=out_path,
        states=(state,),
        generated_at=datetime(2026, 6, 6, 12, 0, tzinfo=UTC),
    )

    payload = read_hot_probability_inputs(out_path=out_path, limit=8, max_age_seconds=60)

    assert payload.schema_version == "polymarket-hot-probability-inputs-v1"
    assert len(payload.inputs) == 1
    assert payload.inputs[0].probability_input.state_id == state.state_id
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
uv run pytest tests/probability/test_hot_inputs.py -q
```

Expected: fail because module does not exist.

- [ ] **Step 3: Implement snapshot module**

Implement:

```python
HOT_PROBABILITY_INPUTS_SCHEMA_VERSION = "polymarket-hot-probability-inputs-v1"

@dataclass(frozen=True)
class HotProbabilityInputPayload:
    schema_version: str
    generated_at: datetime
    inputs: tuple[ProbabilityRuntimeInput, ...]
    skipped: int

def write_hot_probability_inputs(
    *,
    out_path: Path,
    states: Sequence[DecisionState],
    generated_at: datetime,
) -> None:
    rows = []
    skipped = 0
    for state in states:
        if state.data_quality_flags:
            skipped += 1
            continue
        probability_input = ProbabilityInput.from_decision_state(state)
        rows.append(_runtime_input_row(state=state, probability_input=probability_input))
    payload = {
        "schema_version": HOT_PROBABILITY_INPUTS_SCHEMA_VERSION,
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "inputs": rows,
        "skipped": skipped,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(f"{out_path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    durable_replace(tmp_path, out_path)
```

Also implement `read_hot_probability_inputs()` and validation that rejects stale or malformed snapshots.

- [ ] **Step 4: Run snapshot tests**

Run:

```bash
uv run pytest tests/probability/test_hot_inputs.py -q
```

Expected: pass.

---

### Task 4: Emit Hot Probability Inputs From State Build

**Files:**
- Modify: `src/polymarket_engine/features/rust_decision_snapshots.py`
- Modify: `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
- Test: `tests/features/test_rust_decision_snapshots.py`
- Test: `tests/ingestion/test_rust_normalizer_sidecar.py`

- [ ] **Step 1: Write failing tests for emitted snapshot**

Add a test that a state build writes `probability_inputs.json` and does not require querying `features.asof_state_inputs` to create live probability inputs.

- [ ] **Step 2: Add optional output path to state build**

Change signature:

```python
def build_current_decision_state_snapshots(
    *,
    status_path: Path,
    store: DuckDbIngestStore,
    include_next: bool = False,
    read_cache: CurrentDecisionStateReadCache | None = None,
    probability_inputs_path: Path | None = None,
) -> CurrentDecisionStateSnapshotResult:
```

After `store.upsert_asof_state_inputs(states)`, call:

```python
if probability_inputs_path is not None:
    write_hot_probability_inputs(
        out_path=probability_inputs_path,
        states=states,
        generated_at=asof_ts,
    )
```

- [ ] **Step 3: Thread path through normalizer**

Add `probability_inputs_path` to `run_rust_normalizer_cycle`, `run_rust_normalizer_loop`, `_run_*_cycle_with_store`, and the CLI.

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/features/test_rust_decision_snapshots.py tests/ingestion/test_rust_normalizer_sidecar.py -q
```

Expected: pass.

---

### Task 5: Make Probability Runtime Prefer Hot Inputs Over DuckDB Polling

**Files:**
- Modify: `src/polymarket_engine/probability/runtime.py`
- Modify: `src/polymarket_engine/runtime_api.py`
- Test: `tests/probability/test_runtime.py`
- Test: `tests/test_runtime_api.py`

- [ ] **Step 1: Write failing runtime tests**

Add:

```python
def test_build_probability_payload_uses_hot_inputs_without_duckdb_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    inputs_path = tmp_path / "probability_inputs.json"
    write_hot_probability_inputs(out_path=inputs_path, states=(_decision_state(),), generated_at=datetime.now(UTC))

    def fail_duckdb(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("DuckDB should not be read for hot probability inputs")

    monkeypatch.setattr("polymarket_engine.probability.runtime.latest_probability_inputs", fail_duckdb)

    payload = build_probability_payload(
        duckdb_path=tmp_path / "missing.duckdb",
        limit=8,
        allow_compute=True,
        probability_inputs_path=inputs_path,
    )

    assert payload["state"] in {"OK", "PARTIAL"}
```

- [ ] **Step 2: Add optional hot input parameter**

Change:

```python
def build_probability_payload(
    *,
    duckdb_path: Path,
    limit: int,
    allow_compute: bool = False,
    probability_inputs_path: Path | None = None,
) -> dict[str, Any]:
```

If `probability_inputs_path` exists and is fresh, use `read_hot_probability_inputs()` and compute rows from those inputs. Keep persisted-output and DuckDB fallback paths for API compatibility.

- [ ] **Step 3: Wire runtime API**

Add `probability_inputs_path` to `create_runtime_router()` and `create_app()` defaults, reading env `POLYMARKET_PROBABILITY_INPUTS_PATH`.

- [ ] **Step 4: Run focused tests**

Run:

```bash
uv run pytest tests/probability/test_runtime.py tests/test_runtime_api.py -q
```

Expected: pass.

---

### Task 6: Move Outcome Refresh To A Separate Sidecar

**Files:**
- Create: `src/polymarket_engine/validation/outcome_sidecar.py`
- Modify: `src/polymarket_engine/cli.py`
- Modify: `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
- Test: `tests/validation/test_outcome_sidecar.py`
- Test: `tests/test_cli.py`
- Test: `tests/ingestion/test_rust_normalizer_sidecar.py`

- [ ] **Step 1: Write failing sidecar tests**

Create:

```python
def test_outcome_sidecar_refreshes_on_own_cadence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fake_refresh(*args: Any, **kwargs: Any) -> int:
        nonlocal calls
        calls += 1
        return 0

    monkeypatch.setattr("polymarket_engine.validation.outcome_sidecar._upsert_market_outcomes", fake_refresh)
    monkeypatch.setattr("polymarket_engine.validation.outcome_sidecar.time.sleep", lambda _: None)

    run_outcome_refresh_loop(
        duckdb_path=tmp_path / "db.duckdb",
        outcome_status_path=tmp_path / "live" / "outcomes.json",
        interval_seconds=30.0,
        max_cycles=2,
    )

    assert calls == 2
```

- [ ] **Step 2: Implement sidecar loop**

Move or wrap existing `_upsert_market_outcomes()` logic into `outcome_sidecar.py` with:

```python
def run_outcome_refresh_loop(
    *,
    duckdb_path: Path,
    outcome_status_path: Path,
    interval_seconds: float,
    max_cycles: int | None = None,
) -> None:
    with DuckDbIngestStore(duckdb_path) as store:
        store.apply_schema()
        cycles = 0
        while max_cycles is None or cycles < max_cycles:
            _upsert_market_outcomes(store=store, out_path=outcome_status_path)
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                return
            time.sleep(interval_seconds)
```

- [ ] **Step 3: Disable normalizer hot-loop outcome refresh by default**

Add CLI flag `--enable-outcome-refresh` to `run-rust-normalizer-sidecar`. Default false for loop mode. Preserve current behavior for `--once` if needed by tests.

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/validation/test_outcome_sidecar.py tests/test_cli.py tests/ingestion/test_rust_normalizer_sidecar.py -q
```

Expected: pass.

---

### Task 7: Add DuckDB Runtime Settings And Batch Probability Output Writes

**Files:**
- Modify: `src/polymarket_engine/storage/duckdb_store.py`
- Modify: `src/polymarket_engine/probability/runtime.py`
- Test: `tests/storage/test_duckdb_store_settings.py`
- Test: `tests/probability/test_runtime.py`

- [ ] **Step 1: Write failing setting test**

Create:

```python
def test_store_applies_configured_duckdb_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POLYMARKET_DUCKDB_THREADS", "1")
    monkeypatch.setenv("POLYMARKET_DUCKDB_MEMORY_LIMIT", "512MB")
    monkeypatch.setenv("POLYMARKET_DUCKDB_PRESERVE_INSERTION_ORDER", "false")

    with DuckDbIngestStore(tmp_path / "state.duckdb") as store:
        with store._connection() as conn:
            assert conn.execute("select current_setting('threads')").fetchone()[0] == 1
            assert conn.execute("select current_setting('preserve_insertion_order')").fetchone()[0] is False
```

- [ ] **Step 2: Apply settings on connection creation**

In `DuckDbIngestStore.__enter__()` and `_connection()`, call:

```python
def _configure_connection(conn: duckdb.DuckDBPyConnection) -> None:
    if threads := os.getenv("POLYMARKET_DUCKDB_THREADS"):
        conn.execute("SET threads = ?", [int(threads)])
    if memory_limit := os.getenv("POLYMARKET_DUCKDB_MEMORY_LIMIT"):
        conn.execute("SET memory_limit = ?", [memory_limit])
    if preserve := os.getenv("POLYMARKET_DUCKDB_PRESERVE_INSERTION_ORDER"):
        conn.execute("SET preserve_insertion_order = ?", [preserve.lower() == "true"])
```

- [ ] **Step 3: Batch probability outputs**

Add `insert_probability_outputs()` that builds one Polars frame for many outputs. Update `_compute_and_persist_rows()` to collect rows and write once.

- [ ] **Step 4: Run storage/probability tests**

Run:

```bash
uv run pytest tests/storage/test_duckdb_store_settings.py tests/probability/test_runtime.py -q
```

Expected: pass.

---

### Task 8: Deploy Wiring For Separate Hot/Slow Services

**Files:**
- Modify: `deploy/normalizer/normalizer-entrypoint.sh`
- Modify: `deploy/collector/docker-compose.yml`
- Test: `tests/scripts/test_deploy_script.py`

- [ ] **Step 1: Add deploy tests**

Assert compose includes:

```python
assert "outcome-refresh" in compose_text
assert "POLYMARKET_PROBABILITY_INPUTS_PATH" in compose_text
assert "--probability-inputs-path" in normalizer_entrypoint
assert "--enable-outcome-refresh" not in normalizer_entrypoint
```

- [ ] **Step 2: Update normalizer entrypoint**

Add:

```sh
PROBABILITY_INPUTS_PATH="${POLYMARKET_PROBABILITY_INPUTS_PATH:-$LIVE_DIR/probability_inputs.json}"
```

Pass:

```sh
--probability-inputs-path "$PROBABILITY_INPUTS_PATH"
```

- [ ] **Step 3: Add outcome-refresh service**

In `deploy/collector/docker-compose.yml`, add service using the normalizer image:

```yaml
  outcome-refresh:
    image: ${POLYMARKET_NORMALIZER_IMAGE:-polymarket-normalizer:latest}
    restart: unless-stopped
    user: "${POLYMARKET_UID:-1000}:${POLYMARKET_GID:-1000}"
    depends_on:
      - normalizer
    entrypoint: ["/usr/bin/tini", "--"]
    command:
      [
        "polymarket-engine",
        "run-outcome-refresh-sidecar",
        "--duckdb-path",
        "/var/lib/polymarket/db/polymarket.duckdb",
        "--outcome-status-path",
        "/var/lib/polymarket/live/outcomes.json",
        "--interval-seconds",
        "${POLYMARKET_OUTCOME_REFRESH_INTERVAL_SECONDS:-30}",
      ]
```

- [ ] **Step 4: Run deploy tests**

Run:

```bash
uv run pytest tests/scripts/test_deploy_script.py -q
```

Expected: pass.

---

### Task 9: Verification And THEPC Rollout

**Files:**
- No source edits unless verification exposes a bug.

- [ ] **Step 1: Run local focused Python tests**

Run:

```bash
uv run pytest tests/features/test_rust_decision_snapshots.py tests/ingestion/test_rust_normalizer_sidecar.py tests/probability/test_hot_inputs.py tests/probability/test_runtime.py tests/validation/test_outcome_sidecar.py tests/storage/test_duckdb_store_settings.py tests/test_runtime_api.py tests/test_cli.py -q
```

Expected: pass.

- [ ] **Step 2: Run formatting/linting once**

Run:

```bash
uv run ruff check .
uv run mypy src tests
```

Expected: pass, or document unrelated existing failures.

- [ ] **Step 3: Build normalizer image**

Run:

```bash
docker build -t polymarket-normalizer:hot-state-latency-test -f deploy/normalizer/Dockerfile .
```

Expected: image builds.

- [ ] **Step 4: Deploy to THEPC only after reconciling remote drift**

Before deployment, run:

```bash
ssh ender@100.72.104.49 "wsl -d Ubuntu -- bash -lc 'cd /home/ender/polymarket && git status --short && git rev-parse --short HEAD'"
```

If remote has local-only probability event drain changes, pull them into this branch or manually port this branch on top of the remote state. Do not overwrite the live remote with older local code.

- [ ] **Step 5: Verify live performance**

Run:

```bash
ssh ender@100.72.104.49 "wsl -d Ubuntu -- bash -lc 'docker stats --no-stream --format \"{{.Name}} {{.CPUPerc}} {{.MemUsage}}\" && docker logs --tail 120 polymarket-rust-collector-normalizer-1 | tail -80'"
```

Expected: normalizer `state_ms` drops materially on cycles where status semantic inputs are unchanged; outcome-refresh spikes disappear from normalizer logs; collector raw freshness and normalized health remain OK.

---

## Self-Review

- Spec coverage: raw row ingestion remains unchanged, state rebuild is gated by semantic hot-state changes, hot probability inputs bypass DuckDB polling, DuckDB settings and batching are included, and outcome refresh is moved out of the hot loop.
- Placeholder scan: no `TBD`, `TODO`, or unspecified “add tests” steps remain.
- Type consistency: `probability_inputs_path`, `HotProbabilityInputPayload`, `StateBuildDecision`, and `hot_state_signature` are introduced before later tasks use them.
- Deployment risk: plan explicitly requires reconciling THEPC remote drift before deployment.
