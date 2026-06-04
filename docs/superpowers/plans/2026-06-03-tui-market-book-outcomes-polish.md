# TUI Market Book Outcomes Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the cockpit TUI navigation and market/book readability issues while reducing non-market polling overhead without changing the current read-only runtime setup.

**Architecture:** Keep the live SSE market monitor as the fast path. Make the renderer deterministic and selection-aware: market rows remain grouped by asset with countdowns, the selected market always stays visible, the book keeps the fuller UP/DOWN split order book, and outcomes get their own navigation state. Slow only auxiliary probability/outcome polling; do not slow the live market stream.

**Tech Stack:** Rust TUI crate `rust/crates/polymarket-cockpit-tui`, ratatui, crossterm, tokio, existing Python runtime API.

---

### Task 1: Market Grouping, Countdown, and Selection Visibility

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/market.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/state.rs` if selection clamping needs to be exposed for tests
- Test: `rust/crates/polymarket-cockpit-tui/src/render/market.rs`

- [ ] **Step 1: Write failing tests**

Add tests covering:
- a visible blank separator before the second asset group (`BTC` then spacer then `ETH`)
- a countdown column derived from `RuntimeMonitor.generated_at` and `MarketGroup.expiry_ts`
- selected row remains visible when the display contains asset headers and spacers

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui render::market
```

Expected: tests fail because there is no spacer/countdown behavior yet.

- [ ] **Step 2: Implement minimal renderer changes**

Change `MarketDisplayRow` to include an `expires` or `ttl` string. Extend `market_header_labels()` to include `TTL`. Compute countdown from the monitor generated timestamp, falling back to `-` when timestamps are missing or unparsable. Keep existing local time formatting for the contract label.

- [ ] **Step 3: Verify focused tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui render::market
```

Expected: market tests pass.

### Task 2: Full-Depth UP/DOWN Book Split

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/orderbook.rs`
- Test: `rust/crates/polymarket-cockpit-tui/src/render/orderbook.rs`

- [x] **Step 1: Reconcile changed requirement**

The fixed `2 UP + 2 DOWN` depth was intentionally dropped after user feedback. The preferred behavior is the existing fuller order book split: render the selected market's UP levels first and DOWN levels second, with each side allowed to show the available valid depth independently.

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui render::orderbook
```

Expected: existing orderbook tests pass.

- [x] **Step 2: Keep variable-depth book behavior**

No production book-depth change is needed. The book remains selected-market scoped and variable-depth, capped at the existing six valid levels per side.

- [x] **Step 3: Verify focused tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui render::orderbook
```

Expected: orderbook tests pass.

### Task 3: Outcomes Tab Navigation

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/state.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/outcomes.rs`
- Test: same Rust files

- [ ] **Step 1: Write failing tests**

Add tests proving:
- `Up`/`Down` keys move selection on `MainTab::Outcomes`
- selected outcome is marked with `>`
- selected outcome stays visible when there are more rows than the viewport
- Market selection still works independently

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui state event_loop render::outcomes
```

Expected: tests fail because outcomes currently has no selection state.

- [ ] **Step 2: Implement minimal state and event handling**

Add `selected_outcome_index: Option<usize>` to `AppState`. Add `sync_outcome_selection`, `select_next_outcome`, and `select_previous_outcome` methods that clamp/wrap to available outcome rows. Extend `apply_key` so `Up`/`Down` acts on `MainTab::Outcomes`.

- [ ] **Step 3: Render selected outcomes**

Add a marker column to outcomes rows and apply the same visible-window behavior used by Market.

- [ ] **Step 4: Verify focused tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui state event_loop render::outcomes
```

Expected: tests pass.

### Task 4: Reduce TUI Overhead Without Slowing Live Market Latency

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
- Test: `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`

- [ ] **Step 1: Write failing tests**

Add tests proving auxiliary probability/outcome polling uses a slower constant than the live market poll interval, while `poll_interval_duration()` still honors the fast configured SSE cadence.

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui event_loop
```

Expected: tests fail if auxiliary cadence is still 1s and indistinguishable from the live path.

- [ ] **Step 2: Implement minimal overhead reduction**

Keep live SSE at the configured `--poll-interval-ms`. Increase auxiliary probability/outcome polling to about 3s, because those panes do not need to repaint every market tick. Do not change Docker topology, collectors, or runtime API.

- [ ] **Step 3: Verify focused tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui event_loop
```

Expected: event loop tests pass.

### Task 5: Full Local Verification

**Files:**
- No additional source changes unless failures identify a real issue.

- [ ] **Step 1: Run Rust TUI tests**

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui
```

Expected: all TUI tests pass.

- [ ] **Step 2: Run existing Python checks only if touched indirectly**

If no Python files changed during this plan, do not burn time on the full Python suite. If Python runtime contracts are touched, run:

```bash
uv run pytest -q
uv run mypy src tests
uv run ruff check .
```

Expected: all selected checks pass.

### Task 6: THEPC Deploy and Live Diagnostics

**Files:**
- No source changes.

- [ ] **Step 1: Wait for explicit approval before SSH/deploy**

This leaves the Mac, so get approval first.

- [ ] **Step 2: Deploy to THEPC**

Use the existing Tailscale SSH path for THEPC and the established deploy/sync command for this repo.

- [ ] **Step 3: Check live behavior**

Verify:
- Market bid/ask values change only when `/api/runtime/live` changes.
- Book title follows the selected market.
- Book shows two UP rows and two DOWN rows.
- Outcomes selection moves with arrow keys.
- Docker CPU/memory via `docker stats --no-stream`.

Record the result in the final report.

### Risk Notes

- The bid/ask “not updating” symptom may be upstream data staleness, not a TUI bug. The TUI should not synthesize changing prices.
- Countdown uses API generated time, not local wall clock, so it reflects the runtime snapshot as-of state.
- Reducing auxiliary polling should lower TUI/API overhead without changing the live market SSE latency path.
