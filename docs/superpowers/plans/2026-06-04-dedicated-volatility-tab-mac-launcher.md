# Dedicated Volatility Tab And Mac Launcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dedicated read-only Rust TUI Volatility tab between Probability and Outcomes, keep Probability limited to probability outputs, update both Mac desktop icons to call the canonical launcher, and build/verify the Mac TUI locally.

**Architecture:** The runtime API already sends live volatility under `/api/runtime/live.volatility`; this change keeps that contract and moves the Rust TUI display into a dedicated renderer. The Rust status model will parse optional volatility metadata (`generated_at`, `source_key`, `lookback_limit`) so the tab can show source/lookback when available. Both desktop launchers will delegate to `scripts/open_tui_mac.sh`, which owns API URL, stale-binary detection, release build, and launch behavior.

**Tech Stack:** Rust, Ratatui, serde, cargo, Python pytest, zsh.

---

## Context

This plan supersedes `docs/superpowers/plans/2026-06-04-live-volatility-tui.md` for the UI layer. That earlier plan intentionally rendered volatility diagnostics as a Probability fallback. The current requirement reverses that: Probability must stay focused on probability outputs, and volatility gets its own tab.

Current facts to preserve:

- `/api/runtime/live` already includes a `volatility` object from `src/polymarket_engine/runtime_api.py`.
- The file-backed volatility payload includes `generated_at`, `source_key`, `lookback_limit`, `rows`, and `errors`.
- `rust/crates/polymarket-cockpit-tui/src/state.rs` currently has tabs `Live`, `Systems`, `Market`, `Probability`, `Outcomes`, `Logs`.
- `rust/crates/polymarket-cockpit-tui/src/render/probability.rs` currently renders volatility rows when probability rows are empty. Remove that fallback.
- `/Users/goon/Desktop/Polymarket TUI.command` already delegates to `/Users/goon/polymarket/scripts/open_tui_mac.sh`.
- `/Users/goon/Desktop/Desktop - spoon/Polymarket TUI.command` is stale and contains a copied old launcher script.
- The workspace may be dirty from the separate docs-only order-flow edit in `docs/BINARY_CONTRACT_ENGINE_PLAN.md`; do not stage or revert that file while executing this plan.

## File Structure

- Modify `rust/crates/polymarket-cockpit-tui/src/state.rs`
  - Add `MainTab::Volatility`.
  - Insert it in `MainTab::all()` between `Probability` and `Outcomes`.
  - Add the `"Volatility"` label.
  - Update the tab-order test.

- Modify `rust/crates/polymarket-cockpit-tui/src/status.rs`
  - Add optional metadata fields to `RuntimeVolatility`: `generated_at`, `source_key`, `lookback_limit`.
  - Extend the live payload parser test to prove those fields are retained.

- Create `rust/crates/polymarket-cockpit-tui/src/render/volatility.rs`
  - Build a small table model from `AppState.runtime_volatility`.
  - Render BTC/ETH rows with `sigma_tau`, `short`, `medium`, `long`, `regime`, `Source`, `Lookback`, and `Age/Flags`.
  - Render a pending/error row when volatility has no rows.

- Modify `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`
  - Remove volatility imports and fallback table behavior.
  - Keep the empty state as `probability pending`.
  - Remove the stale 7-column width branch used only by the volatility fallback.

- Modify `rust/crates/polymarket-cockpit-tui/src/render/mod.rs`
  - Add `pub mod volatility;`.
  - Add a `MainTab::Volatility` match arm that renders `volatility::render()` in the primary panel and `systems::render()` in the secondary panel.

- Modify `tests/scripts/test_mac_tui_launcher.py`
  - Add a local Mac test proving both desktop launchers delegate to the canonical script.
  - Guard the test with a skip if the desktop launchers are absent, so non-Enoch machines do not fail.

- Modify `/Users/goon/Desktop/Desktop - spoon/Polymarket TUI.command`
  - Replace the stale copied launcher with the canonical two-line wrapper.

## Risk Areas

- The header tabs are generated from `MainTab::all()`, so adding a variant changes tab navigation order. The state test must lock the intended order.
- Probability currently has live volatility behavior hidden inside it. Removing the fallback changes visible behavior by design; the replacement Volatility tab test must prove the rows still render somewhere.
- `RuntimeVolatility` already derives `Default`, so new fields must use `#[serde(default)]` to keep older API payloads parseable.
- Desktop files live outside the repo. The test should verify local state without making the repo unusable on machines that do not have those paths.

## Subagent Delegation

- Subagent 1: Rust tab state and status model (`state.rs`, `status.rs`).
- Subagent 2: Rust render split (`render/volatility.rs`, `render/probability.rs`, `render/mod.rs`).
- Subagent 3: Mac launcher test and desktop wrapper update (`tests/scripts/test_mac_tui_launcher.py`, `/Users/goon/Desktop/Desktop - spoon/Polymarket TUI.command`).

Run the subagents in that order. Subagent 2 depends on the `RuntimeVolatility` metadata fields from Subagent 1.

## Task 1: Add The Volatility Tab To Rust TUI State

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/state.rs`

- [ ] **Step 1: Update the failing tab-order test**

In `rust/crates/polymarket-cockpit-tui/src/state.rs`, change `cockpit_tabs_are_operator_surfaces()` to:

```rust
#[test]
fn cockpit_tabs_are_operator_surfaces() {
    let labels: Vec<&'static str> = MainTab::all().iter().map(MainTab::label).collect();

    assert_eq!(
        labels,
        vec![
            "Live",
            "Systems",
            "Market",
            "Probability",
            "Volatility",
            "Outcomes",
            "Logs"
        ]
    );
}
```

- [ ] **Step 2: Run the focused Rust test and verify it fails**

Run:

```bash
cd /Users/goon/polymarket/rust && cargo test -p polymarket-cockpit-tui cockpit_tabs_are_operator_surfaces -q
```

Expected: fail because `MainTab::all()` does not include `"Volatility"`.

- [ ] **Step 3: Add `MainTab::Volatility`**

In `rust/crates/polymarket-cockpit-tui/src/state.rs`, update `MainTab` and its impl to:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MainTab {
    Live,
    Systems,
    Market,
    Probability,
    Volatility,
    Outcomes,
    Logs,
}

impl MainTab {
    pub fn all() -> &'static [MainTab] {
        &[
            MainTab::Live,
            MainTab::Systems,
            MainTab::Market,
            MainTab::Probability,
            MainTab::Volatility,
            MainTab::Outcomes,
            MainTab::Logs,
        ]
    }

    pub fn label(&self) -> &'static str {
        match self {
            MainTab::Live => "Live",
            MainTab::Systems => "Systems",
            MainTab::Market => "Market",
            MainTab::Probability => "Probability",
            MainTab::Volatility => "Volatility",
            MainTab::Outcomes => "Outcomes",
            MainTab::Logs => "Logs",
        }
    }
}
```

- [ ] **Step 4: Re-run the focused Rust test**

Run:

```bash
cd /Users/goon/polymarket/rust && cargo test -p polymarket-cockpit-tui cockpit_tabs_are_operator_surfaces -q
```

Expected: pass.

- [ ] **Step 5: Commit Task 1**

Run:

```bash
cd /Users/goon/polymarket && git add rust/crates/polymarket-cockpit-tui/src/state.rs && git commit -m "feat: add volatility tui tab"
```

Expected: commit succeeds. If `docs/BINARY_CONTRACT_ENGINE_PLAN.md` is dirty, leave it unstaged.

## Task 2: Parse Volatility Source And Lookback Metadata

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/status.rs`

- [ ] **Step 1: Update the failing live payload parser test**

In `live_payload_parses_combined_runtime_shape()`, update the `volatility` JSON object to include:

```json
"generated_at": "2026-06-03T21:00:00+00:00",
"source_key": "polymarket_rtds_chainlink",
"lookback_limit": 180,
```

The full `volatility` JSON block should be:

```json
"volatility": {
    "state": "OK",
    "generated_at": "2026-06-03T21:00:00+00:00",
    "source_key": "polymarket_rtds_chainlink",
    "lookback_limit": 180,
    "rows": [{
        "asset": "BTC",
        "asof_ts": "2026-06-03T21:00:00+00:00",
        "sigma_tau": 0.0012,
        "short_realized_vol": 0.0001,
        "medium_realized_vol": 0.0002,
        "long_realized_vol": 0.0003,
        "volatility_regime": "normal",
        "age_ms": 120,
        "flags": ["OK"]
    }],
    "errors": []
}
```

Add these assertions after the existing volatility assertions:

```rust
assert_eq!(
    live.volatility.generated_at.as_deref(),
    Some("2026-06-03T21:00:00+00:00")
);
assert_eq!(
    live.volatility.source_key.as_deref(),
    Some("polymarket_rtds_chainlink")
);
assert_eq!(live.volatility.lookback_limit, Some(180));
```

- [ ] **Step 2: Run the focused Rust test and verify it fails**

Run:

```bash
cd /Users/goon/polymarket/rust && cargo test -p polymarket-cockpit-tui live_payload_parses_combined_runtime_shape -q
```

Expected: compile failure because `RuntimeVolatility` has no `generated_at`, `source_key`, or `lookback_limit` fields.

- [ ] **Step 3: Add optional metadata fields to `RuntimeVolatility`**

In `rust/crates/polymarket-cockpit-tui/src/status.rs`, change `RuntimeVolatility` to:

```rust
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq)]
pub struct RuntimeVolatility {
    #[serde(default)]
    pub state: String,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub generated_at: Option<String>,
    #[serde(default, deserialize_with = "deserialize_optional_scalar_string")]
    pub source_key: Option<String>,
    #[serde(default)]
    pub lookback_limit: Option<u64>,
    #[serde(default)]
    pub rows: Vec<RuntimeVolatilityRow>,
    #[serde(default)]
    pub errors: Vec<String>,
}
```

- [ ] **Step 4: Update existing `RuntimeVolatility` test fixtures**

Every direct `RuntimeVolatility { ... }` test fixture must include struct update syntax so future optional fields stay harmless:

```rust
RuntimeVolatility {
    state: "OK".to_string(),
    rows: vec![RuntimeVolatilityRow {
        asset: "BTC".to_string(),
        asof_ts: Some("2026-06-03T21:00:00+00:00".to_string()),
        sigma_tau: Some(0.0012),
        short_realized_vol: Some(0.0001),
        medium_realized_vol: Some(0.0002),
        long_realized_vol: Some(0.0003),
        volatility_regime: Some("normal".to_string()),
        age_ms: Some(120),
        flags: vec!["OK".to_string()],
    }],
    errors: vec![],
    ..RuntimeVolatility::default()
}
```

- [ ] **Step 5: Re-run the focused Rust test**

Run:

```bash
cd /Users/goon/polymarket/rust && cargo test -p polymarket-cockpit-tui live_payload_parses_combined_runtime_shape -q
```

Expected: pass.

- [ ] **Step 6: Commit Task 2**

Run:

```bash
cd /Users/goon/polymarket && git add rust/crates/polymarket-cockpit-tui/src/status.rs rust/crates/polymarket-cockpit-tui/src/render/probability.rs rust/crates/polymarket-cockpit-tui/src/render/systems.rs && git commit -m "feat: parse volatility metadata in tui"
```

Expected: commit succeeds. If `render/probability.rs` or `render/systems.rs` did not need fixture updates in this task, leave them out of `git add`.

## Task 3: Create The Dedicated Volatility Renderer

**Files:**
- Create: `rust/crates/polymarket-cockpit-tui/src/render/volatility.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/mod.rs`

- [ ] **Step 1: Create the failing renderer test and table API**

Create `rust/crates/polymarket-cockpit-tui/src/render/volatility.rs` with this initial test module and public API signatures:

```rust
use ratatui::{
    Frame,
    layout::{Constraint, Rect},
    style::{Color, Style},
    widgets::{Block, Cell, Row, Table},
};

use crate::{
    state::AppState,
    status::{RuntimeVolatility, RuntimeVolatilityRow},
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VolatilityTableModel {
    pub headers: Vec<&'static str>,
    pub rows: Vec<Vec<String>>,
}

pub fn volatility_header_labels() -> [&'static str; 9] {
    [
        "Asset",
        "sigma_tau",
        "short",
        "medium",
        "long",
        "regime",
        "Source",
        "Lookback",
        "Age/Flags",
    ]
}

pub fn volatility_table(_app: &AppState) -> VolatilityTableModel {
    VolatilityTableModel {
        headers: volatility_header_labels().to_vec(),
        rows: vec![],
    }
}

pub fn render(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let model = volatility_table(app);
    let rows = model
        .rows
        .into_iter()
        .map(|row| Row::new(row.into_iter().map(Cell::from).collect::<Vec<_>>()))
        .collect::<Vec<_>>();
    let table = Table::new(
        rows,
        vec![
            Constraint::Length(6),
            Constraint::Length(10),
            Constraint::Length(9),
            Constraint::Length(9),
            Constraint::Length(9),
            Constraint::Length(12),
            Constraint::Length(26),
            Constraint::Length(9),
            Constraint::Min(12),
        ],
    )
    .header(Row::new(model.headers).style(Style::default().fg(Color::Cyan)))
    .block(Block::bordered().title("Volatility"));

    frame.render_widget(table, area);
}

#[cfg(test)]
mod tests {
    use crate::{
        state::AppState,
        status::{RuntimeVolatility, RuntimeVolatilityRow},
    };

    use super::{volatility_header_labels, volatility_table};

    #[test]
    fn volatility_table_renders_live_source_lookback_and_rows() {
        let app = AppState {
            runtime_volatility: Some(RuntimeVolatility {
                state: "OK".to_string(),
                generated_at: Some("2026-06-04T01:00:00+00:00".to_string()),
                source_key: Some("polymarket_rtds_chainlink".to_string()),
                lookback_limit: Some(180),
                rows: vec![
                    RuntimeVolatilityRow {
                        asset: "BTC".to_string(),
                        asof_ts: Some("2026-06-04T01:00:00+00:00".to_string()),
                        sigma_tau: Some(0.001234),
                        short_realized_vol: Some(0.000101),
                        medium_realized_vol: Some(0.000202),
                        long_realized_vol: Some(0.000303),
                        volatility_regime: Some("normal".to_string()),
                        age_ms: Some(120),
                        flags: vec!["OK".to_string()],
                    },
                    RuntimeVolatilityRow {
                        asset: "ETH".to_string(),
                        asof_ts: Some("2026-06-04T01:00:00+00:00".to_string()),
                        sigma_tau: None,
                        short_realized_vol: None,
                        medium_realized_vol: Some(0.000404),
                        long_realized_vol: Some(0.000505),
                        volatility_regime: Some("stale_reference_source".to_string()),
                        age_ms: Some(2200),
                        flags: vec!["missing_volatility".to_string()],
                    },
                ],
                errors: vec![],
            }),
            ..Default::default()
        };

        let table = volatility_table(&app);

        assert_eq!(table.headers, volatility_header_labels().to_vec());
        assert_eq!(
            table.rows[0],
            vec![
                "BTC".to_string(),
                "0.00123".to_string(),
                "0.00010".to_string(),
                "0.00020".to_string(),
                "0.00030".to_string(),
                "normal".to_string(),
                "polymarket_rtds_chainlink".to_string(),
                "180".to_string(),
                "120ms OK".to_string(),
            ]
        );
        assert_eq!(
            table.rows[1],
            vec![
                "ETH".to_string(),
                "-".to_string(),
                "-".to_string(),
                "0.00040".to_string(),
                "0.00051".to_string(),
                "stale_reference_source".to_string(),
                "polymarket_rtds_chainlink".to_string(),
                "180".to_string(),
                "2200ms missing_volatility".to_string(),
            ]
        );
    }
}
```

- [ ] **Step 2: Export the module and verify the renderer test fails**

Add this line to `rust/crates/polymarket-cockpit-tui/src/render/mod.rs`:

```rust
pub mod volatility;
```

Run:

```bash
cd /Users/goon/polymarket/rust && cargo test -p polymarket-cockpit-tui volatility_table_renders_live_source_lookback_and_rows -q
```

Expected: fail because `volatility_table()` returns no rows.

- [ ] **Step 3: Implement volatility table formatting**

Replace the stub `volatility_table()` in `rust/crates/polymarket-cockpit-tui/src/render/volatility.rs` with:

```rust
pub fn volatility_table(app: &AppState) -> VolatilityTableModel {
    let Some(volatility) = app.runtime_volatility.as_ref() else {
        return VolatilityTableModel {
            headers: volatility_header_labels().to_vec(),
            rows: vec![vec![
                "volatility pending".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
            ]],
        };
    };

    if volatility.rows.is_empty() {
        let status = if volatility.errors.is_empty() {
            volatility.state.clone()
        } else {
            volatility.errors.join(",")
        };
        return VolatilityTableModel {
            headers: volatility_header_labels().to_vec(),
            rows: vec![vec![
                "volatility pending".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                status,
                format_source(&volatility.source_key),
                format_lookback(volatility.lookback_limit),
                "-".to_string(),
            ]],
        };
    }

    VolatilityTableModel {
        headers: volatility_header_labels().to_vec(),
        rows: volatility
            .rows
            .iter()
            .map(|row| volatility_row(volatility, row))
            .collect(),
    }
}

fn volatility_row(volatility: &RuntimeVolatility, row: &RuntimeVolatilityRow) -> Vec<String> {
    vec![
        row.asset.clone(),
        format_optional_vol(row.sigma_tau),
        format_optional_vol(row.short_realized_vol),
        format_optional_vol(row.medium_realized_vol),
        format_optional_vol(row.long_realized_vol),
        row.volatility_regime
            .clone()
            .unwrap_or_else(|| "-".to_string()),
        format_source(&volatility.source_key),
        format_lookback(volatility.lookback_limit),
        volatility_age_flags(row),
    ]
}

fn format_optional_vol(value: Option<f64>) -> String {
    value.map_or_else(|| "-".to_string(), |value| format!("{value:.5}"))
}

fn format_source(value: &Option<String>) -> String {
    value
        .as_deref()
        .filter(|value| !value.is_empty())
        .unwrap_or("-")
        .to_string()
}

fn format_lookback(value: Option<u64>) -> String {
    value.map_or_else(|| "-".to_string(), |value| value.to_string())
}

fn volatility_age_flags(row: &RuntimeVolatilityRow) -> String {
    let flags = if row.flags.is_empty() {
        "OK".to_string()
    } else {
        row.flags.join(",")
    };
    match row.age_ms {
        Some(age_ms) => format!("{age_ms}ms {flags}"),
        None => format!("- {flags}"),
    }
}
```

- [ ] **Step 4: Route the Volatility tab in the main renderer**

In `rust/crates/polymarket-cockpit-tui/src/render/mod.rs`, add this match arm between `MainTab::Probability` and `MainTab::Outcomes`:

```rust
MainTab::Volatility => {
    volatility::render(frame, body.primary, app);
    systems::render(frame, body.secondary, app);
}
```

- [ ] **Step 5: Re-run the focused renderer test**

Run:

```bash
cd /Users/goon/polymarket/rust && cargo test -p polymarket-cockpit-tui volatility_table_renders_live_source_lookback_and_rows -q
```

Expected: pass.

- [ ] **Step 6: Commit Task 3**

Run:

```bash
cd /Users/goon/polymarket && git add rust/crates/polymarket-cockpit-tui/src/render/mod.rs rust/crates/polymarket-cockpit-tui/src/render/volatility.rs && git commit -m "feat: render dedicated volatility tui tab"
```

Expected: commit succeeds.

## Task 4: Remove Volatility From Probability

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`

- [ ] **Step 1: Replace the stale fallback test**

In `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`, replace `probability_table_renders_volatility_when_probabilities_are_empty()` with:

```rust
#[test]
fn probability_table_stays_pending_when_probabilities_are_empty_even_with_volatility() {
    let app = AppState {
        runtime_volatility: Some(RuntimeVolatility {
            state: "OK".to_string(),
            rows: vec![RuntimeVolatilityRow {
                asset: "BTC".to_string(),
                asof_ts: Some("2026-06-03T21:00:00+00:00".to_string()),
                sigma_tau: Some(0.0012),
                short_realized_vol: Some(0.0001),
                medium_realized_vol: Some(0.0002),
                long_realized_vol: Some(0.0003),
                volatility_regime: Some("normal".to_string()),
                age_ms: Some(120),
                flags: vec!["OK".to_string()],
            }],
            errors: vec![],
            ..RuntimeVolatility::default()
        }),
        ..Default::default()
    };

    let table = probability_table(&app);

    assert_eq!(table.headers, probability_header_labels().to_vec());
    assert_eq!(
        table.rows[0],
        vec![
            "probability pending".to_string(),
            "-".to_string(),
            "-".to_string(),
            "-".to_string(),
            "-".to_string(),
            "-".to_string(),
        ]
    );
}
```

- [ ] **Step 2: Run the focused Probability test and verify it fails**

Run:

```bash
cd /Users/goon/polymarket/rust && cargo test -p polymarket-cockpit-tui probability_table_stays_pending_when_probabilities_are_empty_even_with_volatility -q
```

Expected: fail because `probability_table()` still returns volatility headers and rows.

- [ ] **Step 3: Remove the Probability volatility fallback**

In `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`, change the imports from:

```rust
use crate::{
    state::AppState,
    status::{RuntimeProbabilityRow, RuntimeVolatilityRow},
};
```

to:

```rust
use crate::{state::AppState, status::RuntimeProbabilityRow};
```

Then replace `probability_table()` with:

```rust
pub fn probability_table(app: &AppState) -> ProbabilityTableModel {
    let probability_rows = probability_rows(app);
    if !probability_rows.is_empty() {
        return ProbabilityTableModel {
            headers: probability_header_labels().to_vec(),
            rows: probability_rows
                .into_iter()
                .map(|row| {
                    vec![
                        row.contract,
                        row.p_finish,
                        row.p_no_touch,
                        row.z_path,
                        row.sigma_tau,
                        row.age_flags,
                    ]
                })
                .collect(),
        };
    }

    ProbabilityTableModel {
        headers: probability_header_labels().to_vec(),
        rows: vec![vec![
            "probability pending".to_string(),
            "-".to_string(),
            "-".to_string(),
            "-".to_string(),
            "-".to_string(),
            "-".to_string(),
        ]],
    }
}
```

Delete these now-unused helper functions from the same file:

```rust
fn volatility_probability_row(row: &RuntimeVolatilityRow) -> Vec<String> {
    vec![
        row.asset.clone(),
        format_optional_vol(row.sigma_tau),
        format_optional_vol(row.short_realized_vol),
        format_optional_vol(row.medium_realized_vol),
        format_optional_vol(row.long_realized_vol),
        row.volatility_regime
            .clone()
            .unwrap_or_else(|| "-".to_string()),
        volatility_age_flags(row),
    ]
}

fn format_optional_vol(value: Option<f64>) -> String {
    value.map_or_else(|| "-".to_string(), |value| format!("{value:.5}"))
}

fn volatility_age_flags(row: &RuntimeVolatilityRow) -> String {
    let flags = if row.flags.is_empty() {
        "OK".to_string()
    } else {
        row.flags.join(",")
    };
    match row.age_ms {
        Some(age_ms) => format!("{age_ms}ms {flags}"),
        None => format!("- {flags}"),
    }
}
```

Replace `probability_widths()` with:

```rust
fn probability_widths(_column_count: usize) -> Vec<Constraint> {
    vec![
        Constraint::Length(18),
        Constraint::Length(10),
        Constraint::Length(12),
        Constraint::Length(9),
        Constraint::Length(11),
        Constraint::Min(12),
    ]
}
```

- [ ] **Step 4: Re-run the focused Probability test**

Run:

```bash
cd /Users/goon/polymarket/rust && cargo test -p polymarket-cockpit-tui probability_table_stays_pending_when_probabilities_are_empty_even_with_volatility -q
```

Expected: pass.

- [ ] **Step 5: Commit Task 4**

Run:

```bash
cd /Users/goon/polymarket && git add rust/crates/polymarket-cockpit-tui/src/render/probability.rs && git commit -m "fix: keep probability tui focused on probabilities"
```

Expected: commit succeeds.

## Task 5: Update Both Mac Desktop Launchers To Use The Canonical Script

**Files:**
- Modify: `tests/scripts/test_mac_tui_launcher.py`
- Modify: `/Users/goon/Desktop/Desktop - spoon/Polymarket TUI.command`

- [ ] **Step 1: Add the failing launcher delegation test**

Change the top of `tests/scripts/test_mac_tui_launcher.py` from:

```python
from pathlib import Path
```

to:

```python
import pytest
from pathlib import Path
```

Then append this test to the same file:

```python


DESKTOP_LAUNCHERS = [
    Path("/Users/goon/Desktop/Polymarket TUI.command"),
    Path("/Users/goon/Desktop/Desktop - spoon/Polymarket TUI.command"),
]


@pytest.mark.skipif(
    not all(path.exists() for path in DESKTOP_LAUNCHERS),
    reason="Enoch Mac desktop launchers are not present on this machine",
)
def test_desktop_tui_launchers_delegate_to_canonical_script() -> None:
    expected = "#!/bin/zsh\nexec /Users/goon/polymarket/scripts/open_tui_mac.sh\n"

    for launcher in DESKTOP_LAUNCHERS:
        assert launcher.read_text(encoding="utf-8") == expected
```

- [ ] **Step 2: Run the focused launcher test and verify it fails locally**

Run:

```bash
cd /Users/goon/polymarket && uv run pytest tests/scripts/test_mac_tui_launcher.py::test_desktop_tui_launchers_delegate_to_canonical_script -q
```

Expected on Enoch's Mac: fail because `/Users/goon/Desktop/Desktop - spoon/Polymarket TUI.command` still contains the copied old script.

- [ ] **Step 3: Replace the stale desktop launcher with the wrapper**

Change `/Users/goon/Desktop/Desktop - spoon/Polymarket TUI.command` to exactly:

```zsh
#!/bin/zsh
exec /Users/goon/polymarket/scripts/open_tui_mac.sh
```

- [ ] **Step 4: Verify both desktop launchers are executable**

Run:

```bash
test -x "/Users/goon/Desktop/Polymarket TUI.command" && test -x "/Users/goon/Desktop/Desktop - spoon/Polymarket TUI.command" && printf 'launchers executable\n'
```

Expected:

```text
launchers executable
```

If either launcher is not executable, run:

```bash
chmod +x "/Users/goon/Desktop/Polymarket TUI.command" "/Users/goon/Desktop/Desktop - spoon/Polymarket TUI.command"
```

- [ ] **Step 5: Re-run the launcher tests**

Run:

```bash
cd /Users/goon/polymarket && uv run pytest tests/scripts/test_mac_tui_launcher.py -q
```

Expected: pass.

- [ ] **Step 6: Commit Task 5 repo test change**

Run:

```bash
cd /Users/goon/polymarket && git add tests/scripts/test_mac_tui_launcher.py && git commit -m "test: verify mac tui desktop launchers"
```

Expected: commit succeeds. The desktop launcher outside the repo is intentionally not part of the commit.

## Task 6: Build And Verify The Mac TUI Locally

**Files:**
- Read: `scripts/open_tui_mac.sh`
- Build output: `rust/target/release/polymarket-cockpit-tui`

- [ ] **Step 1: Run all Rust TUI tests**

Run:

```bash
cd /Users/goon/polymarket/rust && cargo test -p polymarket-cockpit-tui -q
```

Expected: pass.

- [ ] **Step 2: Build the release TUI binary**

Run:

```bash
cd /Users/goon/polymarket/rust && cargo build --release -p polymarket-cockpit-tui
```

Expected: pass and produce `/Users/goon/polymarket/rust/target/release/polymarket-cockpit-tui`.

- [ ] **Step 3: Run Python launcher tests**

Run:

```bash
cd /Users/goon/polymarket && uv run pytest tests/scripts/test_mac_tui_launcher.py -q
```

Expected: pass.

- [ ] **Step 4: Verify the canonical launcher test path**

Run:

```bash
POLYMARKET_TUI_TEST_LAUNCH=1 POLYMARKET_ENGINE_API_URL=http://100.72.104.49:8000 /Users/goon/polymarket/scripts/open_tui_mac.sh
```

Expected:

```text
Checking THEPC Polymarket runtime...
Mac TUI launcher ready.
```

If this fails before the ready line because THEPC is unreachable, run:

```bash
curl -fsS --max-time 4 http://100.72.104.49:8000/api/runtime/live?limit=1 >/tmp/polymarket-live-check.json && python3 -m json.tool /tmp/polymarket-live-check.json | head -40
```

Expected when THEPC is reachable: JSON with top-level `ok`, `status`, `monitor`, `volatility`, and `latency`. If this command fails too, stop and report that local build passed but live launcher verification is blocked by THEPC/Tailscale reachability.

- [ ] **Step 5: Confirm the release binary is from the current commit**

Run:

```bash
current_head="$(git -C /Users/goon/polymarket rev-parse HEAD)" && built_head="$(cat /Users/goon/polymarket/rust/target/release/.polymarket-cockpit-tui.git-head)" && test "$current_head" = "$built_head" && printf 'binary matches current head %s\n' "$current_head"
```

Expected:

```text
binary matches current head <current commit sha>
```

- [ ] **Step 6: Commit verification note only if a repo doc is intentionally updated**

No commit is required for build artifacts. Do not commit `rust/target/`, `/tmp/polymarket-live-check.json`, or desktop files.

## Final Verification

Run these commands after all tasks:

```bash
cd /Users/goon/polymarket/rust && cargo test -p polymarket-cockpit-tui -q
cd /Users/goon/polymarket/rust && cargo build --release -p polymarket-cockpit-tui
cd /Users/goon/polymarket && uv run pytest tests/scripts/test_mac_tui_launcher.py -q
POLYMARKET_TUI_TEST_LAUNCH=1 POLYMARKET_ENGINE_API_URL=http://100.72.104.49:8000 /Users/goon/polymarket/scripts/open_tui_mac.sh
```

Expected:

- Rust TUI tests pass.
- Release TUI build passes.
- Mac launcher pytest passes.
- Launcher prints `Mac TUI launcher ready.` after checking THEPC runtime.

## Completion Criteria

- `Live | Systems | Market | Probability | Volatility | Outcomes | Logs` appears in that order.
- Probability shows probability output rows or `probability pending`; it never renders volatility rows.
- Volatility shows BTC/ETH rows with `sigma_tau`, short/medium/long realized vol, regime, source, lookback, and age/flags when those fields are available.
- Both Mac desktop launchers call `/Users/goon/polymarket/scripts/open_tui_mac.sh`.
- The Mac release TUI binary builds locally and the canonical launcher test path succeeds.
- No unrelated dirty file, especially `docs/BINARY_CONTRACT_ENGINE_PLAN.md`, is staged or reverted.
