# MC Overconfidence Fragment Priors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop uncalibrated live historical fragments from driving overconfident Monte Carlo headline probabilities by default while preserving an explicit research opt-in path.

**Architecture:** The GPU probability worker will keep accepting and syncing `probability_fragments.json`, but live MC will not pass those fragments into `run_four_generator_ensemble()` unless a new opt-in flag is enabled. Disabled live priors will be visible in row diagnostics so the browser UI/TUI can distinguish "no priors by design" from stale or missing fragments.

**Tech Stack:** Python 3.11, pytest, ruff, argparse CLI, POSIX shell entrypoints, Docker Compose env files.

---

## Root Cause Evidence

Live rollover sampling reproduced the bug: first MC rows in a fresh 5-minute contract reached `p_finish` near `0.96-0.99` even when `z_path` was only about `0.45-1.2`. The row diagnostics were already warning with `path_diagnosis="FRAGILE"` and `mc_dispersion` near `1.0`, but headline `p_finish` still used only the core non-stress generators.

The code path causing the overconfidence is:

- `src/polymarket_engine/probability/gpu_worker.py:842-893` loads live fragments and passes them as `history_fragments` on every MC call.
- `src/polymarket_engine/probability/path_generators.py:269-359` uses those fragments directly for `empirical_conditional`, uses the same fragment pool for `block_bootstrap`, and makes `filtered_historical` a renamed copy of `empirical_conditional`.
- `src/polymarket_engine/probability/ensemble_outputs.py:138-157` excludes `stress_overlay` from headline terminal probabilities, so stress only affects risk-adjusted diagnostics.

This means recent one-way live fragments can dominate three core generators, while the stress disagreement is visible but not applied to headline `p_finish`.

## Files

- Modify: `src/polymarket_engine/probability/gpu_worker.py`
  - Add `use_prior_fragments` to `ProbabilityWorkerBudget`, default `False`.
  - Do not load or pass live prior fragments unless the flag is true.
  - Emit `prior_fragment_enabled` and `prior_fragment_reason` diagnostics.
- Modify: `src/polymarket_engine/cli.py`
  - Add `--use-prior-fragments`.
  - Pass the parsed flag into `ProbabilityWorkerBudget`.
- Modify: `deploy/gpu/gpu-probability-entrypoint.sh`
  - Add `POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS`, default `0`.
  - Append `--use-prior-fragments` only for truthy env values.
- Modify: `deploy/collector/docker-compose.yml`
  - Pass `POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS` into `api` and `gpu-probability-worker`.
- Modify: `deploy/collector/.env.example`
  - Document `POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS=0`.
- Modify: `scripts/deploy_pc.sh`
  - Preserve the default disabled setting during THEPC deploy.
- Modify: `src/polymarket_engine/probability/runtime.py`
  - Keep hot-input/runtime fallback prior fragments default-off.
  - Filter persisted probability reads by prior-fragment mode.
  - Include prior-fragment mode/reason/ids in probability output IDs.
- Modify: `src/polymarket_engine/runtime_api.py`
  - Thread API fallback prior-fragment opt-in into runtime cache reads.
- Modify: `src/polymarket_engine/app.py`
  - Read `POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS` for API fallback opt-in.
- Modify: `tests/probability/test_gpu_worker.py`
  - Add a failing default-off test.
  - Update the current prior-fragment test to opt in.
- Modify: `tests/probability/test_runtime.py`
  - Add runtime fallback default-off, opt-in, output-id, and persisted-read mode tests.
- Modify: `tests/test_runtime_api.py`
  - Add API default-off and env opt-in tests.
- Modify: `tests/test_cli.py`
  - Assert CLI default false and flag true.
- Modify: `tests/scripts/test_deploy_script.py`
  - Assert entrypoint, compose API/GPU sections, env example, and THEPC deploy script all keep live prior fragments disabled by default.

## Risky Areas

- Do not delete or stop writing `probability_fragments.json`; it is still useful for diagnostics and future calibration.
- Do not change `stress_overlay` headline weighting in this patch. That is a methodology change and belongs in the next MC calibration plan.
- Do not mark disabled priors as sparse. Sparse should mean "wanted priors but did not have enough", not "live priors intentionally disabled".
- Keep the opt-in name explicit: this is for uncalibrated live historical priors, not for all Monte Carlo paths.
- Persisted probability reads must stay mode-aware; default-off reads must not surface previously persisted opt-in rows.

## Subagent Split

- Subagent 1: `gpu_worker.py` and `tests/probability/test_gpu_worker.py`.
- Subagent 2: CLI, shell entrypoint, compose/env, `deploy_pc.sh`, and their tests.
- Main agent: review both diffs, run the combined test slice, deploy to THEPC after approval, then verify live rows after a rollover.

### Task 1: Add Worker Red Test For Default-Off Live Priors

**Files:**
- Modify: `tests/probability/test_gpu_worker.py`

- [x] **Step 1: Add a failing test before the existing prior-fragment test**

Add this test above `test_worker_passes_asof_safe_fragments_into_ensemble`:

```python
def test_worker_does_not_feed_prior_fragments_without_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asof_ts = datetime.now(UTC)
    probability_status_path = tmp_path / "probabilities.json"
    probability_inputs_path = tmp_path / "probability_inputs.json"
    probability_fragments_path = tmp_path / "probability_fragments.json"
    _write_ready_recovery_status(probability_status_path.with_name("recovery_status.json"))
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": PROBABILITY_INPUTS_SCHEMA_VERSION,
                "generated_at": asof_ts.isoformat(),
                "rows": [
                    _runtime_input_snapshot_row(
                        asof_ts=asof_ts,
                        state_id="state-btc-up",
                        asset="BTC",
                        side="UP",
                        seconds_left=240.0,
                    )
                ],
                "skipped": 0,
            }
        ),
        encoding="utf-8",
    )
    prior_fragment = GeneratorFragment(
        fragment_id="btc-prior-one",
        asset="BTC",
        asof_ts=asof_ts - timedelta(seconds=20),
        prices=(70_000.0, 70_050.0, 70_100.0),
        horizon_seconds=300,
        z_path_bucket="near",
        quality_bucket="OK",
    )
    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker._load_probability_fragments",
        lambda **_: ((prior_fragment,), None),
    )
    seen_history: list[tuple[tuple[float, ...], ...] | None] = []

    def fake_ensemble_output(
        input_row: ProbabilityInput,
        *,
        path_count: int,
        steps: int,
        seed: int,
        history_fragments: tuple[tuple[float, ...], ...] | None = None,
    ) -> ProbabilityOutput:
        seen_history.append(history_fragments)
        return ProbabilityOutput(
            state_id=input_row.state_id,
            asof_ts=input_row.asof_ts,
            p_finish=0.56,
            p_no_touch=0.51,
            z_path=input_row.z_path,
            model_version="ensemble-v1",
            seed=seed,
            diagnostics={
                "model": "ensemble-v1",
                "generator_version": "four-generator-ensemble-v1",
                "path_count": path_count,
                "steps": steps,
                "effective_weights": {},
                "generator_summary": {},
                "generator_runs": [],
                "effective_generator_values": {},
                "u_gen": 0.01,
                "mc_dispersion": 0.02,
                "uncertainty_buffer": 0.02,
                "path_diagnosis": "CLEAN",
                "sparse_scope": False,
            },
        )

    monkeypatch.setattr(
        "polymarket_engine.probability.gpu_worker.run_four_generator_ensemble",
        fake_ensemble_output,
    )

    payload = run_cuda_probability_worker_cycle(
        duckdb_path=tmp_path / "unused.duckdb",
        probability_status_path=probability_status_path,
        probability_inputs_path=probability_inputs_path,
        probability_fragments_path=probability_fragments_path,
        probability_event_path=tmp_path / "probability-events.jsonl",
        budget=ProbabilityWorkerBudget(max_total_paths=30_000),
    )

    assert seen_history == [None]
    row = payload["rows"][0]
    assert row["prior_fragment_enabled"] is False
    assert row["prior_fragment_count"] == 0
    assert row["prior_fragment_reason"] == "disabled_uncalibrated_live_prior"
    assert row["prior_fragment_sparse"] is False
    assert row["prior_fragment_ids"] == []
    assert row["path_diagnosis"] == "CLEAN"
```

- [x] **Step 2: Run the red test**

Run:

```bash
uv run pytest tests/probability/test_gpu_worker.py::test_worker_does_not_feed_prior_fragments_without_opt_in -q
```

Expected: FAIL because `seen_history` contains the prior fragment and `prior_fragment_enabled` is missing.

### Task 2: Implement Worker Default-Off Prior Fragments

**Files:**
- Modify: `src/polymarket_engine/probability/gpu_worker.py`
- Modify: `tests/probability/test_gpu_worker.py`

- [x] **Step 1: Add the budget flag**

In `ProbabilityWorkerBudget`, add:

```python
    use_prior_fragments: bool = False
```

- [x] **Step 2: Guard fragment loading**

Replace the unconditional fragment load in `run_cuda_probability_worker_cycle` with:

```python
    prior_fragments: tuple[GeneratorFragment, ...] = ()
    prior_fragment_error: str | None = None
    if budget.use_prior_fragments:
        prior_fragments, prior_fragment_error = _load_probability_fragments(
            path=probability_fragments_path,
            max_age_seconds=max_input_snapshot_age_seconds,
        )
```

- [x] **Step 3: Pass the opt-in flag into selection and diagnostics**

Change the selection call to:

```python
                fragment_selection = _select_prior_fragments(
                    fragments=prior_fragments,
                    probability_input=runtime_input.probability_input,
                    max_fragment_count=min(budget.fragment_max_rows, path_count),
                    fragment_error=prior_fragment_error,
                    enabled=budget.use_prior_fragments,
                )
```

Change the diagnostic wrapper call to:

```python
                    _output_with_prior_diagnostics(
                        output,
                        fragment_selection=fragment_selection,
                        fragment_error=prior_fragment_error,
                        prior_fragments_enabled=budget.use_prior_fragments,
                    )
```

- [x] **Step 4: Update `_select_prior_fragments`**

Change the signature and disabled branch to:

```python
def _select_prior_fragments(
    *,
    fragments: Sequence[GeneratorFragment],
    probability_input: ProbabilityInput,
    max_fragment_count: int,
    fragment_error: str | None,
    enabled: bool,
) -> FragmentSelection:
    if not enabled:
        return FragmentSelection(
            fragments=(),
            sparse=False,
            reason="disabled_uncalibrated_live_prior",
        )
    if fragment_error is not None:
        return FragmentSelection(fragments=(), sparse=True, reason="unavailable")
    return select_fragments_for_input(
        fragments,
        probability_input=probability_input,
        min_fragment_count=DEFAULT_MIN_FRAGMENT_COUNT,
        max_fragment_count=max(1, max_fragment_count),
    )
```

- [x] **Step 5: Update `_output_with_prior_diagnostics`**

Change the signature and diagnostics update to:

```python
def _output_with_prior_diagnostics(
    output: ProbabilityOutput,
    *,
    fragment_selection: FragmentSelection,
    fragment_error: str | None,
    prior_fragments_enabled: bool,
) -> ProbabilityOutput:
    diagnostics = dict(output.diagnostics)
    diagnostics.update(
        {
            "prior_fragment_enabled": prior_fragments_enabled,
            "prior_fragment_count": len(fragment_selection.fragments),
            "prior_fragment_reason": fragment_selection.reason,
            "prior_fragment_sparse": fragment_selection.sparse,
            "prior_fragment_ids": [
                fragment.fragment_id for fragment in fragment_selection.fragments
            ],
        }
    )
```

Keep the existing sparse and error handling after this block.

- [x] **Step 6: Update the existing opt-in test**

Rename `test_worker_passes_asof_safe_fragments_into_ensemble` to:

```python
def test_worker_passes_asof_safe_fragments_into_ensemble_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
```

Change its budget to:

```python
        budget=ProbabilityWorkerBudget(max_total_paths=30_000, use_prior_fragments=True),
```

Add:

```python
    assert row["prior_fragment_enabled"] is True
```

- [x] **Step 7: Run worker tests**

Run:

```bash
uv run pytest \
  tests/probability/test_gpu_worker.py::test_worker_does_not_feed_prior_fragments_without_opt_in \
  tests/probability/test_gpu_worker.py::test_worker_passes_asof_safe_fragments_into_ensemble_when_enabled \
  -q
```

Expected: both tests PASS.

### Task 3: Add CLI And Deploy Opt-In Wiring

**Files:**
- Modify: `src/polymarket_engine/cli.py`
- Modify: `deploy/gpu/gpu-probability-entrypoint.sh`
- Modify: `deploy/collector/docker-compose.yml`
- Modify: `deploy/collector/.env.example`
- Modify: `scripts/deploy_pc.sh`
- Modify: `tests/test_cli.py`
- Modify: `tests/scripts/test_deploy_script.py`

- [x] **Step 1: Add CLI parser test expectations**

In `test_parse_run_cuda_probability_worker_defaults`, add:

```python
    assert args.use_prior_fragments is False
```

Add this test below it:

```python
def test_parse_run_cuda_probability_worker_use_prior_fragments_arg() -> None:
    args = parse_args(
        [
            "run-cuda-probability-worker",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--use-prior-fragments",
        ]
    )

    assert args.use_prior_fragments is True
```

- [x] **Step 2: Add CLI flag and budget mapping**

In `src/polymarket_engine/cli.py`, add parser wiring after `--fragment-max-rows`:

```python
    cuda_probability_worker.add_argument(
        "--use-prior-fragments",
        action="store_true",
        help="Opt into uncalibrated live probability fragment priors for research runs.",
    )
```

In `_run_cuda_probability_worker`, pass:

```python
        use_prior_fragments=args.use_prior_fragments,
```

- [x] **Step 3: Make the shell entrypoint append the flag only when enabled**

In `deploy/gpu/gpu-probability-entrypoint.sh`, add:

```sh
ENABLE_LIVE_PRIOR_FRAGMENTS="${POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS:-0}"
```

Replace the final `exec polymarket-engine ...` command with:

```sh
set -- polymarket-engine run-cuda-probability-worker \
  --duckdb-path "$DB_PATH" \
  --probability-status-path "$PROBABILITY_STATUS_PATH" \
  --recovery-status-path "$RECOVERY_STATUS_PATH" \
  --offload-status-path "$OFFLOAD_STATUS_PATH" \
  --probability-inputs-path "$PROBABILITY_INPUTS_PATH" \
  --probability-fragments-path "$PROBABILITY_FRAGMENTS_PATH" \
  --interval-seconds "$INTERVAL_SECONDS" \
  --limit "$LIMIT" \
  --valid-seconds "$VALID_SECONDS" \
  --max-input-snapshot-age-seconds "$MAX_INPUT_SNAPSHOT_AGE_SECONDS" \
  --worker-mode "$WORKER_MODE" \
  --generator-policy "$GENERATOR_POLICY" \
  --cpu-target-percent "$CPU_TARGET_PERCENT" \
  --cpu-soft-max-percent "$CPU_SOFT_MAX_PERCENT" \
  --max-rss-mb "$MAX_RSS_MB" \
  --max-cycle-runtime-ms "$MAX_CYCLE_RUNTIME_MS" \
  --max-total-paths "$MAX_TOTAL_PATHS" \
  --min-total-paths "$MIN_TOTAL_PATHS" \
  --sustained-breach-cycles "$SUSTAINED_BREACH_CYCLES" \
  --fragment-max-rows "$FRAGMENT_MAX_ROWS" \
  --cpu-threads "$CPU_THREADS"

case "$ENABLE_LIVE_PRIOR_FRAGMENTS" in
  1|true|TRUE|yes|YES|on|ON)
    set -- "$@" --use-prior-fragments
    ;;
esac

exec "$@"
```

- [x] **Step 4: Add compose and env defaults**

In `deploy/collector/docker-compose.yml`, under `gpu-probability-worker.environment`, add:

```yaml
      POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS: ${POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS:-0}
```

In `deploy/collector/.env.example`, near the probability worker variables, add:

```dotenv
POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS=0
```

- [x] **Step 5: Add THEPC deploy default**

In `scripts/deploy_pc.sh`, define:

```sh
PC_ENABLE_LIVE_PRIOR_FRAGMENTS="${PC_ENABLE_LIVE_PRIOR_FRAGMENTS:-0}"
```

Then set it in the remote `.env` block:

```sh
set_env POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS "$PC_ENABLE_LIVE_PRIOR_FRAGMENTS" deploy/collector/.env
```

- [x] **Step 6: Add deploy script tests**

Add this test to `tests/scripts/test_deploy_script.py`:

```python
def test_gpu_probability_prior_fragments_default_to_disabled() -> None:
    env_example = (ROOT / "deploy" / "collector" / ".env.example").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    entrypoint = (ROOT / "deploy" / "gpu" / "gpu-probability-entrypoint.sh").read_text(
        encoding="utf-8"
    )
    deploy_pc = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert "POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS=0" in env_example
    assert (
        "POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS: "
        "${POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS:-0}"
    ) in compose
    assert (
        'ENABLE_LIVE_PRIOR_FRAGMENTS="${POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS:-0}"'
        in entrypoint
    )
    assert "--use-prior-fragments" in entrypoint
    assert 'PC_ENABLE_LIVE_PRIOR_FRAGMENTS="${PC_ENABLE_LIVE_PRIOR_FRAGMENTS:-0}"' in deploy_pc
    assert "set_env POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS" in deploy_pc
```

- [x] **Step 7: Run CLI and deploy tests**

Run:

```bash
uv run pytest tests/test_cli.py::test_parse_run_cuda_probability_worker_defaults \
  tests/test_cli.py::test_parse_run_cuda_probability_worker_use_prior_fragments_arg \
  tests/scripts/test_deploy_script.py::test_gpu_probability_prior_fragments_default_to_disabled \
  -q
```

Expected: all tests PASS.

### Task 4: Verification, Commit, Deploy, And Live Check

**Files:**
- Verify only unless tests force small follow-up edits.

- [x] **Step 1: Run the focused test slice**

Run:

```bash
uv run pytest tests/probability/test_gpu_worker.py tests/test_cli.py tests/scripts/test_deploy_script.py -q
```

Expected: PASS.

- [x] **Step 2: Run ruff on touched Python files**

Run:

```bash
uv run ruff check src/polymarket_engine/probability/gpu_worker.py src/polymarket_engine/cli.py tests/probability/test_gpu_worker.py tests/test_cli.py tests/scripts/test_deploy_script.py
```

Expected: `All checks passed!`

- [x] **Step 3: Commit**

Run:

```bash
git status --short
git add src/polymarket_engine/probability/gpu_worker.py src/polymarket_engine/cli.py deploy/gpu/gpu-probability-entrypoint.sh deploy/collector/docker-compose.yml deploy/collector/.env.example scripts/deploy_pc.sh tests/probability/test_gpu_worker.py tests/test_cli.py tests/scripts/test_deploy_script.py docs/superpowers/plans/2026-06-14-mc-overconfidence-fragment-priors.md
git commit -m "fix: disable live MC fragment priors by default"
```

Expected: commit succeeds.

- [x] **Step 4: Deploy to THEPC**

Run:

```bash
./scripts/deploy_pc.sh
```

Expected: API and `gpu-probability-worker` containers are updated and the deploy smoke passes.

- [x] **Step 5: Verify live worker command and diagnostics on THEPC**

Run:

```bash
ssh -o ConnectTimeout=8 ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -lc 'cd /home/ender/polymarket && docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml -f deploy/collector/docker-compose.thepc-gpu-api.yml exec -T gpu-probability-worker sh -lc \"tr \\0 \\n < /proc/1/cmdline | grep -E -- --use-prior-fragments || true\"'"
```

Expected: no `--use-prior-fragments` output unless the env is intentionally set to `1`.

Run:

```bash
ssh -o ConnectTimeout=8 ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -lc 'curl -fsS http://127.0.0.1:8000/api/runtime/probabilities?limit=8 | python3 -m json.tool | grep -E \"prior_fragment_enabled|prior_fragment_reason|p_finish|z_path\" | head -40'"
```

Expected: live MC rows include `prior_fragment_enabled: false` and `prior_fragment_reason: disabled_uncalibrated_live_prior`. Fresh-contract MC should no longer jump to `0.96-0.99` solely because recent fragments trended one way.

### Task 5: Review-Driven Runtime/API Prior Guard

**Files:**
- Modify: `src/polymarket_engine/probability/runtime.py`
- Modify: `src/polymarket_engine/runtime_api.py`
- Modify: `src/polymarket_engine/app.py`
- Modify: `deploy/collector/docker-compose.yml`
- Test: `tests/probability/test_runtime.py`
- Test: `tests/test_runtime_api.py`
- Test: `tests/scripts/test_deploy_script.py`

- [x] **Step 1: Add runtime fallback default-off regression**

Add `test_hot_input_fallback_does_not_use_probability_fragments_without_opt_in` so a hot-input fallback with a fragments file present does not load fragments, passes `history_fragments=None`, and emits disabled prior diagnostics.

- [x] **Step 2: Preserve explicit runtime opt-in**

Rename/update the existing hot-input fragment-prior test to `test_hot_input_fallback_uses_probability_fragments_prior_when_enabled` and call `build_probability_payload(..., use_prior_fragments=True)`.

- [x] **Step 3: Split output IDs by prior mode**

Add `test_prior_fragment_mode_changes_probability_output_id` and include `prior_fragment_enabled`, `prior_fragment_reason`, and `prior_fragment_ids` in `_output_id()`.

- [x] **Step 4: Filter persisted reads by prior mode**

Add `test_persisted_probability_rows_are_filtered_by_prior_fragment_mode` and make `latest_probability_output_rows()` filter `features.probability_outputs.output_json` by `diagnostics.prior_fragment_enabled`. Treat missing legacy diagnostics with prior evidence (`prior_fragment_count > 0` or non-empty `prior_fragment_ids`) as prior-enabled/unsafe for default-off reads.

- [x] **Step 5: Wire API env opt-in**

Add API tests for default-off and `POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS=1`, then thread `use_prior_fragments` through `create_app()`, `create_app_from_env()`, `build_runtime_router()`, and both runtime cache calls.

- [x] **Step 6: Verify deploy env coverage**

Assert `POLYMARKET_ENABLE_LIVE_PRIOR_FRAGMENTS` is defaulted to `0` in `.env.example`, THEPC deploy env, the API compose section, and the GPU worker compose section.

## Follow-Up Plan After This Fix

Create a separate MC calibration plan for:

- Replacing `filtered_historical` so it is not a duplicate of `empirical_conditional`.
- Selecting fragments by time-to-expiry bucket, side, volatility regime, z bucket, and realized outcome.
- Producing calibration reports by asset, side, TTE bucket, z bucket, and volatility regime.
- Re-enabling live prior fragments only after a calibration gate shows they improve Brier/log-loss without early-contract overconfidence.
