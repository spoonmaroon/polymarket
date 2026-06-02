# Low-Latency Monte Carlo Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the verified hot replay bridge into a routine operational gate, make restart semantics explicit, preserve the Rust-only hot path, and add offline probability and Monte Carlo readiness without trading integration.

**Architecture:** The live path stays WebSocket event -> Rust in-memory state -> exact hot `DecisionState` -> async journal. Python, DuckDB, scripts, probability schemas, and Monte Carlo stay in replay, research, verification, and reporting. Probability outputs become derived, rebuildable artifacts from replay-safe `DecisionState` rows only after data gates pass.

**Tech Stack:** Python 3.11, pytest, DuckDB, Polars, NumPy, Rust 2024 workspace, cargo tests, Spoon read-only Docker runtime.

---

## Pre-Task-1 Evidence

- Branch is `codex/rust-raw-normalizer`.
- Starting local HEAD before Task 1 execution was `e980823e9ae3da0bdf5e20ff3befb2b9320a4e02`.
- Dirty files before execution are `docs/BINARY_CONTRACT_ENGINE_PLAN.md` and `tests/docs/test_active_runtime_docs.py`.
- Existing Spoon replay proof passed with `rows_checked=40`, `mismatch_count=0`, `rows_skipped_not_replay_ready=451`, and `rows_skipped_quality_blocked=2204`.
- Existing verifier command lives in `src/polymarket_engine/cli.py` and `src/polymarket_engine/features/hot_decision_replay.py`.
- Existing Rust hot path lives in `rust/crates/polymarket-live-probe/src/hot_decision.rs`, `state_manager.rs`, and `decision_journal.rs`.

## Boundaries

- No live trading, signing, private keys, or real orders.
- No DuckDB, Python, normalized health, or status polling on the hot decision path.
- Chainlink RTDS remains BTC/ETH reference and volatility source.
- Coinbase, Binance, and proxy feeds remain diagnostics only.
- Noise remains a data defect to suppress, reconcile, or block.

## Subagent Units

- **Agent A: Repo hygiene and doc boundary** - finish or commit the existing docs/doc-test dirt.
- **Agent B: Replay gate** - expand verifier skip reasons and add the safe Spoon replay gate script.
- **Agent C: Rust warm-state and telemetry** - make restart blocking explicit and add current-window latency telemetry.
- **Agent D: Probability artifact schema** - define pure replay-safe probability input/output schemas and storage.
- **Agent E: Offline Monte Carlo readiness** - add deterministic seeded offline scoring from as-of inputs.

## Risky Areas

- Active DuckDB copying can look current while missing WAL writes. Use a DuckDB read-only attach plus `COPY FROM DATABASE` into a local snapshot DB with retries, not a raw file copy.
- Restart handling must not silently turn missing start thresholds into probability-ready states.
- Latency reporting must separate current-window readiness from future-window prewarm diagnostics.
- Probability and Monte Carlo modules must reject quality-blocked or non-as-of inputs.

---

### Task 1: Repo Hygiene Commit

**Files:**
- Modify: `docs/BINARY_CONTRACT_ENGINE_PLAN.md`
- Modify: `tests/docs/test_active_runtime_docs.py`
- Create: `docs/superpowers/plans/2026-06-02-low-latency-monte-carlo-readiness.md`

- [ ] **Step 1: Run the focused doc test before changing docs**

Run:

```bash
uv run pytest -q tests/docs/test_active_runtime_docs.py
```

Expected: PASS if the existing dirty doc/test pair is coherent. If it fails, inspect only the failing assertions and adjust the doc wording or assertions to keep the active runtime boundary truthful.

- [ ] **Step 2: Commit the planning and doc-boundary state**

Run:

```bash
git add docs/BINARY_CONTRACT_ENGINE_PLAN.md tests/docs/test_active_runtime_docs.py docs/superpowers/plans/2026-06-02-low-latency-monte-carlo-readiness.md
git commit -m "Document active hot replay boundary"
```

Expected: repository has no dirty files after the commit.

---

### Task 2: Replay Verifier Skip Reasons

**Files:**
- Modify: `src/polymarket_engine/features/hot_decision_replay.py`
- Modify: `src/polymarket_engine/cli.py`
- Modify: `tests/features/test_hot_decision_replay_verifier.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing selection tests for skip-by-reason counts**

Add tests that build hot rows covering:

```python
assert selection.rows_skipped_quality_blocked_by_reason == {
    "MissingThreshold": 1,
    "MissingOrderbook": 1,
}
assert selection.rows_skipped_not_replay_ready_by_reason == {
    "price_observed_after_watermark": 1,
    "orderbook_observed_after_watermark": 1,
}
```

Run:

```bash
uv run pytest -q tests/features/test_hot_decision_replay_verifier.py::test_replay_selection_reports_skip_reasons
```

Expected: FAIL because `HotDecisionReplaySelection` has only aggregate skip counts.

- [ ] **Step 2: Add reason maps to the selection dataclass**

Implement:

```python
@dataclass(frozen=True)
class HotDecisionReplaySelection:
    rows: tuple[dict[str, Any], ...]
    rows_scanned: int
    rows_skipped_not_replay_ready: int
    rows_skipped_quality_blocked: int
    rows_skipped_not_replay_ready_by_reason: dict[str, int]
    rows_skipped_quality_blocked_by_reason: dict[str, int]
    price_observed_watermark: datetime | None
    orderbook_observed_watermark: datetime | None
```

Add helpers:

```python
def _quality_block_reasons(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(flag for flag in _string_list(row.get("data_quality_flags")) if flag in REPLAY_BLOCKING_HOT_FLAGS)

def _replay_not_ready_reasons(
    row: dict[str, Any],
    *,
    price_observed_watermark: datetime | None,
    orderbook_observed_watermark: datetime | None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    source_observed_ts = _observed_ts_from_age(row, "source_age_ms")
    if source_observed_ts is None:
        reasons.append("missing_source_age_ms")
    elif price_observed_watermark is None:
        reasons.append("missing_price_observed_watermark")
    elif source_observed_ts > price_observed_watermark:
        reasons.append("price_observed_after_watermark")
    if _has_hot_orderbook(row):
        book_observed_ts = _observed_ts_from_age(row, "book_age_ms")
        if book_observed_ts is None:
            reasons.append("missing_book_age_ms")
        elif orderbook_observed_watermark is None:
            reasons.append("missing_orderbook_observed_watermark")
        elif book_observed_ts > orderbook_observed_watermark:
            reasons.append("orderbook_observed_after_watermark")
    return tuple(reasons)
```

- [ ] **Step 3: Expose reason maps in CLI JSON**

Extend the verifier payload with:

```python
"rows_skipped_not_replay_ready_by_reason": selection.rows_skipped_not_replay_ready_by_reason,
"rows_skipped_quality_blocked_by_reason": selection.rows_skipped_quality_blocked_by_reason,
```

Run:

```bash
uv run pytest -q tests/features/test_hot_decision_replay_verifier.py tests/test_cli.py
```

Expected: PASS with existing aggregate counts unchanged.

- [ ] **Step 4: Commit**

```bash
git add src/polymarket_engine/features/hot_decision_replay.py src/polymarket_engine/cli.py tests/features/test_hot_decision_replay_verifier.py tests/test_cli.py
git commit -m "Report hot replay verifier skip reasons"
```

---

### Task 3: Safe Spoon Replay Gate Script

**Files:**
- Create: `scripts/run_hot_replay_gate.py`
- Create: `tests/scripts/test_run_hot_replay_gate.py`
- Modify: `docs/SPOON_DEPLOYMENT.md`
- Modify: `docs/PART_TWO_LIVE_COLLECTORS.md`
- Modify: `tests/docs/test_active_runtime_docs.py`

- [ ] **Step 1: Write failing tests for DuckDB snapshot copy**

Create a test that writes a source DuckDB, calls `_copy_duckdb_snapshot(source, snapshot)`, and reads the copied table from `snapshot`.

Expected assertion:

```python
assert duckdb.connect(str(snapshot), read_only=True).execute("select count(*) from core.price_ticks").fetchone() == (1,)
```

Run:

```bash
uv run pytest -q tests/scripts/test_run_hot_replay_gate.py::test_copies_duckdb_with_read_only_attach
```

Expected: FAIL because the script does not exist.

- [ ] **Step 2: Implement the snapshot copy helper**

Use a writable connection to the destination and attach the source read-only:

```python
def _copy_duckdb_snapshot(source: Path, snapshot: Path) -> None:
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if snapshot.exists():
        snapshot.unlink()
    with duckdb.connect(str(snapshot)) as conn:
        catalog = str(conn.execute("select current_database()").fetchone()[0])
        conn.execute("attach ? as source_db (read_only)", [str(source)])
        conn.execute(f'copy from database source_db to "{catalog}"')
```

- [ ] **Step 3: Write failing gate report test**

Fixture: raw hot decision JSONL plus normalized source DuckDB. Expected report fields:

```python
assert payload["ok"] is True
assert payload["rows_checked"] == 1
assert payload["mismatch_count"] == 0
assert payload["source_duckdb_path"] == str(source_db)
assert payload["snapshot_duckdb_path"].endswith("hot_replay_snapshot.duckdb")
```

Run:

```bash
uv run pytest -q tests/scripts/test_run_hot_replay_gate.py::test_gate_runs_verifier_against_snapshot
```

Expected: FAIL because the gate runner does not assemble the verifier report.

- [ ] **Step 4: Implement `run_hot_replay_gate.py`**

The script should:

```python
def run_gate(
    *,
    raw_root: Path,
    duckdb_path: Path,
    snapshot_dir: Path,
    report_out: Path,
    limit: int,
    scan_limit: int,
) -> dict[str, object]:
    snapshot_path = snapshot_dir / "hot_replay_snapshot.duckdb"
    _copy_duckdb_snapshot(duckdb_path, snapshot_path)
    store = DuckDbIngestStore(snapshot_path)
    scanned_rows = recent_hot_decision_rows(raw_root, limit=scan_limit)
    selection = replay_ready_hot_decision_rows(rows=scanned_rows, store=store, limit=limit)
    result = verify_hot_decision_rows(rows=selection.rows, store=store)
    payload = {
        "ok": result.ok and result.rows_checked > 0,
        "source_duckdb_path": str(duckdb_path),
        "snapshot_duckdb_path": str(snapshot_path),
        "raw_root": str(raw_root),
        "rows_scanned": selection.rows_scanned,
        "rows_checked": result.rows_checked,
        "rows_skipped_not_replay_ready": selection.rows_skipped_not_replay_ready,
        "rows_skipped_quality_blocked": selection.rows_skipped_quality_blocked,
        "rows_skipped_not_replay_ready_by_reason": selection.rows_skipped_not_replay_ready_by_reason,
        "rows_skipped_quality_blocked_by_reason": selection.rows_skipped_quality_blocked_by_reason,
        "mismatch_count": len(result.mismatches),
        "mismatches": [mismatch.__dict__ for mismatch in result.mismatches],
    }
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload
```

- [ ] **Step 5: Document the Spoon workflow**

Add the command:

```bash
python3 scripts/run_hot_replay_gate.py \
  --raw-root /home/spoon/polymarket-data/raw \
  --duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb \
  --snapshot-dir /home/spoon/polymarket-data/live/hot-replay-snapshot \
  --report-out /home/spoon/polymarket-data/live/hot_decision_replay_report.json \
  --limit 40 \
  --scan-limit 5000
```

Run:

```bash
uv run pytest -q tests/scripts/test_run_hot_replay_gate.py tests/docs/test_active_runtime_docs.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/run_hot_replay_gate.py tests/scripts/test_run_hot_replay_gate.py docs/SPOON_DEPLOYMENT.md docs/PART_TWO_LIVE_COLLECTORS.md tests/docs/test_active_runtime_docs.py
git commit -m "Add safe hot replay gate workflow"
```

---

### Task 4: Explicit Restart Warm-State Blocking

**Files:**
- Modify: `rust/crates/polymarket-runtime-types/src/decision.rs`
- Modify: `rust/crates/polymarket-live-probe/src/hot_decision.rs`
- Modify: `rust/crates/polymarket-live-probe/src/state_manager.rs`
- Modify: `src/polymarket_engine/features/hot_decision_replay.py`
- Modify: `docs/PART_TWO_LIVE_COLLECTORS.md`
- Modify: `docs/SPOON_DEPLOYMENT.md`

Decision: explicitly block current-window decisions after restart when the Rust process cannot prove the window-start Chainlink threshold in memory. Do not recover from raw files inside the hot path. The block remains visible in hot `DecisionState` JSONL and replay reports until the next warmed window starts.

- [ ] **Step 1: Write failing Rust test for restart block flag**

Add a test in `hot_decision.rs`:

```rust
#[test]
fn restart_started_after_window_start_blocks_missing_threshold_current_window() {
    let start = Utc.timestamp_opt(1_780_302_400, 0).unwrap();
    let asof = start + Duration::seconds(20);
    let config = HotDecisionConfig {
        restart_started_at: Some(start + Duration::seconds(5)),
        ..HotDecisionConfig::default()
    };
    let states = HotDecisionBuilder::new(config).build_for_event(
        &HotPathEvent::ChainlinkPrice {
            symbol: "BTC/USD".to_owned(),
            event_ts: asof,
            observed_ts: asof,
        },
        &[sample_contract(start)],
        &[price("BTC/USD", asof, asof, 70_050)],
        &[book("up-token", asof, asof, 61, 64), book("down-token", asof, asof, 39, 42)],
        asof,
    );
    assert!(states[0].data_quality_flags.contains(&HotDecisionQualityFlag::MissingThreshold));
    assert!(states[0].data_quality_flags.contains(&HotDecisionQualityFlag::RestartWarmupBlocked));
}
```

Run:

```bash
cd rust && cargo test -p polymarket-live-probe restart_started_after_window_start_blocks_missing_threshold_current_window
```

Expected: FAIL because the flag and config field do not exist.

- [ ] **Step 2: Implement the flag and config**

Add enum variant:

```rust
RestartWarmupBlocked,
```

Add config field:

```rust
pub restart_started_at: Option<DateTime<Utc>>,
```

In the builder, after `MissingThreshold`:

```rust
if threshold.is_none()
    && self
        .config
        .restart_started_at
        .is_some_and(|started_at| contract.window.start_ts < started_at)
{
    flags.push(HotDecisionQualityFlag::RestartWarmupBlocked);
}
```

In `StateManagerRuntime`, pass the runtime start timestamp into `HotDecisionConfig`.

- [ ] **Step 3: Add Python verifier quality mapping**

Map the new flag to a replay block reason:

```python
REPLAY_BLOCKING_HOT_FLAGS = {
    "MissingThreshold",
    "MissingSettlementPrice",
    "MissingOrderbook",
    "RestartWarmupBlocked",
}
```

No replay comparison should run for rows carrying `RestartWarmupBlocked`.

- [ ] **Step 4: Run focused tests**

```bash
cd rust && cargo test -p polymarket-runtime-types -p polymarket-live-probe hot_decision
uv run pytest -q tests/features/test_hot_decision_replay_verifier.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rust/crates/polymarket-runtime-types/src/decision.rs rust/crates/polymarket-live-probe/src/hot_decision.rs rust/crates/polymarket-live-probe/src/state_manager.rs src/polymarket_engine/features/hot_decision_replay.py docs/PART_TWO_LIVE_COLLECTORS.md docs/SPOON_DEPLOYMENT.md
git commit -m "Block restart-warm current decisions explicitly"
```

---

### Task 5: Current-Window Latency Telemetry

**Files:**
- Modify: `rust/crates/polymarket-live-probe/src/report.rs`
- Modify: `rust/crates/polymarket-live-probe/src/decision_journal.rs`
- Modify: `rust/crates/polymarket-live-probe/src/hot_decision.rs`
- Modify: `scripts/verify_state_manager_report.py`
- Modify: `src/polymarket_engine/monitor.py`
- Modify: `tests/test_monitor.py`
- Modify: `tests/scripts/test_verify_state_manager_report.py`

- [ ] **Step 1: Write failing Rust report test for current-window latency marks**

Expected marks:

```rust
assert!(names.contains("current_orderbook_observed_age_ms"));
assert!(names.contains("current_orderbook_event_to_observed_ms"));
assert!(names.contains("all_orderbook_observed_age_ms"));
assert!(names.contains("all_orderbook_event_to_observed_ms"));
```

Run:

```bash
cd rust && cargo test -p polymarket-live-probe state_manager_report_exposes_current_window_latency_marks
```

Expected: FAIL because only all-window `orderbook_*` marks exist.

- [ ] **Step 2: Rename all-window marks and add current-window marks**

Keep existing chainlink marks. For order books:

```rust
current_orderbook_observed_age_ms
current_orderbook_event_to_observed_ms
next_orderbook_observed_age_ms
next_orderbook_event_to_observed_ms
all_orderbook_observed_age_ms
all_orderbook_event_to_observed_ms
```

Use current/next token sets from the `WarmStateSnapshot` to filter order books before taking max age.

- [ ] **Step 3: Write failing journal test for persist timing**

In `decision_journal.rs`, assert the serialized row has non-null:

```rust
assert!(row["latency"]["state_to_persist_us"].as_u64().is_some());
assert!(row["latency"]["total_event_to_persist_ms"].as_u64().is_some());
```

Run:

```bash
cd rust && cargo test -p polymarket-live-probe sink_records_hot_decision_with_persist_timing
```

Expected: FAIL because the journal currently writes `None`.

- [ ] **Step 4: Fill persist timing inside the async journal worker**

Send an envelope from `HotDecisionSink::try_record`:

```rust
struct HotDecisionEnvelope {
    state: HotDecisionState,
    queued_at: Instant,
}
```

Before append:

```rust
let state_to_persist_us = envelope.queued_at.elapsed().as_micros();
envelope.state.latency.state_to_persist_us = Some(state_to_persist_us);
envelope.state.latency.total_event_to_persist_ms = Some(
    u128::try_from(envelope.state.latency.trigger_event_to_observed_ms).unwrap_or(0)
        + envelope.state.latency.observed_to_state_us / 1_000
        + state_to_persist_us / 1_000,
);
```

This stays in the async persistence worker and does not add synchronous file I/O to the hot decision builder.

- [ ] **Step 5: Update verifier and monitor expectations**

`scripts/verify_state_manager_report.py` should require the new current/all-window marks and accept the old `orderbook_*` names only if a report schema is older than this branch. `src/polymarket_engine/monitor.py` should render current-window marks first.

Run:

```bash
cd rust && cargo test --workspace
uv run pytest -q tests/scripts/test_verify_state_manager_report.py tests/test_monitor.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add rust/crates/polymarket-live-probe/src/report.rs rust/crates/polymarket-live-probe/src/decision_journal.rs rust/crates/polymarket-live-probe/src/hot_decision.rs scripts/verify_state_manager_report.py src/polymarket_engine/monitor.py tests/test_monitor.py tests/scripts/test_verify_state_manager_report.py
git commit -m "Separate current-window latency telemetry"
```

---

### Task 6: Probability Input and Output Schemas

**Files:**
- Create: `src/polymarket_engine/probability/__init__.py`
- Create: `src/polymarket_engine/probability/schema.py`
- Create: `tests/probability/test_probability_schema.py`
- Modify: `src/polymarket_engine/storage/schema.sql`
- Modify: `src/polymarket_engine/storage/duckdb_store.py`
- Modify: `tests/storage/test_normalized_writes.py`

- [ ] **Step 1: Write failing tests for replay-safe input construction**

Test behavior:

```python
ready = ProbabilityInput.from_decision_state(_ready_state())
assert ready.z_path > 0
assert ready.executable_price == 0.64
```

Blocked state behavior:

```python
with pytest.raises(ValueError, match="quality-blocked"):
    ProbabilityInput.from_decision_state(dataclasses.replace(_ready_state(), data_quality_flags=("stale_orderbook",)))
```

Run:

```bash
uv run pytest -q tests/probability/test_probability_schema.py
```

Expected: FAIL because `polymarket_engine.probability.schema` does not exist.

- [ ] **Step 2: Implement schema dataclasses**

Create:

```python
@dataclass(frozen=True)
class ProbabilityInput:
    state_id: str
    asof_ts: datetime
    asset: str
    side: str
    seconds_left: float
    settlement_price: float
    threshold: float
    sigma_tau: float
    executable_price: float
    source_age_ms: int
    book_age_ms: int
    z_path: float

    @classmethod
    def from_decision_state(cls, state: DecisionState) -> ProbabilityInput:
        if state.data_quality_flags:
            raise ValueError("quality-blocked DecisionState cannot build probability input")
        if state.sigma_tau is None or state.sigma_tau <= 0:
            raise ValueError("probability input requires positive sigma_tau")
        if state.executable_price is None:
            raise ValueError("probability input requires executable_price")
        if state.source_age_ms is None or state.book_age_ms is None:
            raise ValueError("probability input requires source and book ages")
        signed_log_distance = math.log(state.settlement_price / state.threshold)
        if state.contract.side == "DOWN":
            signed_log_distance *= -1
        return cls(..., z_path=signed_log_distance / state.sigma_tau)
```

Create `ProbabilityOutput` with finite `p_finish`, `p_no_touch`, `z_path`, `model_version`, `seed`, and `diagnostics`.

- [ ] **Step 3: Add derived artifact storage**

Add table:

```sql
CREATE TABLE IF NOT EXISTS features.probability_outputs (
    output_id VARCHAR PRIMARY KEY,
    state_id VARCHAR NOT NULL,
    asof_ts TIMESTAMPTZ NOT NULL,
    model_version VARCHAR NOT NULL,
    p_finish DOUBLE NOT NULL,
    p_no_touch DOUBLE NOT NULL,
    z_path DOUBLE NOT NULL,
    seed UBIGINT,
    input_json VARCHAR NOT NULL,
    output_json VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
```

Add `DuckDbIngestStore.insert_probability_output(output_id, probability_input, output)`.

- [ ] **Step 4: Run focused tests**

```bash
uv run pytest -q tests/probability/test_probability_schema.py tests/storage/test_normalized_writes.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/probability tests/probability src/polymarket_engine/storage/schema.sql src/polymarket_engine/storage/duckdb_store.py tests/storage/test_normalized_writes.py
git commit -m "Add replay-safe probability schemas"
```

---

### Task 7: Offline Monte Carlo Readiness

**Files:**
- Create: `src/polymarket_engine/probability/monte_carlo.py`
- Create: `tests/probability/test_monte_carlo.py`
- Modify: `docs/BINARY_CONTRACT_ENGINE_PLAN.md`
- Modify: `tests/docs/test_active_runtime_docs.py`

- [ ] **Step 1: Write failing deterministic scoring tests**

Use explicit paths first:

```python
paths = (
    (100.0, 101.0, 102.0),
    (100.0, 99.0, 98.0),
)
output = score_paths(probability_input, paths=paths, model_version="mc-fixture", seed=7)
assert output.p_finish == 0.5
assert output.p_no_touch == 0.5
assert output.seed == 7
```

Run:

```bash
uv run pytest -q tests/probability/test_monte_carlo.py::test_scores_explicit_paths_deterministically
```

Expected: FAIL because the Monte Carlo module does not exist.

- [ ] **Step 2: Implement pure path scoring**

Create:

```python
def score_paths(
    probability_input: ProbabilityInput,
    *,
    paths: Sequence[Sequence[float]],
    model_version: str,
    seed: int,
) -> ProbabilityOutput:
    terminal_wins = 0
    no_touch_wins = 0
    for path in paths:
        _validate_path(path)
        if probability_input.side == "UP":
            terminal_wins += path[-1] >= probability_input.threshold
            no_touch_wins += all(price >= probability_input.threshold for price in path[1:])
        else:
            terminal_wins += path[-1] < probability_input.threshold
            no_touch_wins += all(price < probability_input.threshold for price in path[1:])
    count = len(paths)
    return ProbabilityOutput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        p_finish=terminal_wins / count,
        p_no_touch=no_touch_wins / count,
        z_path=probability_input.z_path,
        model_version=model_version,
        seed=seed,
        diagnostics={"path_count": count},
    )
```

- [ ] **Step 3: Write failing seeded generator tests**

Expected behavior:

```python
left = run_seeded_monte_carlo(probability_input, path_count=1000, steps=20, seed=123)
right = run_seeded_monte_carlo(probability_input, path_count=1000, steps=20, seed=123)
assert left == right
assert 0.0 <= left.p_finish <= 1.0
assert 0.0 <= left.p_no_touch <= 1.0
```

Run:

```bash
uv run pytest -q tests/probability/test_monte_carlo.py::test_seeded_monte_carlo_is_deterministic
```

Expected: FAIL because no seeded runner exists.

- [ ] **Step 4: Implement seeded offline path generation**

Use NumPy only inside the offline module:

```python
def run_seeded_monte_carlo(
    probability_input: ProbabilityInput,
    *,
    path_count: int,
    steps: int,
    seed: int,
) -> ProbabilityOutput:
    if path_count <= 0:
        raise ValueError("path_count must be positive")
    if steps <= 0:
        raise ValueError("steps must be positive")
    rng = np.random.default_rng(seed)
    per_step_sigma = probability_input.sigma_tau / math.sqrt(steps)
    returns = rng.normal(0.0, per_step_sigma, size=(path_count, steps))
    log_start = math.log(probability_input.settlement_price)
    log_paths = log_start + np.cumsum(returns, axis=1)
    prices = np.exp(log_paths)
    full_paths = np.concatenate(
        [np.full((path_count, 1), probability_input.settlement_price), prices],
        axis=1,
    )
    return score_paths(
        probability_input,
        paths=tuple(tuple(float(value) for value in row) for row in full_paths),
        model_version="offline-lognormal-chainlink-sigma-v1",
        seed=seed,
    )
```

This is a deterministic offline baseline. It is not a live trading model claim.

- [ ] **Step 5: Document readiness only**

Update docs to say probability/Monte Carlo are offline derived artifacts from as-of `DecisionState`; they are not live authority, paper trading, or execution.

Run:

```bash
uv run pytest -q tests/probability/test_monte_carlo.py tests/docs/test_active_runtime_docs.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/polymarket_engine/probability/monte_carlo.py tests/probability/test_monte_carlo.py docs/BINARY_CONTRACT_ENGINE_PLAN.md tests/docs/test_active_runtime_docs.py
git commit -m "Add offline Monte Carlo readiness"
```

---

### Task 8: Spoon Read-Only Verification And Report

**Files:**
- Create: `reports/monte-carlo-readiness-2026-06-02.md`
- Modify: `docs/SPOON_DEPLOYMENT.md`

- [ ] **Step 1: Run full local verification once**

Run:

```bash
uv run ruff check .
uv run mypy src tests
uv run pytest -q
cd rust && cargo test --workspace
```

Expected: all checks pass.

- [ ] **Step 2: Run the Spoon read-only replay gate**

Run:

```bash
ssh spoon 'cd /home/spoon/polymarket && python3 scripts/run_hot_replay_gate.py --raw-root /home/spoon/polymarket-data/raw --duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb --snapshot-dir /home/spoon/polymarket-data/live/hot-replay-snapshot --report-out /home/spoon/polymarket-data/live/hot_decision_replay_report.json --limit 40 --scan-limit 5000'
```

Expected: report JSON has `ok=true`, `rows_checked > 0`, and `mismatch_count=0`. It also includes skip counts by reason.

- [ ] **Step 3: Inspect Spoon live telemetry without mutating runtime**

Run:

```bash
ssh spoon 'python3 /home/spoon/polymarket/scripts/verify_state_manager_report.py /home/spoon/polymarket-data/live/status.json --expected-prewarm-windows 2'
ssh spoon 'python3 /home/spoon/polymarket/scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json --raw-root /home/spoon/polymarket-data/raw --normalized-health-path /home/spoon/polymarket-data/live/normalized_health.json --expected-prewarm-windows 2'
```

Expected: health passes. Current-window latency marks are present. `dropped_events=0`.

- [ ] **Step 4: Write readiness report**

Report must include:

```text
- Git SHA verified.
- Replay gate report path and key counts.
- Skip-by-reason table.
- Chainlink observed age.
- Current-window orderbook observed age.
- Hot DecisionState build microseconds.
- Dropped events.
- Current-window event-to-observed lag.
- State-to-persist timing if present.
- Explicit statement that Monte Carlo is offline/replay/research only.
```

- [ ] **Step 5: Commit report**

```bash
git add reports/monte-carlo-readiness-2026-06-02.md docs/SPOON_DEPLOYMENT.md
git commit -m "Report Monte Carlo readiness gate"
```

---

## Final Verification

Run exactly once after implementation:

```bash
uv run ruff check .
uv run mypy src tests
uv run pytest -q
cd rust && cargo test --workspace
```

Expected:

```text
All checks pass.
```

## Approval Gate

Do not implement runtime/code changes until Enoch approves this plan and chooses execution mode.
