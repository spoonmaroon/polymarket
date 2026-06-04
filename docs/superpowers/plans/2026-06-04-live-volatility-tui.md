# Live Volatility TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show live `sigma_tau`, computed realized-vol windows, and volatility regime in the TUI without requiring runtime Monte Carlo probabilities to be enabled.

**Architecture:** Add a read-only `volatility` section to `/api/runtime/live`, sourced from latest `features.asof_state_inputs` rows. Extend the Rust TUI status model to parse that section, then render it in Systems and as the fallback Probability tab content when MC probability rows are empty.

**Tech Stack:** Python FastAPI runtime API, DuckDB, pytest, Rust Ratatui TUI, serde, cargo tests.

---

## File Structure

- Modify `src/polymarket_engine/runtime_api.py`
  - Add `_live_volatility_payload()` and include it in `_runtime_live_payload()`.
  - Query latest as-of rows per asset from DuckDB using read-only retry.
- Modify `tests/test_runtime_api.py`
  - Add a failing test that `/api/runtime/live` includes BTC/ETH sigma, short/medium/long vols, regime, age, and flags.
- Modify `rust/crates/polymarket-cockpit-tui/src/status.rs`
  - Add `RuntimeVolatility` and `RuntimeVolatilityRow`.
  - Add `volatility` to `RuntimeLive`.
- Modify `rust/crates/polymarket-cockpit-tui/src/render/systems.rs`
  - Show compact live volatility lines.
- Modify `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`
  - When probability rows are empty, render volatility diagnostics instead of only `probability pending`.
- Modify `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
  - Preserve volatility state from SSE/live updates.

## Task 1: Runtime API Live Volatility Payload

**Files:**
- Modify: `tests/test_runtime_api.py`
- Modify: `src/polymarket_engine/runtime_api.py`

- [ ] **Step 1: Write the failing Python API test**

Add a test that builds two latest as-of rows and asserts `/api/runtime/live` includes:

```python
assert payload["volatility"]["state"] == "OK"
assert payload["volatility"]["rows"][0]["asset"] == "BTC"
assert payload["volatility"]["rows"][0]["sigma_tau"] == pytest.approx(0.0012)
assert payload["volatility"]["rows"][0]["short_realized_vol"] == pytest.approx(0.0001)
assert payload["volatility"]["rows"][0]["medium_realized_vol"] == pytest.approx(0.0002)
assert payload["volatility"]["rows"][0]["long_realized_vol"] == pytest.approx(0.0003)
assert payload["volatility"]["rows"][0]["volatility_regime"] == "normal"
assert payload["volatility"]["rows"][0]["flags"] == ["OK"]
```

- [ ] **Step 2: Verify the test fails**

Run:

```bash
uv run pytest tests/test_runtime_api.py::test_runtime_live_includes_volatility_diagnostics -q
```

Expected: failure because `volatility` is missing from the live payload.

- [ ] **Step 3: Implement `_live_volatility_payload()`**

Add a read-only query against `features.asof_state_inputs`, partitioned by asset and ordered by newest `asof_ts`.

Return shape:

```json
{
  "state": "OK",
  "rows": [
    {
      "asset": "BTC",
      "asof_ts": "2026-06-04T00:00:00+00:00",
      "sigma_tau": 0.0012,
      "short_realized_vol": 0.0001,
      "medium_realized_vol": 0.0002,
      "long_realized_vol": 0.0003,
      "volatility_regime": "normal",
      "age_ms": 120,
      "flags": ["OK"]
    }
  ],
  "errors": []
}
```

- [ ] **Step 4: Verify Python API test passes**

Run:

```bash
uv run pytest tests/test_runtime_api.py::test_runtime_live_includes_volatility_diagnostics -q
```

Expected: pass.

## Task 2: Rust Runtime Model Parses Volatility

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/status.rs`

- [ ] **Step 1: Write the failing Rust parser test**

Extend `live_payload_parses_combined_runtime_shape` or add a new test:

```rust
assert_eq!(live.volatility.rows[0].asset, "BTC");
assert_eq!(live.volatility.rows[0].sigma_tau, Some(0.0012));
assert_eq!(live.volatility.rows[0].volatility_regime.as_deref(), Some("normal"));
```

- [ ] **Step 2: Verify the test fails**

Run:

```bash
cargo test -p polymarket-cockpit-tui live_payload_parses_volatility_diagnostics
```

Expected: failure because `RuntimeLive` has no `volatility` field.

- [ ] **Step 3: Add Rust structs**

Add:

```rust
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
pub struct RuntimeVolatility {
    #[serde(default)]
    pub state: String,
    #[serde(default)]
    pub rows: Vec<RuntimeVolatilityRow>,
    #[serde(default)]
    pub errors: Vec<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeVolatilityRow {
    pub asset: String,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub asof_ts: Option<String>,
    pub sigma_tau: Option<f64>,
    pub short_realized_vol: Option<f64>,
    pub medium_realized_vol: Option<f64>,
    pub long_realized_vol: Option<f64>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub volatility_regime: Option<String>,
    pub age_ms: Option<u64>,
    #[serde(default)]
    pub flags: Vec<String>,
}
```

- [ ] **Step 4: Verify Rust parser test passes**

Run:

```bash
cargo test -p polymarket-cockpit-tui live_payload_parses_volatility_diagnostics
```

Expected: pass.

## Task 3: Render Volatility In Systems And Probability

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/state.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/systems.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`

- [ ] **Step 1: Write failing render tests**

Expected strings:

```rust
assert!(text.contains("BTC sigma=0.00120"));
assert!(text.contains("regime=normal"));
assert!(rows[0].contract.contains("BTC"));
assert!(rows[0].sigma_tau == "0.00120");
```

- [ ] **Step 2: Verify render tests fail**

Run:

```bash
cargo test -p polymarket-cockpit-tui volatility
```

Expected: failure because the app state and renderers do not retain or display volatility.

- [ ] **Step 3: Store volatility in app state and live updates**

Add `runtime_volatility: Option<RuntimeVolatility>` to `AppState`, add it to `RuntimeUpdate`, set it from `RuntimeLive`, and drain it like status/gates/monitor.

- [ ] **Step 4: Render compact Systems lines**

Format:

```text
BTC sigma=0.00120 short=0.00010 med=0.00020 long=0.00030 regime=normal
```

- [ ] **Step 5: Render Probability fallback diagnostics**

When `runtime_probabilities.rows` is empty, show volatility rows with:

```text
Asset | sigma_tau | short | medium | long | regime | Age/Flags
```

- [ ] **Step 6: Verify Rust render tests pass**

Run:

```bash
cargo test -p polymarket-cockpit-tui volatility
```

Expected: pass.

## Task 4: Focused Verification

- [ ] Run Python focused tests:

```bash
uv run pytest tests/test_runtime_api.py::test_runtime_live_includes_volatility_diagnostics -q
```

- [ ] Run Rust TUI tests:

```bash
cargo test -p polymarket-cockpit-tui
```

- [ ] Run Ruff on changed Python file:

```bash
uv run ruff check src/polymarket_engine/runtime_api.py tests/test_runtime_api.py
```

- [ ] Smoke check local live payload if API is running:

```bash
curl -fsS "http://127.0.0.1:8000/api/runtime/live?limit=8" | python3 -m json.tool | rg "volatility|sigma_tau|short_realized_vol|volatility_regime"
```

## Self-Review

- [ ] Volatility comes from latest as-of state only.
- [ ] No MC computation is triggered by the TUI.
- [ ] No wallet, trade, signing, or order-placement code is added.
- [ ] Missing volatility renders as missing/stale instead of fake zeros.
- [ ] Systems panel remains compact.
- [ ] Probability tab remains read-only.

