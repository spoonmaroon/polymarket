# Spoon CPU Optimization Report

Date: 2026-06-02

Scope: `/Users/goon/polymarket` live collector and normalizer runtime on
`spoon`, with adjacent load from QuestDB and GEX Green. This report is for
later review and implementation. It does not authorize live trading, private
keys, or real order placement.

## Executive Summary

`spoon` is CPU-constrained, not memory-constrained. The host has 4 cores, and
runtime snapshots showed load averages above core count while RAM still had
roughly 15 GiB available.

The current CPU pressure comes from two separate classes of work:

1. Temporary deploy/build pressure: Docker deploys compile the Rust collector
   on `spoon`, producing multiple `rustc` workers at roughly 60-75 percent CPU
   each.
2. Steady-state runtime pressure: the Polymarket normalizer runs every 0.1
   seconds and frequently rebuilds current decision-state snapshots. Live logs
   showed normalizer cycles commonly in the 80-150 ms range, with some state
   rebuilds spiking close to 1 second.

The live collector itself is not the main CPU problem. The collector process was
around 6-9 percent CPU in later snapshots. The bigger Polymarket runtime cost
was the Python normalizer sidecar, and the broader host cost also included
QuestDB and GEX Green orderflow.

Recommended direction:

- Keep `spoon` as the always-on read-only truth collector.
- Stop doing expensive Rust builds on `spoon` during live operation.
- Relax or gate the normalizer loop so it does less work per second.
- Keep Monte Carlo and backtests on the PC or other burst compute.

## Observed Evidence

### Host Resource Snapshot

Earlier live checks showed:

```text
Host: spoon
Cores: 4
Load average: 7.02 / 6.71 / 5.68
Memory: 7.7 GiB used of 23 GiB
Memory available: about 15.9 GiB
Disk /: 265 GiB used of 437 GiB, 64 percent
```

Aggregate process grouping showed approximately:

```text
gex         176 percent CPU, 6.2 GiB RSS
polymarket   64 percent CPU, 0.5 GiB RSS
questdb      62 percent CPU, 1.0 GiB RSS
```

The host was therefore overcommitted on CPU but healthy on RAM and disk.

### Temporary Rust Build Spike

During a live Polymarket deploy, `spoon` was running:

```text
docker compose ... up -d --build collector normalizer
cargo build --release -p polymarket-live-probe
multiple rustc workers
```

Observed `rustc` workers were consuming roughly 60-75 percent CPU each. This is
expected for release builds, but it is a poor fit for a 4-core host that is also
running live collectors, GEX, and QuestDB.

This is a deployment strategy issue more than a runtime-code issue.

### Polymarket Steady-State Runtime

The normalizer entrypoint uses:

```sh
INTERVAL_SECONDS="${POLYMARKET_NORMALIZER_INTERVAL_SECONDS:-0.1}"
```

The Docker Compose default is:

```yaml
POLYMARKET_NORMALIZER_INTERVAL_SECONDS: ${POLYMARKET_NORMALIZER_INTERVAL_SECONDS:-0.1}
```

That means the normalizer wakes up 10 times per second unless overridden.

Later Docker stats showed:

```text
polymarket-rust-collector-collector-1     about 6 percent CPU
polymarket-rust-collector-normalizer-1  about 40-104 percent CPU
gex-green-questdb-1                      about 34-58 percent CPU
```

Normalizer logs showed examples such as:

```text
normalizer_cycle elapsed_ms=144 normalize_ms=45 state_ms=99 health_ms=0 health_skipped=true rows_read=6
normalizer_cycle elapsed_ms=341 normalize_ms=120 state_ms=221 health_ms=0 health_skipped=true rows_read=15
normalizer_cycle elapsed_ms=1009 normalize_ms=24 state_ms=984 health_ms=0 health_skipped=true rows_read=4
```

The important point: these cycles are not reading huge raw batches anymore.
Some cycles read only a few rows, yet state rebuild still dominates latency.

## Current Code Hotspots

### 1. Normalizer Cadence

File:

```text
deploy/normalizer/normalizer-entrypoint.sh
deploy/collector/docker-compose.yml
```

Issue:

The default normalizer interval is 0.1 seconds. That makes sense for aggressive
freshness, but it is expensive on a 4-core shared host.

Low-risk optimization:

Set `POLYMARKET_NORMALIZER_INTERVAL_SECONDS=0.25` first. If health remains
fresh, test `0.5`.

Expected result:

Less CPU spent polling, scanning signatures, parsing status, and rebuilding
state. Raw Rust collection and `status.json` freshness should remain fast
because those are collector-side, not normalizer-side.

Risk:

Normalized DuckDB rows and normalized health update slightly less often. This
should be acceptable if DuckDB is treated as replay/research state and the Rust
collector remains the live truth source.

### 2. Full Status Report Used As Normalizer Input

File:

```text
src/polymarket_engine/ingestion/rust_normalizer_sidecar.py
src/polymarket_engine/features/rust_decision_snapshots.py
```

Current behavior:

The sidecar reads the state-manager status payload and computes a state
signature from:

```text
current contracts
next contracts
orderbooks
chainlink_prices
prices
```

But the file being passed to the sidecar is the full ops/status report:

```text
/var/lib/polymarket/live/status.json
```

That full report also carries monitor, websocket, freshness, latency, and other
ops-only fields.

Existing implementation plan:

```text
docs/superpowers/plans/2026-06-02-status-state-sidecar-input.md
```

That plan proposes adding:

```text
/var/lib/polymarket/live/status_state.json
```

The compact file should contain only the normalizer state inputs:

```text
schema
generated timestamp
current contracts
next contracts
Chainlink prices
orderbooks
```

Expected result:

Reduced parse cost and cleaner rebuild gating. The full `status.json` remains
available for monitoring and diagnostics.

### 3. Current Decision-State Rebuild Cost

File:

```text
src/polymarket_engine/features/rust_decision_snapshots.py
```

The build path currently:

1. Reads status.
2. Parses prices and orderbooks.
3. Builds current and optional next contract specs.
4. Primes threshold prices.
5. Seeds latest prices.
6. Primes latest prices.
7. Primes price histories.
8. Seeds latest orderbooks.
9. Primes latest orderbooks.
10. Builds volatility snapshots.
11. Upserts contract specs.
12. Builds and writes `DecisionState` rows.

That is correct for replay safety, but expensive to run repeatedly at a 0.1
second cadence. The live logs show `state_ms` is now the biggest remaining
normalizer cost.

Optimization direction:

- Rebuild only when current-window state inputs change materially.
- Do not rebuild on ops-only status changes.
- Avoid writing duplicate `DecisionState` rows when the semantic state is
  unchanged.
- Keep a small in-process latest-price and latest-book cache.
- Treat DuckDB writes as replay/audit persistence, not the live decision hot
  path.

### 4. Deploy Builds On The Live Host

File:

```text
scripts/deploy.sh
deploy/collector/Dockerfile
```

Current deploy command builds images on `spoon`:

```text
docker compose ... up -d --build collector normalizer
```

The collector Dockerfile runs:

```text
cargo build --release -p polymarket-live-probe
```

Optimization direction:

- Build images on the PC or Mac.
- Push to a registry or transfer a saved image tarball.
- Make `spoon` pull/load and restart, not compile.
- If builds must remain on `spoon`, cap build parallelism with `CARGO_BUILD_JOBS=1`
  or run deploys only during off-hours.

Expected result:

Large temporary CPU spikes disappear from the always-on host.

## Recommended Implementation Plan

### Phase 0: Immediate No-Code Relief

Goal: reduce CPU risk without changing application code.

Steps:

1. Wait for the active Rust deploy/build to finish.
2. Set `POLYMARKET_NORMALIZER_INTERVAL_SECONDS=0.25` in the spoon collector env.
3. Restart only the normalizer container.
4. Verify:

```bash
ssh spoon 'cd /home/spoon/polymarket && python3 scripts/check_collector_status.py \
  --status-path /home/spoon/polymarket-data/live/status.json \
  --raw-root /home/spoon/polymarket-data/raw \
  --normalized-health-path /home/spoon/polymarket-data/live/normalized_health.json \
  --expected-prewarm-windows 2'
```

5. Watch one CPU snapshot and one normalizer log tail.

Success criteria:

```text
collector health passes
normalized health age remains below 30 seconds
normalizer CPU drops materially
collector CPU remains low
```

Rollback:

Set `POLYMARKET_NORMALIZER_INTERVAL_SECONDS=0.1` and restart the normalizer.

### Phase 1: Migrate Docker Rebuilds To The PC

Goal: make the PC the build worker and keep `spoon` as a low-power runtime host.

Target architecture:

```text
PC
  -> git checkout
  -> Docker BuildKit rebuilds collector and normalizer
  -> persistent Rust/Python/Docker layer caches
  -> image artifact export
  -> optional Monte Carlo batch compute
  -> sleep/shutdown after work

spoon
  -> load or pull prebuilt images
  -> docker compose up -d without --build
  -> collector/normalizer health verification
  -> no rustc/cargo during normal live operation
```

Preferred implementation:

1. Add a PC build script:

```text
scripts/build_images_pc.sh
```

Responsibilities:

- enable BuildKit;
- build `polymarket-rust-collector:<git-sha>`;
- build `polymarket-normalizer:<git-sha>`;
- tag both images with `latest`;
- save images to an artifact directory such as `dist/docker/`;
- print the image IDs and source git SHA.

2. Add a deploy-prebuilt script:

```text
scripts/deploy_prebuilt_images.sh
```

Responsibilities:

- copy image tarballs from PC to `spoon`, or use a registry tag;
- run `docker load` on `spoon` when tarballs are used;
- set `POLYMARKET_COLLECTOR_IMAGE` and `POLYMARKET_NORMALIZER_IMAGE`;
- run `docker compose up -d collector normalizer` without `--build`;
- run `scripts/check_collector_status.py` against the live data root.

3. Update `deploy/collector/docker-compose.yml` to support image overrides:

```yaml
collector:
  image: ${POLYMARKET_COLLECTOR_IMAGE:-polymarket-rust-collector:latest}
  build:
    context: ../..
    dockerfile: deploy/collector/Dockerfile

normalizer:
  image: ${POLYMARKET_NORMALIZER_IMAGE:-polymarket-normalizer:latest}
  build:
    context: ../..
    dockerfile: deploy/normalizer/Dockerfile
```

The `build:` blocks can remain for local fallback, but production deploy should
use image tags and skip `--build`.

4. Update `scripts/deploy.sh` with a mode switch:

```text
POLYMARKET_DEPLOY_USE_PREBUILT=1
```

When set, deploy must refuse to run `docker compose up --build`. It should only
load or use already-built images. If the image tag is missing on `spoon`, fail
fast instead of compiling.

5. Keep build-on-`spoon` as an explicit fallback only:

```text
POLYMARKET_DEPLOY_ALLOW_SPOON_BUILD=1
```

This should be off by default. If enabled, also set:

```text
CARGO_BUILD_JOBS=1
```

That makes emergency host builds slower but less hostile to live workloads.

Docker rebuild optimization:

- Keep the Rust Dockerfile using BuildKit cache mounts for cargo registry, git,
  and target directories.
- Keep dependency-copy layers before source-copy layers so most rebuilds reuse
  cached dependencies.
- Add a named local BuildKit cache on the PC if rebuilds still redownload crates.
- For the Python normalizer image, copy `pyproject.toml`, `README.md`, and lock
  files before copying `src/` so package install layers are reused when only
  source changes.
- Avoid `--no-cache` unless debugging a corrupt image.
- Prefer SHA-tagged images over mutable-only `latest` during deploy review.

Power-conservation plan:

1. Keep the PC off or asleep by default.
2. Wake it only for:
   - Docker image rebuilds;
   - Monte Carlo batches;
   - backtests or calibration sweeps.
3. Use a wrapper script that runs:

```text
pull latest repo -> build/test -> export images -> deploy to spoon -> verify -> sleep/shutdown
```

4. Cap PC Monte Carlo workers so it does not sit at full power longer than
   necessary. Prefer finishing a bounded batch and sleeping over running a
   permanent worker.

Minimum PC prerequisites:

```text
Docker Engine or Docker Desktop with BuildKit
git access to the private repo
ssh access to spoon
enough disk for Docker layer cache and image tarballs
same target architecture as spoon, or docker buildx configured for linux/amd64
```

Tests:

```bash
bash -n scripts/build_images_pc.sh
bash -n scripts/deploy_prebuilt_images.sh
bash -n scripts/deploy.sh
uv run pytest -q tests/scripts/test_deploy_script.py
```

Live verification:

```bash
ssh spoon 'docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Image}}" | grep polymarket'
ssh spoon 'ps -eo pcpu,args --sort=-pcpu | head -n 12'
ssh spoon 'pgrep -af "cargo build|rustc" || true'
```

Success criteria:

```text
PC builds both Docker images successfully
spoon loads or pulls the PC-built images
spoon deploy does not spawn cargo build or rustc
collector health passes after deploy
normalizer health passes after deploy
image tags or IDs are recorded with the deployed git SHA
```

Rollback:

Use the last known-good image tags on `spoon` and restart the compose stack.
Only use build-on-`spoon` fallback if the PC builder and image transfer path are
both unavailable.

### Phase 2: Implement Compact Status-State Input

Goal: decouple the normalizer from the full ops/status report.

Use existing plan:

```text
docs/superpowers/plans/2026-06-02-status-state-sidecar-input.md
```

Important files:

```text
rust/crates/polymarket-live-probe/src/report.rs
rust/crates/polymarket-live-probe/src/main.rs
deploy/collector/collector-entrypoint.sh
deploy/collector/docker-compose.yml
src/polymarket_engine/features/rust_decision_snapshots.py
src/polymarket_engine/ingestion/rust_normalizer_sidecar.py
```

Success criteria:

```text
full status.json still exists for monitor and diagnostics
new status_state.json exists for normalizer inputs
normalizer POLYMARKET_STATUS_PATH points at status_state.json
normalizer accepts both full and compact schemas for compatibility
CPU and state_ms improve
```

### Phase 3: Reduce State Rebuild Work

Goal: stop spending hundreds of milliseconds rebuilding state when only a tiny
or duplicate input changed.

Implementation ideas:

1. Add tests proving ops-only status changes do not trigger state rebuilds.
2. Persist or cache the last semantic state signature.
3. Skip `upsert_asof_state_inputs` when the generated state hash is identical.
4. Keep current-window latest price and orderbook caches hot across cycles.
5. Rebuild volatility less frequently than top-of-book updates, unless the
   Chainlink series changes enough to affect the snapshot.

Candidate tests:

```bash
uv run pytest -q tests/ingestion/test_rust_normalizer_sidecar.py
uv run pytest -q tests/features/test_rust_decision_snapshots.py
```

Success criteria:

```text
no future data leakage
current state still replay-safe
state_ms median drops materially
no health regressions
```

### Phase 4: Keep Monte Carlo Off Spoon

Goal: preserve low-power always-on collection while supporting heavy research.

Recommended architecture:

```text
spoon
  -> live Rust collector
  -> raw journals
  -> status files
  -> lightweight normalized/replay bridge

PC
  -> Docker image rebuilds
  -> Monte Carlo
  -> backtests
  -> calibration
  -> parameter sweeps
  -> power-managed burst worker
```

Implementation direction:

1. PC pulls or receives compact replay snapshots from `spoon`.
2. PC runs Monte Carlo and backtests in bounded batches.
3. PC writes compact probability artifacts back for review.
4. PC builds Docker images when code changes require a runtime deploy.
5. PC transfers or publishes images for `spoon`.
6. PC sleeps or shuts down after the batch.

Do not put Monte Carlo in the always-on `spoon` decision loop.

Suggested PC workflow:

```text
wake PC
git pull / fetch target branch
run focused tests
docker build collector and normalizer with cache
export or push SHA-tagged images
deploy prebuilt images to spoon
verify spoon health
run optional Monte Carlo/backtest batch
save artifacts
sleep/shutdown PC
```

Artifacts to preserve:

```text
dist/docker/polymarket-rust-collector-<sha>.tar
dist/docker/polymarket-normalizer-<sha>.tar
reports/generated/pc-monte-carlo-<timestamp>.json
reports/generated/pc-build-deploy-<timestamp>.txt
```

The PC should not own live truth. If the PC is asleep, `spoon` should continue
collecting raw data and writing status files.

## Priority Order

1. Change normalizer interval to `0.25` and measure.
2. Add PC Docker builder and prebuilt-image deploy path.
3. Implement compact `status_state.json`.
4. Optimize state rebuild gating.
5. Add PC burst-worker flow for Monte Carlo.

## Risks And Constraints

### Data Freshness Risk

Increasing the normalizer interval may make DuckDB rows less fresh. This should
not affect raw live collection if the Rust collector remains the source of truth.

Mitigation:

- Keep `status.json` freshness checks strict.
- Keep normalized health threshold at 30 seconds.
- Verify before and after any interval change.

### Replay Safety Risk

Skipping state writes too aggressively could create replay gaps.

Mitigation:

- Preserve append-only raw journals.
- Keep sampled hot-decision replay gates.
- Only skip writes when the semantic state hash is unchanged.

### Deployment Risk

Moving builds off-host adds image distribution complexity.

Mitigation:

- Keep build-on-host fallback.
- Start with manual `docker save` / `docker load` before adding a registry.
- Verify deployed image IDs and health after restart.
- Record source git SHA, image ID, and target host in each PC build artifact.
- Fail fast if `POLYMARKET_DEPLOY_USE_PREBUILT=1` is set but the image is
  missing on `spoon`.

### PC Power And Availability Risk

The PC may be asleep when a deploy or backtest is desired.

Mitigation:

- Keep `spoon` able to run the last known-good images indefinitely.
- Treat PC work as batch work, not always-on infrastructure.
- Use a manual wake/build/deploy/shutdown workflow first.
- Add Wake-on-LAN or scheduled wake only after the manual path is reliable.
- Never make live collection depend on the PC being awake.

### CPU Limit Risk

Hard CPU limits can make the normalizer fall behind.

Mitigation:

- Prefer cadence and gating changes first.
- If using Docker CPU limits, start with soft limits and monitor normalized
  health age.

## Open Questions

1. What is the required normalized DuckDB freshness target: under 1 second, under
   5 seconds, or simply under the 30 second health threshold?
2. Should `spoon` ever compile Rust during live market hours?
3. Should the PC receive raw journals, DuckDB snapshots, or compact replay
   exports for Monte Carlo?
4. Is QuestDB required for this Polymarket path, or is that load entirely from
   GEX and separable?
5. Is the PC running Linux directly, Docker Desktop, or WSL2? This determines
   whether `buildx` is required for `linux/amd64` images.
6. Should the first PC-to-spoon image transfer use `docker save` over SSH or a
   private registry?

## Final Recommendation

Treat `spoon` as a low-power collector and truth host, not as a build machine or
simulation server. The first implementation should be conservative:

```text
normalizer interval 0.25
PC-built Docker images
prebuilt-image deploys to spoon
compact status_state.json
state rebuild gating
PC burst worker for Monte Carlo
```

That path reduces CPU without weakening the replay-safety boundary.
