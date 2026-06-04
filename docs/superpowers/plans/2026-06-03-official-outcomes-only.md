# Official Outcomes Only Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make recorded market outcomes come only from Polymarket official resolution metadata, not local Chainlink-derived comparison.

**Architecture:** Keep `validation.market_outcome_history` as the runtime/history table, but change the writer contract so `official_winner` is the label and `computed_winner` remains null. Add a small source adapter that parses Polymarket CLOB market metadata payloads and maps the winning token id to the stored Up/Down token ids. The normalizer refresh stays throttled and read-only.

**Tech Stack:** Python 3, DuckDB, pytest, existing FastAPI runtime JSON, Rust TUI status parsing/rendering.

---

### Task 1: Official Resolution Parser

**Files:**
- Modify: `src/polymarket_engine/validation/outcomes.py`
- Test: `tests/validation/test_outcomes.py`

- [ ] **Step 1: Write failing tests**

Add tests proving a Polymarket market payload with a `winner: true` token maps the winning token id to `UP` or `DOWN`, and unresolved/ambiguous payloads stay pending.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/validation/test_outcomes.py -k official_resolution`

Expected: FAIL because the official parser does not exist yet.

- [ ] **Step 3: Implement minimal parser**

Add a pure parser that accepts a market payload and stored `up_token_id` / `down_token_id`, returning resolved official fields only when exactly one known token is marked winner.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest -q tests/validation/test_outcomes.py -k official_resolution`

Expected: PASS.

### Task 2: Outcome Refresh Contract

**Files:**
- Modify: `src/polymarket_engine/validation/outcomes.py`
- Modify: `src/polymarket_engine/validation/__init__.py`
- Modify: `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
- Test: `tests/validation/test_outcomes.py`
- Test: `tests/ingestion/test_rust_normalizer_sidecar.py`

- [ ] **Step 1: Write failing tests**

Change outcome-refresh tests so expired markets are written as `official_resolution_status = "pending"` and `computed_winner is None` when no official source payload is present.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/validation/test_outcomes.py tests/ingestion/test_rust_normalizer_sidecar.py`

Expected: FAIL because the current code computes local labels.

- [ ] **Step 3: Implement official-only refresh**

Rename the refresh function to official semantics, stop using Chainlink end-price comparison as a label, and preserve raw threshold/end columns only as nullable audit context if already available.

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest -q tests/validation/test_outcomes.py tests/ingestion/test_rust_normalizer_sidecar.py`

Expected: PASS.

### Task 3: Runtime/TUI Surface

**Files:**
- Modify: `tests/test_runtime_api.py`
- Modify: `rust/crates/polymarket-cockpit-tui/src/status.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/outcomes.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
- Test: Rust cockpit tests and runtime API tests.

- [ ] **Step 1: Write failing tests**

Update tests so the runtime/TUI no longer presents `computed_winner` as an outcome label and shows official winner or pending.

- [ ] **Step 2: Verify RED**

Run: `uv run pytest -q tests/test_runtime_api.py -k outcomes` and `cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui outcomes`

Expected: FAIL until code stops rendering computed labels.

- [ ] **Step 3: Implement display changes**

Keep JSON compatibility for `computed_winner` as nullable, but render official/pending only in the TUI outcome section.

- [ ] **Step 4: Verify GREEN**

Run the same focused Python and Rust tests.

### Task 4: Docs

**Files:**
- Modify: `docs/PART_TWO_LIVE_COLLECTORS.md`
- Modify: `docs/SPOON_DEPLOYMENT.md`
- Modify: `docs/BINARY_CONTRACT_ENGINE_PLAN.md`

- [ ] **Step 1: Update source-of-truth wording**

Replace text that says Chainlink-derived `computed_winner` is an outcome label with text that says official Polymarket metadata is required before labeling the contract.

- [ ] **Step 2: Verify docs references**

Run: `rg -n "computed_winner|Chainlink-rule-derived|official_winner" docs src tests rust/crates/polymarket-cockpit-tui/src`

Expected: No docs or UI text present computed labels as truth.

### Risk Areas

- Schema churn: avoid dropping columns now because runtime clients already parse `computed_winner`.
- Source ambiguity: if zero, multiple, or unknown tokens have `winner: true`, keep status pending and add a source note instead of guessing.
- Replay leakage: official labels must remain labels/history only, not features in decision-state or probability input.
- Live cadence: outcome refresh should remain throttled because outcomes only change after expiry.
