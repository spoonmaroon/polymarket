# Wave Signal Dynamic Edge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a paper-only terminal-wave signal that uses dynamic edge as the real gate and shows visible markers at 0.90, 0.95, and the 0.96 tick-size regime.

**Architecture:** Keep the first version transient and read-only: compute wave fields from the current as-of `ProbabilityInput` plus probability/gate diagnostics, then expose those fields through `/api/runtime/probabilities`, the Rust TUI, the browser monitor, and TUI logs. Do not add live trading, signing, order placement, or persistent replay-label tables in this plan.

**Tech Stack:** Python 3.11 via `uv`, DuckDB-backed runtime probability rows, pytest, Rust/ratatui TUI, serde, TypeScript React browser UI, npm/tsc.

---

## File Structure

- Create `src/polymarket_engine/probability/wave_signal.py`
  - Owns the pure wave classifier.
  - Depends only on scalar probability/execution/gate inputs.
  - Returns JSON-safe fields for runtime rows.
- Create `tests/probability/test_wave_signal.py`
  - Unit tests for phase transitions, dynamic edge gate, and visible markers.
- Modify `src/polymarket_engine/probability/runtime.py`
  - Applies the wave classifier to both grid-hit and grid-refresh runtime rows.
  - Uses current as-of executable price from `ProbabilityInput`, not cached future data.
- Modify `tests/test_runtime_api.py`
  - Verifies `/api/runtime/probabilities` includes wave fields on grid hits and refreshes.
- Modify `rust/crates/polymarket-cockpit-tui/src/status.rs`
  - Adds optional serde fields for `wave_score`, `wave_phase`, `wave_reasons`, `wave_markers`, `dynamic_edge`, and `dynamic_required_edge`.
- Modify `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`
  - Adds a compact `wave` column with phase, score, and markers.
- Modify `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
  - Adds wave summary to MC/probability log lines.
- Modify `ui/src/App.tsx`
  - Adds TypeScript fields and a compact wave column/detail block.
- Modify `ui/src/styles.css`
  - Adds small non-overlapping phase/marker styles.

## Rules

- Use test-first implementation for every production change.
- Keep all wave fields paper/read-only.
- Use only as-of-safe fields already present in the runtime probability input and diagnostics.
- Dynamic edge is the real gate: a row is actionable only when `dynamic_edge >= dynamic_required_edge`.
- Markers are visible but not gates by themselves:
  - `P90` when executable price is at least `0.90`.
  - `P95` when executable price is at least `0.95`.
  - `TICK96` when executable price is at least `0.96`.
- `missed` means the visible wave zone is present but dynamic edge fails.
- `late` means the visible wave zone is very expensive but dynamic edge still passes.

---

### Task 1: Add Pure Wave Signal Classifier

**Files:**
- Create: `src/polymarket_engine/probability/wave_signal.py`
- Test: `tests/probability/test_wave_signal.py`

- [ ] **Step 1: Write failing tests for dynamic edge and marker phases**

Create `tests/probability/test_wave_signal.py`:

```python
from __future__ import annotations

import pytest

from polymarket_engine.probability.wave_signal import WaveSignalInput
from polymarket_engine.probability.wave_signal import classify_wave_signal


def test_wave_signal_marks_breaking_when_price_is_90_and_dynamic_edge_passes() -> None:
    signal = classify_wave_signal(
        WaveSignalInput(
            p_finish=0.97,
            p_no_touch=0.86,
            executable_price=0.90,
            edge_after_costs=0.045,
            required_edge=0.030,
            seconds_left=18.0,
            source_age_ms=80,
            book_age_ms=90,
        )
    )

    assert signal["wave_phase"] == "breaking"
    assert signal["wave_score"] == pytest.approx(1.0)
    assert signal["wave_markers"] == ["P90"]
    assert signal["dynamic_edge"] == pytest.approx(0.045)
    assert signal["dynamic_required_edge"] == pytest.approx(0.030)
    assert "EDGE_OK" in signal["wave_reasons"]


def test_wave_signal_marks_late_when_price_is_95_but_edge_still_passes() -> None:
    signal = classify_wave_signal(
        WaveSignalInput(
            p_finish=0.99,
            p_no_touch=0.95,
            executable_price=0.955,
            edge_after_costs=0.026,
            required_edge=0.020,
            seconds_left=9.0,
            source_age_ms=70,
            book_age_ms=75,
        )
    )

    assert signal["wave_phase"] == "late"
    assert signal["wave_markers"] == ["P90", "P95"]
    assert "PRICE_95" in signal["wave_reasons"]


def test_wave_signal_marks_tick_size_regime_at_96() -> None:
    signal = classify_wave_signal(
        WaveSignalInput(
            p_finish=0.995,
            p_no_touch=0.96,
            executable_price=0.965,
            edge_after_costs=0.021,
            required_edge=0.020,
            seconds_left=5.0,
            source_age_ms=50,
            book_age_ms=55,
        )
    )

    assert signal["wave_phase"] == "late"
    assert signal["wave_markers"] == ["P90", "P95", "TICK96"]
    assert "TICK_SIZE_96" in signal["wave_reasons"]


def test_wave_signal_marks_missed_when_visible_wave_zone_lacks_edge() -> None:
    signal = classify_wave_signal(
        WaveSignalInput(
            p_finish=0.96,
            p_no_touch=0.91,
            executable_price=0.94,
            edge_after_costs=0.010,
            required_edge=0.030,
            seconds_left=11.0,
            source_age_ms=80,
            book_age_ms=85,
        )
    )

    assert signal["wave_phase"] == "missed"
    assert signal["wave_markers"] == ["P90"]
    assert "EDGE_SHORT" in signal["wave_reasons"]


def test_wave_signal_uses_fallback_edge_when_gate_fields_are_missing() -> None:
    signal = classify_wave_signal(
        WaveSignalInput(
            p_finish=0.88,
            p_no_touch=0.82,
            executable_price=0.72,
            edge_after_costs=None,
            required_edge=None,
            seconds_left=24.0,
            source_age_ms=120,
            book_age_ms=130,
        )
    )

    assert signal["wave_phase"] == "forming"
    assert signal["dynamic_edge"] == pytest.approx(0.16)
    assert signal["dynamic_required_edge"] == pytest.approx(0.03)
    assert signal["wave_markers"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/probability/test_wave_signal.py -q
```

Expected: FAIL because `polymarket_engine.probability.wave_signal` does not exist.

- [ ] **Step 3: Implement the pure classifier**

Create `src/polymarket_engine/probability/wave_signal.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

WavePhase = Literal["none", "forming", "breaking", "late", "missed"]


@dataclass(frozen=True)
class WaveSignalInput:
    p_finish: float
    p_no_touch: float
    executable_price: float | None
    edge_after_costs: float | None
    required_edge: float | None
    seconds_left: float
    source_age_ms: int
    book_age_ms: int


class WaveSignal(TypedDict):
    wave_score: float
    wave_phase: WavePhase
    wave_reasons: list[str]
    wave_markers: list[str]
    dynamic_edge: float | None
    dynamic_required_edge: float | None


def classify_wave_signal(signal_input: WaveSignalInput) -> WaveSignal:
    _validate_probability(signal_input.p_finish, "p_finish")
    _validate_probability(signal_input.p_no_touch, "p_no_touch")
    if signal_input.executable_price is not None:
        _validate_probability(signal_input.executable_price, "executable_price")
    if signal_input.seconds_left < 0:
        raise ValueError("seconds_left must be nonnegative")
    if signal_input.source_age_ms < 0 or signal_input.book_age_ms < 0:
        raise ValueError("source_age_ms and book_age_ms must be nonnegative")

    dynamic_edge = _dynamic_edge(signal_input)
    dynamic_required_edge = _dynamic_required_edge(signal_input)
    edge_ok = (
        dynamic_edge is not None
        and dynamic_required_edge is not None
        and dynamic_edge >= dynamic_required_edge
    )
    executable = signal_input.executable_price
    markers = _markers(executable)
    reasons = _reasons(
        signal_input=signal_input,
        markers=markers,
        edge_ok=edge_ok,
        dynamic_edge=dynamic_edge,
        dynamic_required_edge=dynamic_required_edge,
    )
    phase = _phase(
        signal_input=signal_input,
        markers=markers,
        edge_ok=edge_ok,
        dynamic_edge=dynamic_edge,
        dynamic_required_edge=dynamic_required_edge,
    )
    return {
        "wave_score": _wave_score(signal_input, dynamic_edge, dynamic_required_edge),
        "wave_phase": phase,
        "wave_reasons": reasons,
        "wave_markers": markers,
        "dynamic_edge": dynamic_edge,
        "dynamic_required_edge": dynamic_required_edge,
    }


def _dynamic_edge(signal_input: WaveSignalInput) -> float | None:
    if signal_input.edge_after_costs is not None:
        return float(signal_input.edge_after_costs)
    if signal_input.executable_price is None:
        return None
    return signal_input.p_finish - signal_input.executable_price


def _dynamic_required_edge(signal_input: WaveSignalInput) -> float | None:
    if signal_input.required_edge is not None:
        return float(signal_input.required_edge)
    if signal_input.executable_price is None:
        return None
    age_penalty = 0.0
    if signal_input.source_age_ms > 1_000:
        age_penalty += 0.01
    if signal_input.book_age_ms > 1_000:
        age_penalty += 0.01
    terminal_penalty = 0.01 if signal_input.seconds_left <= 15 else 0.0
    return 0.02 + age_penalty + terminal_penalty


def _markers(executable_price: float | None) -> list[str]:
    if executable_price is None:
        return []
    markers: list[str] = []
    if executable_price >= 0.90:
        markers.append("P90")
    if executable_price >= 0.95:
        markers.append("P95")
    if executable_price >= 0.96:
        markers.append("TICK96")
    return markers


def _phase(
    *,
    signal_input: WaveSignalInput,
    markers: list[str],
    edge_ok: bool,
    dynamic_edge: float | None,
    dynamic_required_edge: float | None,
) -> WavePhase:
    visible_wave = bool(markers) or signal_input.p_finish >= 0.90
    if visible_wave and not edge_ok:
        return "missed"
    if "P95" in markers or "TICK96" in markers:
        return "late" if edge_ok else "missed"
    if "P90" in markers:
        return "breaking" if edge_ok else "missed"
    if edge_ok and signal_input.p_finish >= 0.75:
        return "forming"
    if (
        dynamic_edge is not None
        and dynamic_required_edge is not None
        and dynamic_edge >= dynamic_required_edge * 0.75
        and signal_input.p_finish >= 0.70
    ):
        return "forming"
    return "none"


def _wave_score(
    signal_input: WaveSignalInput,
    dynamic_edge: float | None,
    dynamic_required_edge: float | None,
) -> float:
    probability_score = _clamp((signal_input.p_finish - 0.50) / 0.50)
    edge_score = 0.0
    if dynamic_edge is not None and dynamic_required_edge is not None and dynamic_required_edge > 0:
        edge_score = _clamp(dynamic_edge / dynamic_required_edge)
    price_bonus = 0.0
    executable = signal_input.executable_price
    if executable is not None:
        if executable >= 0.96:
            price_bonus = 0.20
        elif executable >= 0.95:
            price_bonus = 0.15
        elif executable >= 0.90:
            price_bonus = 0.10
    return round(_clamp((0.55 * probability_score) + (0.45 * edge_score) + price_bonus), 3)


def _reasons(
    *,
    signal_input: WaveSignalInput,
    markers: list[str],
    edge_ok: bool,
    dynamic_edge: float | None,
    dynamic_required_edge: float | None,
) -> list[str]:
    reasons: list[str] = []
    if signal_input.executable_price is None:
        reasons.append("NO_EXECUTABLE_PRICE")
    if dynamic_edge is not None and dynamic_required_edge is not None:
        reasons.append("EDGE_OK" if edge_ok else "EDGE_SHORT")
    if signal_input.p_finish >= 0.90:
        reasons.append("HIGH_P_FINISH")
    if signal_input.p_no_touch >= 0.90:
        reasons.append("HIGH_P_NO_TOUCH")
    if "P90" in markers:
        reasons.append("PRICE_90")
    if "P95" in markers:
        reasons.append("PRICE_95")
    if "TICK96" in markers:
        reasons.append("TICK_SIZE_96")
    if signal_input.seconds_left <= 15:
        reasons.append("TERMINAL_WINDOW")
    return reasons


def _validate_probability(value: float, field_name: str) -> None:
    if isinstance(value, bool) or not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be between 0 and 1")


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
```

- [ ] **Step 4: Run tests to verify classifier passes**

Run:

```bash
uv run pytest tests/probability/test_wave_signal.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit classifier**

```bash
git add src/polymarket_engine/probability/wave_signal.py tests/probability/test_wave_signal.py
git commit -m "feat: classify terminal wave signals"
```

---

### Task 2: Thread Wave Fields Into Runtime Probability Rows

**Files:**
- Modify: `src/polymarket_engine/probability/runtime.py`
- Modify: `tests/test_runtime_api.py`

- [ ] **Step 1: Write failing API test for grid-hit wave fields**

In `tests/test_runtime_api.py`, add this test after `test_runtime_probabilities_uses_safe_grid_cache_before_mc`:

```python
def test_runtime_probabilities_adds_wave_signal_to_grid_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = replace(_decision_state(), best_bid=0.89, best_ask=0.90, executable_price=0.90)
    probability_input = ProbabilityInput.from_decision_state(state)
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)
    entry = grid_entry_from_probability_input(
        probability_input,
        market_slug=state.contract.slug,
        start_ts=state.contract.start_ts,
        expiry_ts=state.contract.expiry_ts,
        p_finish=0.97,
        p_no_touch=0.86,
        u_gen=0.046,
        path_count=10_000,
        seed=20260605,
        volatility_regime=state.volatility_regime,
        training_cutoff_ts=state.asof_ts,
        max_event_ts=state.asof_ts,
        max_observed_ts=state.asof_ts,
        generated_at=datetime.now(UTC),
        valid_from=datetime.now(UTC) - timedelta(seconds=1),
        valid_until=datetime.now(UTC) + timedelta(seconds=30),
        diagnostics={
            "gate": {
                "edge_after_costs": 0.045,
                "required_edge": 0.030,
                "decision_hint": "TRADE_CANDIDATE",
            }
        },
    )
    upsert_probability_grid_entry(store, entry)

    def fail_compute(*_: object, **__: object) -> NoReturn:
        raise AssertionError("probability API should use safe probability grid cache")

    monkeypatch.setattr(
        "polymarket_engine.probability.runtime._compute_and_persist_rows",
        fail_compute,
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=db_path,
        enable_runtime_probabilities=True,
    )

    response = TestClient(app).get("/api/runtime/probabilities?limit=4")

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["wave_phase"] == "breaking"
    assert row["wave_markers"] == ["P90"]
    assert row["wave_score"] == pytest.approx(1.0)
    assert row["dynamic_edge"] == pytest.approx(0.045)
    assert row["dynamic_required_edge"] == pytest.approx(0.030)
    assert "EDGE_OK" in row["wave_reasons"]
```

- [ ] **Step 2: Write failing API test for refresh wave fields and missed marker**

In `tests/test_runtime_api.py`, add this test after `test_runtime_probabilities_refreshes_grid_on_miss_even_with_persisted_output`:

```python
def test_runtime_probabilities_adds_wave_signal_to_refresh_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = replace(_decision_state(), best_bid=0.93, best_ask=0.94, executable_price=0.94)
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=db_path,
        enable_runtime_probabilities=True,
    )

    response = TestClient(app).get("/api/runtime/probabilities?limit=4")

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["cache_status"] == "REFRESH"
    assert "wave_phase" in row
    assert "wave_score" in row
    assert "dynamic_edge" in row
    assert "dynamic_required_edge" in row
    assert row["wave_markers"] == ["P90"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run pytest tests/test_runtime_api.py::test_runtime_probabilities_adds_wave_signal_to_grid_rows tests/test_runtime_api.py::test_runtime_probabilities_adds_wave_signal_to_refresh_rows -q
```

Expected: FAIL because runtime rows do not include `wave_phase` or related fields.

- [ ] **Step 4: Apply the wave signal in runtime rows**

Modify `src/polymarket_engine/probability/runtime.py`:

```python
from polymarket_engine.probability.wave_signal import WaveSignalInput
from polymarket_engine.probability.wave_signal import classify_wave_signal
```

Add this helper near `_merge_grid_diagnostics`:

```python
def _apply_wave_signal(row: dict[str, Any], probability_input: ProbabilityInput) -> None:
    row.update(
        classify_wave_signal(
            WaveSignalInput(
                p_finish=_float(row["p_finish"], "p_finish"),
                p_no_touch=_float(row["p_no_touch"], "p_no_touch"),
                executable_price=probability_input.executable_price,
                edge_after_costs=_optional_runtime_float(
                    row.get("edge_after_costs"),
                    "edge_after_costs",
                ),
                required_edge=_optional_runtime_float(
                    row.get("required_edge"),
                    "required_edge",
                ),
                seconds_left=probability_input.seconds_left,
                source_age_ms=probability_input.source_age_ms,
                book_age_ms=probability_input.book_age_ms,
            )
        )
    )
```

Call `_apply_wave_signal(row, probability_input)` in both row creation paths:

```python
_merge_grid_diagnostics(
    row=row,
    diagnostics=hit.entry.diagnostics,
    preview_is_current=hit.entry.asof_ts == probability_input.asof_ts,
)
_apply_wave_signal(row, probability_input)
rows.append(row)
```

and:

```python
_merge_grid_diagnostics(
    row=row,
    diagnostics=entry.diagnostics,
    preview_is_current=True,
)
_apply_wave_signal(row, probability_input)
rows.append(row)
```

Also call it in `_runtime_row()` before returning so direct compute rows and any future in-memory row path keep the same output contract:

```python
row = {
    ...
}
_apply_wave_signal(row, probability_input)
return row
```

Do not change `_persisted_runtime_row()` in this task. The live probability endpoint is grid-first and refreshes on misses; persisted-output fallback is intentionally not part of the current runtime decision path.

- [ ] **Step 5: Run focused API tests**

Run:

```bash
uv run pytest tests/probability/test_wave_signal.py tests/test_runtime_api.py::test_runtime_probabilities_adds_wave_signal_to_grid_rows tests/test_runtime_api.py::test_runtime_probabilities_adds_wave_signal_to_refresh_rows -q
```

Expected: PASS.

- [ ] **Step 6: Commit runtime API wave fields**

```bash
git add src/polymarket_engine/probability/runtime.py tests/test_runtime_api.py
git commit -m "feat: expose wave signal in probability runtime"
```

---

### Task 3: Add Wave Signal to Rust TUI Probability Rows and Logs

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/status.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`

- [ ] **Step 1: Write failing serde parse assertions**

In `rust/crates/polymarket-cockpit-tui/src/status.rs`, extend the JSON payload in `probabilities_payload_parses_cached_rows()`:

```json
"wave_score": 0.87,
"wave_phase": "breaking",
"wave_reasons": ["EDGE_OK", "PRICE_90"],
"wave_markers": ["P90"],
"dynamic_edge": 0.045,
"dynamic_required_edge": 0.030,
```

Add these assertions:

```rust
assert_eq!(probabilities.rows[0].wave_score, Some(0.87));
assert_eq!(
    probabilities.rows[0].wave_phase.as_deref(),
    Some("breaking")
);
assert_eq!(
    probabilities.rows[0].wave_reasons,
    vec!["EDGE_OK", "PRICE_90"]
);
assert_eq!(probabilities.rows[0].wave_markers, vec!["P90"]);
assert_eq!(probabilities.rows[0].dynamic_edge, Some(0.045));
assert_eq!(probabilities.rows[0].dynamic_required_edge, Some(0.030));
```

- [ ] **Step 2: Run serde test to verify it fails**

Run:

```bash
cargo test --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui status::tests::probabilities_payload_parses_cached_rows -q
```

Expected: FAIL because `RuntimeProbabilityRow` does not have wave fields.

- [ ] **Step 3: Add wave fields to Rust status model**

In `rust/crates/polymarket-cockpit-tui/src/status.rs`, add fields to `RuntimeProbabilityRow` after `gate_reasons`:

```rust
#[serde(default)]
pub wave_score: Option<f64>,
#[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
pub wave_phase: Option<String>,
#[serde(default)]
pub wave_reasons: Vec<String>,
#[serde(default)]
pub wave_markers: Vec<String>,
#[serde(default)]
pub dynamic_edge: Option<f64>,
#[serde(default)]
pub dynamic_required_edge: Option<f64>,
```

Update every `RuntimeProbabilityRow` fixture in TUI tests with:

```rust
wave_score: None,
wave_phase: None,
wave_reasons: Vec::new(),
wave_markers: Vec::new(),
dynamic_edge: None,
dynamic_required_edge: None,
```

- [ ] **Step 4: Run serde test to verify it passes**

Run:

```bash
cargo test --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui status::tests::probabilities_payload_parses_cached_rows -q
```

Expected: PASS.

- [ ] **Step 5: Write failing TUI render test for wave column**

In `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`, update `ProbabilityDisplayRow`:

```rust
pub wave: String,
```

Update `probability_header_labels()` expected test to include `"wave"` between `"gate"` and `"diag"`.

Update `probability_rows_render_read_only_probability_outputs()` fixture:

```rust
wave_score: Some(0.87),
wave_phase: Some("breaking".to_string()),
wave_reasons: vec!["EDGE_OK".to_string(), "PRICE_90".to_string()],
wave_markers: vec!["P90".to_string()],
dynamic_edge: Some(0.045),
dynamic_required_edge: Some(0.030),
```

Add assertion:

```rust
assert_eq!(rows[0].wave, "breaking 0.87 P90");
```

- [ ] **Step 6: Run TUI render test to verify it fails**

Run:

```bash
cargo test --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui render::probability::tests::probability_rows_render_read_only_probability_outputs -q
```

Expected: FAIL because render code does not produce the `wave` column yet.

- [ ] **Step 7: Implement compact TUI wave column**

In `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`:

```rust
pub fn probability_header_labels() -> [&'static str; 10] {
    [
        "Contract",
        "p_finish",
        "p_no_touch",
        "edge/req",
        "gate",
        "wave",
        "diag",
        "weights",
        "cache",
        "Age/Flags",
    ]
}
```

Add:

```rust
fn wave_label(row: &RuntimeProbabilityRow) -> String {
    let Some(phase) = row.wave_phase.as_deref() else {
        return "-".to_string();
    };
    let score = row
        .wave_score
        .map(|value| format!(" {value:.2}"))
        .unwrap_or_default();
    let markers = if row.wave_markers.is_empty() {
        String::new()
    } else {
        format!(" {}", row.wave_markers.join("/"))
    };
    format!("{phase}{score}{markers}")
}
```

Add `wave: wave_label(row),` in `probability_row()`, and include `row.wave` in the table row vector after `row.gate`.

Adjust table constraints in `render()` so the extra column has bounded width:

```rust
Constraint::Length(18),
```

for the `wave` column.

- [ ] **Step 8: Write failing log summary test**

In `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`, add a test near the existing probability log tests:

```rust
#[test]
fn monte_carlo_log_line_includes_wave_summary() {
    let probabilities = probabilities_with_wave("breaking", 0.87, vec!["P90"]);

    let line = monte_carlo_log_line(&probabilities);

    assert!(line.contains("wave=breaking:1"));
    assert!(line.contains("markers=P90:1"));
}
```

Add helper in test module:

```rust
fn probabilities_with_wave(
    phase: &str,
    score: f64,
    markers: Vec<&str>,
) -> RuntimeProbabilities {
    let mut probabilities = probabilities();
    probabilities.rows[0].wave_phase = Some(phase.to_string());
    probabilities.rows[0].wave_score = Some(score);
    probabilities.rows[0].wave_markers = markers.into_iter().map(str::to_string).collect();
    probabilities
}
```

- [ ] **Step 9: Implement wave summary in TUI log line**

In `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`, update `monte_carlo_log_line()`:

```rust
fn monte_carlo_log_line(probabilities: &RuntimeProbabilities) -> String {
    format!(
        "mc rows={} {} gates={} wave={} markers={} cache={} at={}",
        probabilities.rows.len(),
        probability_side_summary(&probabilities.rows),
        probability_gate_summary(&probabilities.rows),
        probability_wave_summary(&probabilities.rows),
        probability_wave_marker_summary(&probabilities.rows),
        probability_cache_summary(&probabilities.rows),
        probabilities.generated_at
    )
}
```

Add:

```rust
fn probability_wave_summary(rows: &[RuntimeProbabilityRow]) -> String {
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    for row in rows {
        let phase = row
            .wave_phase
            .as_deref()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .unwrap_or("none")
            .to_string();
        *counts.entry(phase).or_default() += 1;
    }
    counts
        .into_iter()
        .map(|(phase, count)| format!("{phase}:{count}"))
        .collect::<Vec<_>>()
        .join(" ")
}

fn probability_wave_marker_summary(rows: &[RuntimeProbabilityRow]) -> String {
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    for row in rows {
        for marker in &row.wave_markers {
            *counts.entry(marker.clone()).or_default() += 1;
        }
    }
    if counts.is_empty() {
        return "-".to_string();
    }
    counts
        .into_iter()
        .map(|(marker, count)| format!("{marker}:{count}"))
        .collect::<Vec<_>>()
        .join(" ")
}
```

- [ ] **Step 10: Run TUI tests**

Run:

```bash
cargo test --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui -q
```

Expected: PASS.

- [ ] **Step 11: Commit TUI wave display**

```bash
git add rust/crates/polymarket-cockpit-tui/src/status.rs rust/crates/polymarket-cockpit-tui/src/render/probability.rs rust/crates/polymarket-cockpit-tui/src/event_loop.rs
git commit -m "feat: show wave signals in cockpit tui"
```

---

### Task 4: Add Browser Monitor Wave Markers

**Files:**
- Modify: `ui/src/App.tsx`
- Modify: `ui/src/styles.css`

- [ ] **Step 1: Add TypeScript wave fields**

In `ui/src/App.tsx`, extend `ProbabilityRow`:

```ts
  wave_score?: number | null;
  wave_phase?: string | null;
  wave_reasons?: string[];
  wave_markers?: string[];
  dynamic_edge?: number | null;
  dynamic_required_edge?: number | null;
```

- [ ] **Step 2: Add compact wave column and details**

In `ProbabilityTable`, add a header after `Gate`:

```tsx
<span>Wave</span>
```

Add a row cell after the gate cell:

```tsx
<span>
  <WaveBadge row={row} />
</span>
```

In `SelectedDetails`, add metric cards in `hero-metrics`:

```tsx
<Metric label="wave" value={formatWave(row)} />
<Metric label="dynamic edge" value={formatDynamicEdge(row)} />
```

In `GateAndWeights`, add wave reasons to diagnosis chips before gate reasons:

```tsx
...unknownList(row.wave_reasons),
...unknownList(row.wave_markers),
```

Add helper functions near `GatePill`:

```tsx
function WaveBadge({ row }: { row: ProbabilityRow }) {
  const phase = cleanString(row.wave_phase) ?? "none";
  const score = isFiniteNumber(row.wave_score) ? row.wave_score.toFixed(2) : "--";
  const markers = unknownList(row.wave_markers).join("/");
  return (
    <span className={`wave-badge wave-${phaseTone(phase)}`}>
      <span>{phase}</span>
      <strong>{score}</strong>
      {markers ? <small>{markers}</small> : null}
    </span>
  );
}

function formatWave(row: ProbabilityRow) {
  const phase = cleanString(row.wave_phase) ?? "-";
  const score = isFiniteNumber(row.wave_score) ? row.wave_score.toFixed(2) : "--";
  const markers = unknownList(row.wave_markers).join("/");
  return markers ? `${phase} ${score} ${markers}` : `${phase} ${score}`;
}

function formatDynamicEdge(row: ProbabilityRow) {
  if (!isFiniteNumber(row.dynamic_edge) || !isFiniteNumber(row.dynamic_required_edge)) {
    return "-";
  }
  return `${row.dynamic_edge.toFixed(3)} / ${row.dynamic_required_edge.toFixed(3)}`;
}

function phaseTone(value: string) {
  const normalized = value.toLowerCase();
  if (normalized === "breaking") return "breaking";
  if (normalized === "late") return "late";
  if (normalized === "missed") return "missed";
  if (normalized === "forming") return "forming";
  return "none";
}
```

- [ ] **Step 3: Add CSS for stable compact wave badges**

In `ui/src/styles.css`, add:

```css
.wave-badge {
  display: inline-grid;
  grid-template-columns: auto auto;
  align-items: center;
  gap: 2px 6px;
  min-width: 92px;
  max-width: 150px;
  border: 1px solid #3a4248;
  border-radius: 6px;
  padding: 5px 7px;
  color: #d8dedb;
  background: #20252a;
  font-size: 11px;
  font-weight: 780;
  line-height: 1.1;
}

.wave-badge strong {
  color: #f7f8f4;
  font-size: 11px;
}

.wave-badge small {
  grid-column: 1 / -1;
  overflow: hidden;
  color: #aab3b0;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wave-forming {
  border-color: rgba(86, 189, 143, 0.55);
  background: rgba(86, 189, 143, 0.10);
}

.wave-breaking {
  border-color: rgba(211, 184, 90, 0.62);
  background: rgba(211, 184, 90, 0.12);
}

.wave-late {
  border-color: rgba(223, 143, 76, 0.62);
  background: rgba(223, 143, 76, 0.13);
}

.wave-missed {
  border-color: rgba(213, 91, 91, 0.62);
  background: rgba(213, 91, 91, 0.13);
}
```

Adjust `.probability-row` grid columns from six columns to seven columns. Use this shape:

```css
grid-template-columns:
  minmax(190px, 1.25fr)
  minmax(72px, 0.45fr)
  minmax(82px, 0.45fr)
  minmax(88px, 0.45fr)
  minmax(100px, 0.55fr)
  minmax(94px, 0.55fr)
  minmax(64px, 0.35fr);
```

- [ ] **Step 4: Run browser UI typecheck and build**

Run:

```bash
npm --prefix ui exec -- tsc --noEmit -p ui/tsconfig.json
npm --prefix ui run build
```

Expected: PASS.

- [ ] **Step 5: Commit browser UI wave display**

```bash
git add ui/src/App.tsx ui/src/styles.css
git commit -m "feat: show wave signals in runtime monitor"
```

---

### Task 5: Verification and Deploy Readiness

**Files:**
- No new source files unless verification exposes a real defect.

- [ ] **Step 1: Run focused Python tests**

```bash
uv run pytest tests/probability/test_wave_signal.py tests/test_runtime_api.py -q
```

Expected: PASS.

- [ ] **Step 2: Run Rust TUI tests**

```bash
cargo fmt --all -- --check
cargo test --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui -q
```

Expected: PASS.

- [ ] **Step 3: Run browser UI checks**

```bash
npm --prefix ui exec -- tsc --noEmit -p ui/tsconfig.json
npm --prefix ui run build
```

Expected: PASS.

- [ ] **Step 4: Run repo lint on touched Python**

```bash
uv run ruff check src/polymarket_engine/probability/wave_signal.py src/polymarket_engine/probability/runtime.py tests/probability/test_wave_signal.py tests/test_runtime_api.py
```

Expected: PASS.

- [ ] **Step 5: Confirm deploy blocker state**

```bash
git status --short
```

Expected: only the known pre-existing untracked handoff files remain if they have not been moved/committed:

```text
?? docs/superpowers/hand_off/
?? scripts/handoff_dynamic_mc_generator_weights.sh
```

If THEPC deployment is needed, do not use `scripts/deploy_pc.sh` until the worktree is clean or a clean temporary worktree is created from the final commit.

- [ ] **Step 6: Final implementation commit if any verification fixes were needed**

Only if Step 1-4 required small follow-up fixes:

```bash
git add <fixed-files>
git commit -m "fix: harden wave signal display"
```

## Risks

- **False confidence risk:** `wave_score` is a heuristic in this plan, not a trained predictor. The UI must treat it as paper-only.
- **Entry-price risk:** `executable_price` is top-of-book style input today, not full target-size VWAP. The dynamic edge gate is still better than fixed price cutoffs, but later paper execution should use depth-aware VWAP.
- **Cache staleness risk:** Cached `p_finish` can be reused while executable price changes. This plan deliberately recomputes dynamic edge from current as-of executable price and cached probability, but high-speed terminal waves may still require shorter grid validity later.
- **UI density risk:** Adding a wave column can crowd the TUI/browser table. Keep labels compact and stable-width.
- **Deployment risk:** The dynamic worktree currently has untracked handoff files, so direct THEPC deploy scripts will refuse to run until a clean worktree is used.

## Suggested Subagent Delegation

- **Task 1 + Task 2:** Python probability runtime subagent. Owns wave module and API rows.
- **Task 3:** Rust TUI subagent. Owns serde model, probability table, and log summary.
- **Task 4:** Browser UI subagent. Owns React/types/styles and UI build.
- **Task 5:** Main agent. Runs verification, reviews diffs, and handles deploy-readiness decisions.

## Plan Self-Review

- Spec coverage: dynamic edge gate is implemented by `classify_wave_signal`; visible `0.90`, `0.95`, and `0.96` markers are represented by `P90`, `P95`, and `TICK96`; API/TUI/browser/log display each has a task; read-only/paper-only scope is explicit.
- Placeholder scan: no `TBD`, `TODO`, or open-ended “add tests” steps remain.
- Type consistency: Python fields use `wave_score`, `wave_phase`, `wave_reasons`, `wave_markers`, `dynamic_edge`, and `dynamic_required_edge`; Rust and TypeScript use the same names.
