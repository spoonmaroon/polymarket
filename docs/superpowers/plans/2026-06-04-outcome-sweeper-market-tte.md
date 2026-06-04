# Outcome Sweeper and Market TTE Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep expired unresolved outcomes from getting parked as pending, and keep the Market table focused on current/next contracts with local expiry date/time before the asset.

**Architecture:** The normalizer remains the single writer for DuckDB outcome history, but each outcome refresh must include both the newest expired markets and older pending markets that need official-source rechecks. The TUI Market table remains read-only and derived from `/api/runtime/live`, but it should hide rows older than the post-expiry handoff window and move stale outcome tracking to the Outcomes tab.

**Tech Stack:** Python 3.14/pytest/DuckDB/httpx for outcome resolution; Rust/ratatui/cargo tests for the cockpit TUI; Docker Compose on THEPC for deploy.

---

## Root Cause Findings

- ETH `00:35` local / `05:35 UTC` was still `pending` in `validation.market_outcome_history`, but the official CLOB endpoint for condition `0x62d3c3193c646ce92d8ccd9e4a9f407b1a5d49725d3db2656a972a192dc509a7` reports token `23077214231580156388726837531044636698978266416836790192501768037070252478095970338200753522352876881128826098820077` as winner, so the official result is `UP`.
- `upsert_official_market_outcomes(..., max_markets=4)` sorts expired market groups newest-first, then drops everything outside the newest four. Once a pending market falls outside that window, it can stay pending forever.
- `/api/runtime/live` currently reports current BTC/ETH expiring at `06:10 UTC` and next at `06:15 UTC`; the collector is updating. Seeing `+4:33` in Market is a TUI display/state retention problem, not a current collector selection problem.

## File Structure

- Modify `src/polymarket_engine/validation/outcomes.py`
  - Add pending outcome sweeper selection so unresolved expired markets are revisited even when they are older than the newest refresh window.
- Modify `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
  - Add `POLYMARKET_OFFICIAL_OUTCOME_PENDING_SWEEP_LIMIT`, default `20`, and pass it into `upsert_official_market_outcomes`.
- Modify `deploy/collector/docker-compose.yml` and `deploy/collector/.env.example`
  - Expose the pending sweep limit.
- Modify `tests/validation/test_outcomes.py`
  - Add regression coverage for an older pending market outside `max_markets`.
- Modify `tests/ingestion/test_rust_normalizer_sidecar.py`
  - Lock the sidecar/env wiring for the pending sweep limit.
- Modify `rust/crates/polymarket-cockpit-tui/src/render/market.rs`
  - Replace `Seen` with an `Expires` column before `Market`.
  - Render local expiry date/time like `Jun 04 01:10 CDT`.
  - Show post-expiry rows for only 60 seconds so the Market tab can show the official outcome handoff.
  - Add a read-only `Outcome` column on Market, sourced only from official outcome history.
- Modify `rust/crates/polymarket-cockpit-tui/src/market_view.rs` only if a shared local expiry formatter is cleaner than keeping it in the renderer.

---

### Task 1: Add Official Outcome Pending Sweeper

**Files:**
- Modify: `src/polymarket_engine/validation/outcomes.py`
- Test: `tests/validation/test_outcomes.py`

- [ ] **Step 1: Write the failing stale-pending regression test**

Add a test that creates at least three expired markets, marks the oldest as pending in `validation.market_outcome_history`, calls `upsert_official_market_outcomes(max_markets=1, pending_sweep_limit=10, market_payload_source=fake_source)`, and asserts the oldest pending market resolves from the fake official CLOB payload.

Run:

```bash
uv run pytest tests/validation/test_outcomes.py::test_official_outcome_sweeps_stale_pending_market_outside_newest_limit -q
```

Expected: FAIL because `upsert_official_market_outcomes` does not accept `pending_sweep_limit` and does not revisit older pending markets.

- [ ] **Step 2: Implement pending market selection**

In `upsert_official_market_outcomes`, add parameter:

```python
pending_sweep_limit: int | None = None
```

Add helper:

```python
def _pending_official_market_ids(
    *,
    store: DuckDbIngestStore,
    asof_ts: datetime,
    limit: int | None,
) -> set[str]:
    limit_clause = "" if limit is None else " limit ?"
    params: list[object] = [asof_ts]
    if limit is not None:
        params.append(limit)
    with store._connection() as conn:
        rows = conn.execute(
            """
            select market_id
            from validation.market_outcome_history
            where expiry_ts <= ?
              and official_winner is null
              and official_resolution_status = 'pending'
            order by expiry_ts asc, asset, interval, market_id
            """
            + limit_clause,
            params,
        ).fetchall()
    return {str(row[0]) for row in rows}
```

Select groups as:

```python
newest_groups = market_groups if max_markets is None else market_groups[:max_markets]
pending_ids = _pending_official_market_ids(
    store=store,
    asof_ts=asof_ts,
    limit=pending_sweep_limit,
)
selected_by_market_id = {
    next(iter(rows.values())).market_id: rows for rows in newest_groups if rows
}
for rows in reversed(market_groups):
    if not rows:
        continue
    market_id = next(iter(rows.values())).market_id
    if market_id in pending_ids:
        selected_by_market_id[market_id] = rows
market_groups = list(selected_by_market_id.values())
```

- [ ] **Step 3: Run the targeted validation tests**

```bash
uv run pytest tests/validation/test_outcomes.py -q
```

Expected: all validation outcome tests pass.

---

### Task 2: Wire Sweeper Limit Through the Sidecar

**Files:**
- Modify: `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
- Modify: `deploy/collector/docker-compose.yml`
- Modify: `deploy/collector/.env.example`
- Test: `tests/ingestion/test_rust_normalizer_sidecar.py`

- [ ] **Step 1: Write the failing sidecar env test**

Add a test beside `test_upsert_market_outcomes_limits_official_refresh_from_env` that sets:

```python
monkeypatch.setenv("POLYMARKET_OFFICIAL_OUTCOME_PENDING_SWEEP_LIMIT", "7")
```

Patch `upsert_official_market_outcomes` and assert it receives:

```python
kwargs["pending_sweep_limit"] == 7
```

Run:

```bash
uv run pytest tests/ingestion/test_rust_normalizer_sidecar.py::test_upsert_market_outcomes_uses_pending_sweep_limit_from_env -q
```

Expected: FAIL because the env var is not wired yet.

- [ ] **Step 2: Implement sidecar env parsing**

Add constants:

```python
OUTCOME_PENDING_SWEEP_LIMIT = 20
OFFICIAL_OUTCOME_PENDING_SWEEP_LIMIT_ENV = "POLYMARKET_OFFICIAL_OUTCOME_PENDING_SWEEP_LIMIT"
```

Add helper:

```python
def _official_outcome_pending_sweep_limit_from_env() -> int | None:
    raw_limit = os.environ.get(OFFICIAL_OUTCOME_PENDING_SWEEP_LIMIT_ENV)
    if raw_limit is None or raw_limit.strip() == "":
        return OUTCOME_PENDING_SWEEP_LIMIT
    limit = int(raw_limit)
    return limit if limit > 0 else None
```

Pass it to `upsert_official_market_outcomes(...)`.

- [ ] **Step 3: Add deploy env defaults**

In `deploy/collector/docker-compose.yml` normalizer environment add:

```yaml
POLYMARKET_OFFICIAL_OUTCOME_PENDING_SWEEP_LIMIT: ${POLYMARKET_OFFICIAL_OUTCOME_PENDING_SWEEP_LIMIT:-20}
```

In `deploy/collector/.env.example` add:

```text
POLYMARKET_OFFICIAL_OUTCOME_PENDING_SWEEP_LIMIT=20
```

- [ ] **Step 4: Run sidecar tests**

```bash
uv run pytest tests/ingestion/test_rust_normalizer_sidecar.py -q
```

Expected: all sidecar tests pass.

---

### Task 3: Fix Market Table Expiry Time and Expired Row Display

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/market.rs`
- Possibly modify: `rust/crates/polymarket-cockpit-tui/src/market_view.rs`

- [ ] **Step 1: Write failing TUI tests**

Add tests that assert:

```rust
assert_eq!(
    market_header_labels(),
    ["", "Expires", "Market", "UP bid/ask", "DOWN bid/ask", "Spread", "TTE", "Outcome"]
);
assert!(rows[1].expires.contains("Jun 03"));
assert_eq!(rows[1].market, "BTC 5m");
```

Add another test with monitor `generated_at` more than 60 seconds after expiry and assert the expired market row is absent from `market_rows(...)`.

Run:

```bash
cargo test -q --manifest-path rust/crates/polymarket-cockpit-tui/Cargo.toml market_rows
```

Expected: FAIL because the table still has `Seen`, no Market outcome column, and still renders long-expired rows.

- [ ] **Step 2: Implement Market row changes**

Update `MarketDisplayRow` to:

```rust
pub struct MarketDisplayRow {
    pub marker: String,
    pub expires: String,
    pub market: String,
    pub up: String,
    pub down: String,
    pub spread: String,
    pub tte: String,
    pub outcome: String,
}
```

Update header labels to:

```rust
["", "Expires", "Market", "UP bid/ask", "DOWN bid/ask", "Spread", "TTE", "Outcome"]
```

Before pushing a contract row, skip old expired groups:

```rust
if market_expired_beyond_handoff(group.expiry_ts, generated_at) {
    continue;
}
```

Render expiry timestamp from local time:

```rust
fn local_expiry_timestamp(expiry_ts: Option<DateTime<Utc>>) -> String
```

Use the existing expiry field from `MarketGroup`; fallback to `-` if missing. Show a row for up to 60 seconds after expiry so the official outcome can appear on Market, then drop it from Market and leave it on Outcomes.

- [ ] **Step 3: Run TUI tests**

```bash
cargo test -q --manifest-path rust/crates/polymarket-cockpit-tui/Cargo.toml
```

Expected: all TUI tests pass.

---

### Task 4: Verify, Commit, Push, Deploy to THEPC

**Files:** no new source files beyond Tasks 1-3.

- [ ] **Step 1: Run focused checks**

```bash
uv run pytest tests/validation/test_outcomes.py tests/ingestion/test_rust_normalizer_sidecar.py -q
cargo test -q --manifest-path rust/crates/polymarket-cockpit-tui/Cargo.toml
git diff --check
```

- [ ] **Step 2: Commit and push**

```bash
git add src/polymarket_engine/validation/outcomes.py \
  src/polymarket_engine/ingestion/rust_normalizer_sidecar.py \
  deploy/collector/docker-compose.yml \
  deploy/collector/.env.example \
  tests/validation/test_outcomes.py \
  tests/ingestion/test_rust_normalizer_sidecar.py \
  rust/crates/polymarket-cockpit-tui/src/render/market.rs
git commit -m "fix(runtime): sweep pending outcomes and update market handoff"
git push origin codex/polymarket-engine-tui
```

- [ ] **Step 3: Deploy to THEPC**

Sync the branch to THEPC, rebuild the normalizer/collector images, copy the new TUI binary to `/home/ender/bin/polymarket-cockpit-tui`, and restart only the normalizer/API services if needed for env changes. Do not stop the Rust collector unless compose requires it for the env update.

- [ ] **Step 4: Runtime verification**

Check:

```bash
curl -sS 'http://127.0.0.1:8000/api/runtime/live?limit=8'
curl -sS 'http://127.0.0.1:8000/api/runtime/outcomes?limit=20'
```

Expected:
- Market rows show current/next contracts, not `+4:xx`.
- `00:35` ETH resolves `UP` from official CLOB metadata.
- New pending expirations remain pending only while the official CLOB payload has no winning token.

---

## Self-Review

- Spec coverage: covers `00:35` pending, expiry date/time before BTC/ETH, removal of `Seen`, current/next contract display, Market outcome handoff, and outcome sweeper if collector dies.
- Boundary check: outcome labels still come only from official Polymarket CLOB metadata. No local compute is introduced.
- Risk: pending sweeper adds extra CLOB calls. Default cap is `20` older pending markets per refresh, plus the existing newest-market limit.
