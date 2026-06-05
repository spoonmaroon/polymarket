# Probability Engine Audit - 2026-06-05

## Scope and sources

This audit compares the current probability and Monte Carlo implementation against:

- `docs/BINARY_CONTRACT_ENGINE_PLAN.md`, with emphasis on sections 5-6, volatility, probability outputs, and implementation status.
- `graphify-out/converted/BTC_Binary_Path_Probability_Incomplete_Research_Paper_d182482a.md`, with emphasis on sections 4-7 and formula/provenance notes.
- Current code in `src/polymarket_engine/probability/schema.py`, `monte_carlo.py`, `empirical_prior.py`, and `runtime.py`.
- The probability-output write path in `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`.
- `docs/reports/monte-carlo-backend-benchmark-2026-06-05.md`.

No deployment status is inferred here.

## What is implemented and aligned

- The schema has the core plan/paper outputs: `p_finish`, `p_no_touch`, `z_path`, and `sigma_tau` through `ProbabilityInput` and `ProbabilityOutput`. The output object validates finite probabilities, UTC timestamps, strict JSON diagnostics, model version, and deterministic seed metadata.
- `ProbabilityInput.from_decision_state` enforces basic replay safety by rejecting state timestamps after `asof_ts` for threshold, settlement, and book fields. It also blocks rows with data-quality flags and requires positive finite `sigma_tau`.
- `z_path` is implemented as side-aware signed log distance divided by `sigma_tau`. For DOWN contracts the sign is flipped, matching the plan's "cushion from danger line" intent.
- `score_paths` directly estimates `p_finish` and `p_no_touch` from simulated path indicators. Terminal wins count final settlement-side success; no-touch wins require every path price to remain on the winning side.
- `run_seeded_monte_carlo` provides a deterministic offline lognormal baseline scaled by `sigma_tau`. It validates generated paths after simulation, so non-finite or nonpositive prices are rejected before scoring.
- `run_empirical_conditional_monte_carlo` implements a first empirical conditional prior slice. It filters to Chainlink-only ticks, excludes any tick with `event_ts > asof_ts` or `observed_ts > asof_ts`, samples historical fragments, rescales them to current `sigma_tau`, and emits diagnostics for bucket size, future-tick exclusions, non-Chainlink exclusions, and fallback level.
- The empirical prior uses Chainlink source key `polymarket_rtds_chainlink` and asset-specific symbols `BTC/USD` or `ETH/USD`. That matches the plan's Chainlink-only volatility/source rule for settlement-reference probability inputs.
- Runtime code can compute and persist cached probability outputs into `features.probability_outputs`. The sidecar then writes a probability status payload with latest rows, skipped count, and errors. TUI/API consumers can read cached outputs instead of recomputing Monte Carlo on every display refresh.
- Native runtime default is `POLYMARKET_PROBABILITY_GENERATOR=lognormal` and `POLYMARKET_PROBABILITY_BACKEND=cpu_rayon`. If the Rust native module is unavailable, it falls back to Python NumPy while preserving output semantics.

## Gaps/risks against the engine plan and paper

- The live default is still lognormal, not the paper's primary as-of walk-forward empirical Monte Carlo. The empirical conditional prior exists, but it is opt-in via `POLYMARKET_PROBABILITY_GENERATOR=empirical_conditional`.
- The empirical prior is only a first slice of G1. It does not yet condition on horizon buckets, seconds-left buckets, `z_path` buckets, volatility regime/trend, wick frequency, threshold-cross behavior, source-quality state beyond Chainlink filtering, or event windows.
- Sparse empirical buckets currently fall back to lognormal. The plan/paper also call for coarser bucket fallback, increased uncertainty, or blocking; the current fallback records diagnostics but does not itself apply a decision block or uncertainty buffer.
- Multiple path generators are not implemented as an ensemble. There is no G2 block bootstrap, G3 filtered historical simulation as a separate generator, G4 stress overlay, generator weights, ensemble `p_finish`/`p_no_touch`, or generator-dispersion uncertainty.
- Browser visualization for all four path generators is not ready to be truthful yet. The UI can display cached `features.probability_outputs` now, but it cannot show four live generator lanes until G2-G4 emit real outputs or explicit placeholder states.
- `p_no_touch` is calculated correctly as a path-survival indicator for simulated paths, but there is no implemented decision layer that uses weak `p_no_touch` to wait, demand more edge, block, or reduce size.
- `z_path` is computed and persisted, but there is no implemented minimum `z_path` gate or refresh trigger tied to `z_path` bucket changes.
- `sigma_tau` is consumed by the probability engine, but this audit did not verify the upstream volatility builder beyond the plan status. The engine should continue treating missing or suspect Chainlink volatility as missing confidence, not substituting proxy feeds.
- Cached probability outputs are persisted and surfaced, but the plan's cached probability grid behavior is not fully present. Current runtime computes latest active rows and caches payloads for a short interval; it does not maintain reusable grids by asset/side/horizon/time/`z_path`/volatility buckets.
- Formula provenance is partly respected: Monte Carlo sample averages are standard estimators, while `z_path`, `sigma_tau`, executable edge, and risk buffers are project-defined. The current implementation should keep exposing diagnostics because these project-defined quantities still need validation.

## Live-runtime wiring notes

- `build_probability_payload` first returns persisted latest probability rows when available. If none exist, it derives latest `ProbabilityInput` rows from `features.asof_state_inputs`, computes outputs, persists them, and returns the computed rows.
- `latest_probability_inputs_from_connection` skips rows with data-quality flags before probability calculation. It also supports active-contract and max-state-age filters.
- `_compute_probability_outputs` in the Rust normalizer sidecar computes active probability outputs after state inputs are built, writes a probability runtime status file, and reports probability-output errors without claiming trading authority.
- `compute_and_persist_probability_outputs` writes one deterministic output per latest active state using a seed derived from `state_id` and `asof_ts`.
- Default runtime path is `lognormal` generator plus `cpu_rayon` backend. `python_numpy` is an explicit or fallback backend. CUDA is not accepted by `run_native_or_python` in the current Python runtime path.
- The benchmark report supports CPU as the live default: for `live-small` 8192 paths x 64 steps, `cpu_rayon` averaged 2.488 ms while CUDA averaged 61.179 ms due to cold/context overhead. CUDA only beat CPU in the large 1,000,000 path sweep case, and the report recommends CPU for live cached probability runs unless warm-cache CUDA benchmarks beat CPU.

## Recommendation for current deployment defaults

- For THEPC read-only shadow display, enable cached runtime probabilities and set `POLYMARKET_PROBABILITY_GENERATOR=empirical_conditional`. This makes the TUI/API show the empirical prior when buckets are available and the explicit lognormal fallback when they are sparse.
- Keep `POLYMARKET_PROBABILITY_BACKEND=cpu_rayon` as the live hot-path backend for native/lognormal runs unless a fresh benchmark proves CUDA is faster for the configured live path count and steps.
- Do not make CUDA the live default yet. Use CUDA only for large offline sweeps or visual/research batches after a warm-cache benchmark proves it is faster for that workload.
- Keep empirical conditional prior as read-only shadow output until bucket conditioning, sparse-bucket policy, generator dispersion, and calibration are validated.
- Treat probability outputs as research/shadow artifacts, not trading authority. `p_finish` can anchor fair value, but `p_no_touch`, `z_path`, Chainlink/source quality, executable price, and uncertainty gates still need a decision layer before any production trading claim.
- Preserve the Chainlink-only settlement/volatility rule for `sigma_tau` and empirical fragments. Proxy feeds should remain quality checks, not volatility or prior inputs.
