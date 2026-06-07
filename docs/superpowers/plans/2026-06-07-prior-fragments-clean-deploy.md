# 6.1 Prior Fragments Clean Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Monte Carlo paths use an as-of-safe empirical prior distribution from historical BTC/ETH path fragments, then leave the repo pristine and deploy the exact verified SHA.

**Architecture:** Spoon is the CPU authority and writes `probability_inputs.json` plus `probability_fragments.json`. THEPC is the GPU/API authority and reads mirrored fragments into the four-generator ensemble. If no adequate fragment prior is available, the row is explicitly sparse (`sparse_scope=true`, `path_diagnosis=SPARSE`) instead of silently pretending the prior is clean.

**Tech Stack:** Python 3.14 test runtime, DuckDB storage, FastAPI runtime API, Vite/React UI, shell deploy scripts, Docker Compose split between Spoon and THEPC.

---

## Critical Constraints

- Preserve as-of safety: every fragment used for a probability row must have `asof_ts <= probability_input.asof_ts`.
- Do not use final settlement, later BTC/ETH moves, or later Polymarket data as features.
- Do not delete or discard dirty work without first making a checkpoint patch and getting an exact destructive cleanup approval.
- Deploy scripts require a pristine worktree. Deployment happens only after commits and verification.
- Keep live trading disabled; this remains read-only / paper-first.

## File Structure

- Modify `src/polymarket_engine/probability/generator_fragments.py`: JSON snapshot schema, fragment selection, sparse diagnostics.
- Modify `src/polymarket_engine/probability/path_generators.py`: consume fragment price paths and report selected fragment metadata.
- Modify `src/polymarket_engine/probability/ensemble_runtime.py`: accept selected prior fragments and expose fragment diagnostics.
- Modify `src/polymarket_engine/probability/gpu_worker.py`: read `probability_fragments.json`, select fragments per input, pass prices into ensemble.
- Modify `src/polymarket_engine/probability/runtime.py`: DuckDB fallback builds as-of fragments from price ticks and passes them to ensemble.
- Modify `src/polymarket_engine/features/rust_decision_snapshots.py`: normalizer writes `probability_fragments.json` alongside hot inputs.
- Modify `src/polymarket_engine/cli.py`: add `--probability-fragments-path` to normalizer and GPU worker commands.
- Modify `deploy/gpu/gpu-probability-entrypoint.sh` and `deploy/normalizer/normalizer-entrypoint.sh`: wire fragment path env vars.
- Modify `src/polymarket_engine/runtime_api.py` and `ui/src/App.tsx`: expose/display prior fragment count and sparse state if not already visible.
- Tests:
  - `tests/probability/test_generator_fragments.py`
  - `tests/probability/test_gpu_worker.py`
  - `tests/probability/test_runtime.py`
  - `tests/features/test_rust_decision_snapshots.py`
  - `tests/test_cli.py`
  - `tests/test_runtime_api.py`
  - `tests/ui/probability_value_test.ts`
  - deploy script tests as needed.

---

### Task 1: Safety Checkpoint and Baseline Inventory

**Files:**
- Create: `tmp/worktree-checkpoints/`
- Read-only: current git status and diff.

- [ ] **Step 1: Create a patch checkpoint**

Run:
```bash
mkdir -p tmp/worktree-checkpoints
git diff --binary > tmp/worktree-checkpoints/2026-06-07-before-prior-fragments.patch
git ls-files --others --exclude-standard > tmp/worktree-checkpoints/2026-06-07-untracked-files.txt
tar -czf tmp/worktree-checkpoints/2026-06-07-untracked-files.tgz -T tmp/worktree-checkpoints/2026-06-07-untracked-files.txt
```

Expected: patch, untracked list, and tarball exist.

- [ ] **Step 2: Record current dirty lanes**

Run:
```bash
git status --short --branch
git diff --stat
```

Expected: dirty files are visible and no cleanup has occurred yet.

- [ ] **Step 3: Verify current focused baseline**

Run:
```bash
uv run pytest tests/probability/test_ensemble_runtime.py tests/probability/test_gpu_worker.py tests/probability/test_runtime.py tests/test_runtime_api.py -q
npx tsx tests/ui/probability_value_test.ts
npm run build --prefix ui
```

Expected: pass, or record failures before starting implementation.

---

### Task 2: Fragment Snapshot Contract

**Files:**
- Modify: `src/polymarket_engine/probability/generator_fragments.py`
- Test: `tests/probability/test_generator_fragments.py`

- [ ] **Step 1: Write failing tests for fragment snapshot round-trip and as-of selection**

Add tests:
```python
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from polymarket_engine.probability.generator_fragments import (
    GENERATOR_FRAGMENTS_SCHEMA_VERSION,
    GeneratorFragment,
    read_probability_fragments,
    select_fragments_for_input,
    write_probability_fragments,
)
from polymarket_engine.probability.schema import ProbabilityInput


def _input() -> ProbabilityInput:
    return ProbabilityInput(
        state_id="state-btc-up",
        asof_ts=datetime(2026, 6, 7, 12, 3, tzinfo=UTC),
        asset="BTC",
        side="UP",
        comparison_operator=">=",
        seconds_left=120.0,
        settlement_price=100.0,
        threshold=101.0,
        sigma_tau=0.01,
        executable_price=0.52,
        source_age_ms=100,
        book_age_ms=100,
        z_path=-0.4,
    )


def test_probability_fragments_snapshot_round_trips_and_filters_asof(tmp_path: Path) -> None:
    out_path = tmp_path / "probability_fragments.json"
    prior = GeneratorFragment(
        fragment_id="btc-prior",
        asset="BTC",
        asof_ts=datetime(2026, 6, 7, 12, 0, tzinfo=UTC),
        prices=(100.0, 100.5, 101.0),
        horizon_seconds=120,
        z_path_bucket="near",
        quality_bucket="OK",
    )
    future = GeneratorFragment(
        fragment_id="btc-future",
        asset="BTC",
        asof_ts=datetime(2026, 6, 7, 12, 4, tzinfo=UTC),
        prices=(100.0, 99.5, 99.0),
        horizon_seconds=120,
        z_path_bucket="near",
        quality_bucket="OK",
    )

    write_probability_fragments(
        out_path=out_path,
        fragments=(prior, future),
        generated_at=datetime(2026, 6, 7, 12, 3, tzinfo=UTC),
    )

    payload = read_probability_fragments(
        out_path=out_path,
        max_age_seconds=60 * 60 * 24 * 365,
    )
    selected = select_fragments_for_input(
        payload.fragments,
        probability_input=_input(),
        min_fragment_count=1,
        max_fragment_count=10,
    )

    assert payload.schema_version == GENERATOR_FRAGMENTS_SCHEMA_VERSION
    assert [fragment.fragment_id for fragment in selected.fragments] == ["btc-prior"]
    assert selected.sparse is False
    assert selected.reason == "exact"


def test_select_fragments_marks_sparse_when_bucket_is_thin() -> None:
    selected = select_fragments_for_input(
        (),
        probability_input=_input(),
        min_fragment_count=2,
        max_fragment_count=10,
    )

    assert selected.fragments == ()
    assert selected.sparse is True
    assert selected.reason == "missing"
```

- [ ] **Step 2: Run tests and verify RED**

Run:
```bash
uv run pytest tests/probability/test_generator_fragments.py -q
```

Expected: FAIL because snapshot functions and `FragmentSelection` do not exist or are incomplete.

- [ ] **Step 3: Implement snapshot contract**

Implement in `generator_fragments.py`:
- `GENERATOR_FRAGMENTS_SCHEMA_VERSION = "polymarket-probability-fragments-v1"`
- `ProbabilityFragmentsPayload`
- `FragmentSelection`
- `write_probability_fragments(...)`
- `read_probability_fragments(...)`
- `select_fragments_for_input(...)`
- deterministic z bucket helper:
  - `deep_down` if `z_path < -1`
  - `near` if `-1 <= z_path <= 1`
  - `deep_up` if `z_path > 1`
- fallback selection:
  1. same asset, as-of-safe, horizon >= seconds_left, same z bucket
  2. same asset, as-of-safe, horizon >= seconds_left
  3. sparse empty selection

- [ ] **Step 4: Run tests and verify GREEN**

Run:
```bash
uv run pytest tests/probability/test_generator_fragments.py -q
```

Expected: pass.

---

### Task 3: Normalizer Writes Prior Fragment Snapshot

**Files:**
- Modify: `src/polymarket_engine/features/rust_decision_snapshots.py`
- Modify: `src/polymarket_engine/cli.py`
- Modify: `deploy/normalizer/normalizer-entrypoint.sh`
- Test: `tests/features/test_rust_decision_snapshots.py`
- Test: `tests/test_cli.py`
- Test: `tests/scripts/test_deploy_script.py`

- [ ] **Step 1: Write failing normalizer test**

Add or extend a test so `run_rust_normalizer_cycle(...)` with `probability_fragments_path` writes a snapshot where every fragment has `asof_ts <= cycle_asof_ts`, same asset as a tracked contract, positive prices, and horizon seconds.

- [ ] **Step 2: Run test and verify RED**

Run:
```bash
uv run pytest tests/features/test_rust_decision_snapshots.py::test_build_writes_probability_fragments_from_asof_price_history -q
```

Expected: FAIL because no fragment output path is wired.

- [ ] **Step 3: Implement fragment build from price ticks**

In `rust_decision_snapshots.py`:
- accept `probability_fragments_path: Path | None`
- when current decision states are built, use `read_store.price_ticks_before(...)`
- build fragments from older Chainlink/settlement price windows only
- enforce fragment `asof_ts <= asof_ts`
- write atomic `probability_fragments.json`
- keep output bounded by `fragment_max_rows`

- [ ] **Step 4: Wire CLI and entrypoint**

Add:
```bash
--probability-fragments-path data/live/probability_fragments.json
--fragment-max-rows 250000
```

Add env:
```bash
POLYMARKET_PROBABILITY_FRAGMENTS_PATH="${POLYMARKET_PROBABILITY_FRAGMENTS_PATH:-$LIVE_DIR/probability_fragments.json}"
```

- [ ] **Step 5: Verify task**

Run:
```bash
uv run pytest tests/features/test_rust_decision_snapshots.py tests/test_cli.py tests/scripts/test_deploy_script.py -q
```

Expected: pass.

---

### Task 4: GPU Worker Uses Fragment Prior

**Files:**
- Modify: `src/polymarket_engine/probability/gpu_worker.py`
- Modify: `deploy/gpu/gpu-probability-entrypoint.sh`
- Test: `tests/probability/test_gpu_worker.py`

- [ ] **Step 1: Write failing worker test**

Add a test where:
- `probability_inputs.json` has one ready input.
- `probability_fragments.json` has two matching historical BTC fragments and one future fragment.
- monkeypatched `run_four_generator_ensemble(...)` records `history_fragments`.
- worker cycle passes only the two as-of-safe matching fragment price paths.

- [ ] **Step 2: Run test and verify RED**

Run:
```bash
uv run pytest tests/probability/test_gpu_worker.py::test_worker_passes_asof_safe_fragments_into_ensemble -q
```

Expected: FAIL because worker does not read fragment snapshot.

- [ ] **Step 3: Implement worker fragment loading**

Add `probability_fragments_path: Path | None` to cycle/loop/CLI path.
Read once per cycle:
```python
payload = read_probability_fragments(out_path=probability_fragments_path, max_age_seconds=...)
selection = select_fragments_for_input(payload.fragments, probability_input=runtime_input.probability_input, ...)
history_fragments = tuple(fragment.prices for fragment in selection.fragments)
```

Pass `history_fragments` to `run_four_generator_ensemble(...)`.
Promote diagnostics:
- `prior_fragment_count`
- `prior_fragment_reason`
- `prior_fragment_sparse`
- `prior_fragment_ids`

- [ ] **Step 4: Wire GPU entrypoint**

Add:
```sh
PROBABILITY_FRAGMENTS_PATH="${POLYMARKET_PROBABILITY_FRAGMENTS_PATH:-/var/lib/polymarket/live/probability_fragments.json}"
--probability-fragments-path "$PROBABILITY_FRAGMENTS_PATH"
```

- [ ] **Step 5: Verify task**

Run:
```bash
uv run pytest tests/probability/test_gpu_worker.py tests/probability/test_ensemble_runtime.py -q
```

Expected: pass.

---

### Task 5: Runtime/API Fallback Uses Prior Fragments

**Files:**
- Modify: `src/polymarket_engine/probability/runtime.py`
- Modify: `src/polymarket_engine/runtime_api.py`
- Modify: `src/polymarket_engine/app.py`
- Test: `tests/probability/test_runtime.py`
- Test: `tests/test_runtime_api.py`

- [ ] **Step 1: Write failing runtime fallback test**

Add a test where `build_probability_payload(...)` receives a `probability_fragments_path` and monkeypatched ensemble records fragments. Assert hot-input fallback rows pass as-of-safe prior fragments and expose prior diagnostics.

- [ ] **Step 2: Run test and verify RED**

Run:
```bash
uv run pytest tests/probability/test_runtime.py::test_hot_input_fallback_uses_probability_fragments_prior -q
```

Expected: FAIL because runtime fallback does not accept fragment path.

- [ ] **Step 3: Implement runtime fragment path**

Add optional `probability_fragments_path` to:
- `ProbabilityRuntimeCache.payload(...)`
- `build_probability_payload(...)`
- runtime API router/app config

Use the same selection function as worker. If no snapshot exists, fall back to sparse baseline and expose reason.

- [ ] **Step 4: Verify task**

Run:
```bash
uv run pytest tests/probability/test_runtime.py tests/test_runtime_api.py -q
```

Expected: pass.

---

### Task 6: UI Shows Prior Status

**Files:**
- Modify: `ui/src/probabilityRows.ts`
- Modify: `ui/src/App.tsx`
- Test: `tests/ui/probability_value_test.ts`

- [ ] **Step 1: Write failing UI helper test**

Assert that rows with:
```ts
{
  prior_fragment_count: 12,
  prior_fragment_reason: "exact",
  prior_fragment_sparse: false
}
```
produce a visible prior summary.

- [ ] **Step 2: Run test and verify RED**

Run:
```bash
npx tsx tests/ui/probability_value_test.ts
```

Expected: FAIL because prior summary helper/display is missing.

- [ ] **Step 3: Implement UI display**

Add a compact line in the selected MC details:
- `prior_fragments`
- `prior_scope`
- `sparse`

Do not add a timer carousel.

- [ ] **Step 4: Verify task**

Run:
```bash
npx tsx tests/ui/probability_value_test.ts
npm run build --prefix ui
```

Expected: pass.

---

### Task 7: Full Verification

**Files:** all touched files.

- [ ] **Step 1: Run focused Python and UI checks**

Run:
```bash
uv run pytest tests/probability/test_generator_fragments.py tests/probability/test_ensemble_runtime.py tests/probability/test_gpu_worker.py tests/probability/test_runtime.py tests/features/test_rust_decision_snapshots.py tests/test_runtime_api.py tests/test_cli.py tests/scripts/test_deploy_script.py -q
uv run ruff check src/polymarket_engine/probability src/polymarket_engine/features/rust_decision_snapshots.py src/polymarket_engine/runtime_api.py src/polymarket_engine/app.py tests/probability tests/features/test_rust_decision_snapshots.py tests/test_runtime_api.py tests/test_cli.py tests/scripts/test_deploy_script.py
npx tsx tests/ui/probability_value_test.ts
npm run build --prefix ui
```

Expected: pass.

- [ ] **Step 2: Run deploy-script dry checks**

Run:
```bash
bash -n scripts/deploy.sh
bash -n scripts/deploy_pc.sh
bash -n deploy/gpu/gpu-probability-entrypoint.sh
bash -n deploy/normalizer/normalizer-entrypoint.sh
```

Expected: pass.

---

### Task 8: Curate and Clean Worktree

**Files:** entire repo.

- [ ] **Step 1: Review dirty files by lane**

Run:
```bash
git status --short --branch
git diff --stat
```

Expected: all dirty files classified as:
- intended prior-fragment/ensemble changes
- intended deployment split changes
- unrelated leftovers

- [ ] **Step 2: Stage intended files only**

Run `git add` only for files that are part of this plan or required deploy split support.

- [ ] **Step 3: Commit intended changes**

Run:
```bash
git commit -m "feat: use as-of prior fragments for ensemble probabilities"
```

Expected: commit succeeds.

- [ ] **Step 4: Stop for destructive cleanup approval if leftovers remain**

If `git status --short` still shows files, report the exact leftover file list and ask for approval before deleting, restoring, or stashing them.

- [ ] **Step 5: Make worktree pristine after approval**

Use the approved cleanup action only. Then run:
```bash
git status --short --branch
```

Expected: no dirty files.

---

### Task 9: Deploy and Live Verify

**Files:** deployment scripts and remote hosts.

- [ ] **Step 1: Deploy Spoon CPU authority**

Run the repo’s Spoon deploy path for collector/normalizer. Verify `probability_fragments.json` exists and is fresh on Spoon.

- [ ] **Step 2: Deploy THEPC GPU/API authority**

Run:
```bash
./scripts/deploy_pc.sh
```

Expected: script accepts clean tree, transfers exact `HEAD`, restarts services.

- [ ] **Step 3: Verify live prior-fragment probability surface**

Check:
```bash
ssh spoon 'ls -l /home/spoon/polymarket-data/live/probability_fragments.json && stat /home/spoon/polymarket-data/live/probability_fragments.json'
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "python3 - <<EOF\nimport json\np=\"/home/ender/polymarket-data/live/probabilities.json\"\nobj=json.load(open(p))\nrows=obj.get(\"rows\", [])\nprint(obj.get(\"state\"), len(rows))\nprint(rows[0].get(\"model_version\"), rows[0].get(\"generator_version\"), rows[0].get(\"prior_fragment_count\"), rows[0].get(\"path_diagnosis\"))\nEOF"'
```

Expected:
- fresh Spoon fragment snapshot
- THEPC rows show `ensemble-v1`
- `generator_version == four-generator-ensemble-v1`
- prior fragment fields present

---

## Risk Areas

- Dirty tree has unrelated deployment/runtime-keeper files; cleanup must be explicit and checkpointed.
- Live DuckDB may be locked; use snapshot artifacts where possible.
- Fragment sample size may be genuinely sparse. That is acceptable if surfaced as `SPARSE`; it is not acceptable to hide it.
- Deploy to THEPC depends on SSH/Tailscale/WSL availability and Docker image build time.

## Subagent Delegation

- Worker A: fragment snapshot contract and tests.
- Worker B: normalizer/CLI/entrypoint fragment writer.
- Worker C: GPU worker/runtime fallback fragment reader.
- Worker D: UI/API prior status display and tests.
- Controller: cleanup, final verification, commit/deploy decisions.

## Self-Review

- Spec coverage: 6.1 prior distribution, as-of safety, sparse behavior, UI visibility, cleanup, deploy all have tasks.
- Placeholder scan: no TODO/TBD placeholders.
- Type consistency: `ProbabilityFragmentsPayload`, `FragmentSelection`, `probability_fragments_path`, and `prior_fragment_*` are named consistently across tasks.
