# In-Process Rust Normalizer Sidecar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce normalized-data cycle latency by replacing the deployed normalizer shell loop's three Python subprocesses per cycle with one long-lived Python sidecar process.

**Architecture:** Keep the existing one-shot CLI commands for manual rebuilds and tests. Add a small in-process sidecar runner that applies the DuckDB schema once at startup, then runs raw normalization, current decision-state building, and normalized health writing inside one process per cycle. Update the Docker entrypoint to `exec` the sidecar loop so Docker still owns restart behavior.

**Tech Stack:** Python 3.14 in the current `uv` environment, existing `DuckDbIngestStore`, existing Rust raw JSONL normalizer, existing decision-state snapshot builder, existing normalized-health writer, POSIX shell Docker entrypoint, pytest/ruff/mypy.

---

## Evidence

The current `/Users/goon/polymarket/deploy/normalizer/normalizer-entrypoint.sh` starts three separate `polymarket-engine` commands every loop:

- `normalize-rust-events`
- `build-current-decision-states`
- `write-normalized-health`

Local temp-data benchmark on 2026-06-02:

- in-process normalize + state + health cycle average: about `100ms`
- current three-subprocess cycle average: about `510ms`

That points to process startup and repeated CLI/schema overhead as the next latency target after byte-offset checkpoints.

## File Structure

Create:
- `/Users/goon/polymarket/src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`  
  Owns the reusable cycle and loop functions for the normalizer sidecar.
- `/Users/goon/polymarket/tests/ingestion/test_rust_normalizer_sidecar.py`  
  Tests the one-cycle behavior and loop logging without Docker.

Modify:
- `/Users/goon/polymarket/src/polymarket_engine/cli.py`  
  Adds `run-rust-normalizer-sidecar` CLI command and `--once` test/smoke option.
- `/Users/goon/polymarket/deploy/normalizer/normalizer-entrypoint.sh`  
  Replaces the shell `while true` and three subprocesses with one `exec polymarket-engine run-rust-normalizer-sidecar ...`.
- `/Users/goon/polymarket/tests/test_cli.py`  
  Adds parser and `--once` command coverage.
- `/Users/goon/polymarket/tests/scripts/test_deploy_script.py`  
  Updates entrypoint assertions to prove the deployed sidecar uses one long-lived process.

## Risk Areas

- Schema application should happen once at sidecar startup, not every cycle. Keep `normalize-rust-events` unchanged for manual one-shot rebuilds.
- If a cycle raises, the sidecar process should exit rather than swallowing the error forever; Docker restart policy can restart it.
- The entrypoint must preserve the raw archive sentinel check before starting the Python sidecar.
- The new log line must preserve `normalizer_cycle elapsed_ms=... normalize_ms=... state_ms=... health_ms=...` so existing log greps still work.
- The sidecar should not reprocess full files unless `--reprocess-all` is explicitly passed.

## Subagent Proposal

No subagent is recommended for the first implementation because the write set is small and tightly coupled: one CLI command, one runner module, one deploy entrypoint, and tests. If delegating anyway, split into:

- **Agent A:** `/Users/goon/polymarket/src/polymarket_engine/ingestion/rust_normalizer_sidecar.py` and `/Users/goon/polymarket/tests/ingestion/test_rust_normalizer_sidecar.py`
- **Agent B:** `/Users/goon/polymarket/src/polymarket_engine/cli.py`, `/Users/goon/polymarket/tests/test_cli.py`, `/Users/goon/polymarket/deploy/normalizer/normalizer-entrypoint.sh`, and `/Users/goon/polymarket/tests/scripts/test_deploy_script.py`

---

### Task 1: Sidecar Cycle Module

**Files:**
- Create: `/Users/goon/polymarket/src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
- Create: `/Users/goon/polymarket/tests/ingestion/test_rust_normalizer_sidecar.py`

- [ ] **Step 1: Write the failing one-cycle test**

Create `/Users/goon/polymarket/tests/ingestion/test_rust_normalizer_sidecar.py` with a test that:

- creates a temp raw tree with `.polymarket_archive_root`
- writes one Chainlink JSONL row
- writes a minimal Rust state-manager `status.json`
- calls `run_rust_normalizer_cycle(...)`
- asserts a health file exists
- asserts `rows_read == 1`
- asserts timing fields are present and non-negative

Run:
```bash
uv run pytest -q tests/ingestion/test_rust_normalizer_sidecar.py::test_sidecar_cycle_normalizes_builds_states_and_writes_health
```

Expected: fail with import error for `polymarket_engine.ingestion.rust_normalizer_sidecar`.

- [ ] **Step 2: Implement the cycle function**

Create `/Users/goon/polymarket/src/polymarket_engine/ingestion/rust_normalizer_sidecar.py` with:

- dataclass `RustNormalizerCycleResult`
- function `run_rust_normalizer_cycle(...)`
- helper `_normalizer_summary(results)`

The cycle should:

1. create `DuckDbIngestStore(db_path)`
2. optionally call `store.apply_schema()`
3. call `normalize_rust_event_tree(raw_root=raw_root, store=store, reprocess_all=reprocess_all)`
4. if `status_path.exists()`, call `build_current_decision_state_snapshots(..., include_next=include_next)`
5. call `write_normalized_health_status(store=store, out_path=normalized_health_path)`
6. return the row/byte summary plus `elapsed_ms`, `normalize_ms`, `state_ms`, and `health_ms`

- [ ] **Step 3: Run the one-cycle test**

Run:
```bash
uv run pytest -q tests/ingestion/test_rust_normalizer_sidecar.py::test_sidecar_cycle_normalizes_builds_states_and_writes_health
```

Expected: pass.

- [ ] **Step 4: Add a no-status-path test**

Add a test that calls `run_rust_normalizer_cycle(...)` with a missing status path and asserts:

- health is still written
- `states_written == 0`
- no exception is raised

Run:
```bash
uv run pytest -q tests/ingestion/test_rust_normalizer_sidecar.py
```

Expected: pass.

---

### Task 2: CLI Command

**Files:**
- Modify: `/Users/goon/polymarket/src/polymarket_engine/cli.py`
- Modify: `/Users/goon/polymarket/tests/test_cli.py`

- [ ] **Step 1: Write failing parser test**

Add a test in `/Users/goon/polymarket/tests/test_cli.py` that parses:

```bash
run-rust-normalizer-sidecar \
  --raw-root data/raw \
  --duckdb-path data/db/polymarket.duckdb \
  --status-path data/live/status.json \
  --normalized-health-path data/live/normalized_health.json \
  --interval-seconds 1 \
  --once
```

Expected parsed values:

- `args.command == "run-rust-normalizer-sidecar"`
- `args.raw_root == Path("data/raw")`
- `args.duckdb_path == Path("data/db/polymarket.duckdb")`
- `args.status_path == Path("data/live/status.json")`
- `args.normalized_health_path == Path("data/live/normalized_health.json")`
- `args.interval_seconds == 1.0`
- `args.once is True`

Run:
```bash
uv run pytest -q tests/test_cli.py::test_parse_run_rust_normalizer_sidecar_args
```

Expected: fail because the command does not exist.

- [ ] **Step 2: Add parser and dispatch**

In `/Users/goon/polymarket/src/polymarket_engine/cli.py`:

- add the subparser `run-rust-normalizer-sidecar`
- add `--raw-root`, `--duckdb-path`, `--status-path`, `--normalized-health-path`, `--interval-seconds`, `--include-next`, `--reprocess-all`, and `--once`
- dispatch it from `run_collect_command(...)`

For `--once`, run one cycle with `apply_schema=True`, print one JSON summary, and return `0`.

- [ ] **Step 3: Run parser test**

Run:
```bash
uv run pytest -q tests/test_cli.py::test_parse_run_rust_normalizer_sidecar_args
```

Expected: pass.

- [ ] **Step 4: Write failing once-mode command test**

Add a test in `/Users/goon/polymarket/tests/test_cli.py` that:

- creates a temp raw tree and one Chainlink row
- creates a temp `status.json`
- runs `await cli.run_collect_command([... "run-rust-normalizer-sidecar", ..., "--once"])`
- asserts result `0`
- asserts stdout JSON includes `rows_read == 1`, `bytes_read > 0`, and `elapsed_ms >= 0`
- asserts normalized health file exists

Run:
```bash
uv run pytest -q tests/test_cli.py::test_run_rust_normalizer_sidecar_once_command
```

Expected: fail until CLI dispatch calls the new sidecar module.

- [ ] **Step 5: Implement once-mode dispatch**

Wire `--once` to `run_rust_normalizer_cycle(...)`, print the cycle result as sorted compact JSON, and return `0`.

- [ ] **Step 6: Run CLI tests**

Run:
```bash
uv run pytest -q tests/test_cli.py::test_parse_run_rust_normalizer_sidecar_args tests/test_cli.py::test_run_rust_normalizer_sidecar_once_command
```

Expected: pass.

---

### Task 3: Long-Running Loop

**Files:**
- Modify: `/Users/goon/polymarket/src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
- Modify: `/Users/goon/polymarket/src/polymarket_engine/cli.py`
- Modify: `/Users/goon/polymarket/tests/ingestion/test_rust_normalizer_sidecar.py`

- [ ] **Step 1: Write failing loop test**

Add a test that monkeypatches `time.sleep` and calls `run_rust_normalizer_loop(..., max_cycles=2)` or equivalent. Assert:

- two `normalizer_cycle` lines are printed
- the second cycle has `rows_read == 0`
- `sleep` was called once with the interval

Run:
```bash
uv run pytest -q tests/ingestion/test_rust_normalizer_sidecar.py::test_sidecar_loop_reuses_process_and_sleeps_between_cycles
```

Expected: fail because loop function does not exist.

- [ ] **Step 2: Implement loop function**

Add `run_rust_normalizer_loop(...)` that:

- calls `store.apply_schema()` once before the loop
- runs cycles with `apply_schema=False`
- prints `normalizer_cycle elapsed_ms=... normalize_ms=... state_ms=... health_ms=... files=... rows_read=... bytes_read=...`
- sleeps after each cycle unless `max_cycles` is reached

- [ ] **Step 3: Wire CLI non-once mode**

In `/Users/goon/polymarket/src/polymarket_engine/cli.py`, if `--once` is absent, call the loop function and return `0`.

- [ ] **Step 4: Run sidecar tests**

Run:
```bash
uv run pytest -q tests/ingestion/test_rust_normalizer_sidecar.py
```

Expected: pass.

---

### Task 4: Deploy Entrypoint

**Files:**
- Modify: `/Users/goon/polymarket/deploy/normalizer/normalizer-entrypoint.sh`
- Modify: `/Users/goon/polymarket/tests/scripts/test_deploy_script.py`

- [ ] **Step 1: Write failing deploy-script test**

Update `/Users/goon/polymarket/tests/scripts/test_deploy_script.py` so the normalizer entrypoint test asserts:

- it contains `run-rust-normalizer-sidecar`
- it contains `exec polymarket-engine`
- it does not contain `while true`
- it passes `--interval-seconds "$INTERVAL_SECONDS"`
- it passes `--normalized-health-path "$NORMALIZED_HEALTH_PATH"`

Run:
```bash
uv run pytest -q tests/scripts/test_deploy_script.py::test_normalizer_defaults_to_one_second_checkpointed_cadence
```

Expected: fail while the entrypoint still has the shell loop.

- [ ] **Step 2: Replace shell loop with sidecar exec**

Change `/Users/goon/polymarket/deploy/normalizer/normalizer-entrypoint.sh` after the `mkdir` line to:

```sh
exec polymarket-engine run-rust-normalizer-sidecar \
  --raw-root "$RAW_DIR" \
  --duckdb-path "$DB_PATH" \
  --status-path "$STATUS_PATH" \
  --normalized-health-path "$NORMALIZED_HEALTH_PATH" \
  --interval-seconds "$INTERVAL_SECONDS" \
  --include-next
```

- [ ] **Step 3: Run deploy-script test and shell syntax check**

Run:
```bash
uv run pytest -q tests/scripts/test_deploy_script.py::test_normalizer_defaults_to_one_second_checkpointed_cadence
sh -n deploy/normalizer/normalizer-entrypoint.sh
```

Expected: pass and no shell syntax output.

---

### Task 5: Verification And Commit

**Files:**
- Verify all changed files.

- [ ] **Step 1: Run focused tests**

Run:
```bash
uv run pytest -q \
  tests/ingestion/test_rust_normalizer_sidecar.py \
  tests/test_cli.py \
  tests/scripts/test_deploy_script.py
```

Expected: pass.

- [ ] **Step 2: Run static checks**

Run:
```bash
uv run ruff check src/polymarket_engine/cli.py src/polymarket_engine/ingestion/rust_normalizer_sidecar.py tests/test_cli.py tests/ingestion/test_rust_normalizer_sidecar.py tests/scripts/test_deploy_script.py
uv run mypy src/polymarket_engine/cli.py src/polymarket_engine/ingestion/rust_normalizer_sidecar.py tests/test_cli.py tests/ingestion/test_rust_normalizer_sidecar.py
```

Expected: both pass.

- [ ] **Step 3: Run broad branch checks**

Run:
```bash
uv run ruff check .
uv run mypy src tests
uv run pytest -q
cargo test -p polymarket-live-probe
```

Expected: all pass.

- [ ] **Step 4: Commit**

Run:
```bash
git add \
  src/polymarket_engine/ingestion/rust_normalizer_sidecar.py \
  src/polymarket_engine/cli.py \
  deploy/normalizer/normalizer-entrypoint.sh \
  tests/ingestion/test_rust_normalizer_sidecar.py \
  tests/test_cli.py \
  tests/scripts/test_deploy_script.py \
  docs/superpowers/plans/2026-06-02-in-process-rust-normalizer-sidecar.md
git commit -m "Run normalizer sidecar in one process"
```

Expected: new local commit. Do not push or deploy without explicit approval.

---

## Approval Gate

Stop before Task 1 code changes until Enoch approves this plan.
