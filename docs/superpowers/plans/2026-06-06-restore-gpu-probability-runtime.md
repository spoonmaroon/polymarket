# Restore GPU Probability Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the CUDA/GPU probability worker and event-log path while keeping hot probability inputs as a fallback/diagnostic lane so the Runtime Monitor graphs populate again.

**Architecture:** The GPU worker remains the authority for graphable Monte Carlo rows via `probabilities.json` and `probability-events.jsonl`. Hot inputs remain available for fast CPU fallback, but an empty hot-input snapshot must never mask populated GPU/status rows. The browser consumes the same runtime monitor UI and keeps last graphable CUDA rows through empty/partial refreshes.

**Tech Stack:** Python/FastAPI, DuckDB, Docker Compose, CUDA worker image, React/Vite, TypeScript UI helper tests, pytest.

---

### Task 1: Restore Runtime GPU/Event Path And Keep Hot Inputs

**Files:**
- Modify: `src/polymarket_engine/runtime_api.py`
- Modify: `src/polymarket_engine/probability/runtime.py`
- Modify: `src/polymarket_engine/cli.py`
- Modify: `deploy/collector/docker-compose.yml`
- Modify: `scripts/deploy_pc.sh`
- Restore if missing: `deploy/gpu/Dockerfile`
- Restore if missing: `deploy/gpu/gpu-probability-entrypoint.sh`
- Restore if missing: `src/polymarket_engine/probability/gpu_worker.py`
- Restore if missing: `src/polymarket_engine/probability/cuda_monte_carlo.py`
- Restore if missing: `src/polymarket_engine/probability/event_log.py`
- Restore if missing: `src/polymarket_engine/probability/grid_cache.py`
- Keep: `src/polymarket_engine/probability/hot_inputs.py`
- Keep: `src/polymarket_engine/probability/runtime_inputs.py`
- Test: `tests/test_runtime_api.py`
- Test: `tests/probability/test_hot_inputs.py`
- Restore relevant GPU tests from `7e22240` or `9250f3f` if needed.

- [ ] Write a failing API regression where `probability_inputs.json` exists with `inputs: []` and `skipped: 4`, while `probabilities.json` exists with one graphable row. `/api/runtime/probabilities` must return the status row, not the empty hot-input payload.
- [ ] Write a failing event-stream regression where `probability-events.jsonl` is missing but a newest `.drain` file exists. `/api/runtime/probability-events/stream?max_events=1` must emit the drain event.
- [ ] Restore the probability event stream route and helpers from `9250f3f`, including newest-drain fallback.
- [ ] Restore the GPU worker/deploy service from the CUDA branch. Keep `POLYMARKET_PROBABILITY_INPUTS_PATH` on normalizer/API, but do not remove the GPU worker.
- [ ] Update probability API selection order to prefer non-empty GPU/status rows before hot-input rows. Hot inputs may be returned only when they have rows, or when no GPU/status/event data is available.
- [ ] Run `uv run pytest -q tests/test_runtime_api.py tests/probability/test_hot_inputs.py` and record the result.

### Task 2: Restore Runtime Monitor Graph Retention

**Files:**
- Modify: `ui/src/App.tsx`
- Restore/modify: `ui/src/probabilityRows.ts`
- Restore/modify: `ui/src/marketRows.ts`
- Modify: `ui/src/styles.css` only if needed for restored graph panels.
- Test: `tests/ui/probability_rows_test.ts`
- Test: `tests/ui/probability_state_test.ts`
- Test: `tests/ui/test_probability_rows_helper.py`
- Test: `tests/ui/test_probability_state_helper.py`

- [ ] Restore the probability row helper module from the branch where CUDA rows and graph retention worked.
- [ ] Add or restore a UI helper regression proving an empty hot-input/refresh payload does not wipe previously graphable CUDA rows.
- [ ] Add or restore a UI helper regression proving streamed probability rows merge into the existing payload and remain selectable by contract side.
- [ ] Ensure `App.tsx` treats `simulation_preview`, `path_count`, `cache_status`, `p_hat`, and `p_finish` rows as graphable.
- [ ] Run `uv run pytest -q tests/ui/test_probability_rows_helper.py tests/ui/test_probability_state_helper.py` and `cd ui && npm run build` and record the result.

### Task 3: Integrate, Deploy, And Browser Verify

**Files:**
- Modify only as needed after Tasks 1 and 2.

- [ ] Run focused integration checks: `uv run pytest -q tests/test_runtime_api.py tests/probability/test_hot_inputs.py tests/ui/test_probability_rows_helper.py tests/ui/test_probability_state_helper.py`.
- [ ] Build the UI bundle with `cd ui && npm run build`.
- [ ] Deploy to THEPC with the repo-supported PC deploy path.
- [ ] Verify `curl 'http://127.0.0.1:8000/api/runtime/probabilities?limit=24'` returns non-empty graphable rows or a non-empty status/event fallback.
- [ ] Verify `curl --max-time 3 'http://127.0.0.1:8000/api/runtime/probability-events/stream?limit=4&max_events=1&interval_ms=100'` is not 404.
- [ ] Use the in-app Browser on `http://127.0.0.1:8000/` to confirm MC rows and graphs render.
