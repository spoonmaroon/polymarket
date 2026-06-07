# Runtime Keeper Verification Report

Date (UTC): 2026-06-07 02:41:34Z

## Scope

Verification for the runtime keeper portion of:

- `docs/superpowers/plans/2026-06-06-polymarket-runtime-keeper.md`
- `docs/superpowers/plans/2026-06-06-cpu-budgeted-active-active-failsafe.mdm`

## Local checks

- `uv run pytest tests/cluster tests/ops/test_runtime_keeper.py tests/probability/test_gpu_worker.py tests/scripts/test_runtime_keeper_scripts.py tests/scripts/test_deploy_script.py::test_compose_and_env_support_prebuilt_image_overrides tests/docs/test_active_runtime_docs.py tests/test_cli.py::test_parse_run_cuda_probability_worker_defaults tests/test_cli.py::test_parse_runtime_keeper_args tests/test_cli.py::test_parse_sync_cluster_artifacts_args tests/test_cli.py::test_sync_cluster_artifacts_dry_run_prints_rsync_commands tests/test_cli.py::test_sync_cluster_artifacts_execute_runs_rsync_commands tests/test_runtime_api.py::test_probability_events_payload_tails_large_event_file_without_full_read tests/test_runtime_api.py::test_probability_events_payload_reuses_unchanged_event_file tests/test_runtime_api.py::test_probability_events_stream_reads_newest_drain_when_jsonl_missing -q`
  - Result: pass (`42 passed, 1 warning in 0.38s`).
- `uv run ruff check src/polymarket_engine/cluster src/polymarket_engine/ops src/polymarket_engine/probability/generator_fragments.py src/polymarket_engine/probability/gpu_worker.py src/polymarket_engine/runtime_api.py src/polymarket_engine/cli.py tests/cluster tests/ops tests/probability/test_gpu_worker.py tests/scripts/test_runtime_keeper_scripts.py tests/scripts/test_deploy_script.py tests/docs/test_active_runtime_docs.py tests/test_cli.py tests/test_runtime_api.py`
  - Result: `All checks passed!`.
- `uv run pytest tests/scripts/test_runtime_keeper_scripts.py -q`
  - Result after installer provisioning patch: pass (`2 passed in 0.01s`).

## THEPC deploy

- Synced runtime/failsafe files to `/home/ender/polymarket`.
- Rebuilt and recreated THEPC services with:

```bash
DOCKER_CONFIG=/tmp/polymarket-docker-config docker compose \
  --env-file deploy/collector/.env \
  -f deploy/collector/docker-compose.yml \
  up -d --build api gpu-probability-worker
```

- Result: images built and containers started:
  - `api`
  - `collector`
  - `gpu-probability-worker`
  - `normalizer`
  - `outcome-refresh`

## THEPC live API checks

- `/health`
  - Result: status `200`, elapsed `0.685s`.
- `/api/runtime/live?limit=8`
  - Result: `prices=2`, `orderbooks=8`, `price_assets=['BTC/USD', 'ETH/USD']`, `book_assets=['BTC', 'ETH']`, `status_age_ms=315`.
- `/api/runtime/probabilities?limit=4`
  - Result: status `200`, `ok=true`, `state=NOWCAST`, `rows=4`, `probability_assets=['BTC', 'ETH']`.
- `/api/runtime/probability-events/stream?limit=4&max_events=1`
  - Result: status `200`, elapsed `0.004s`.
- `/home/ender/polymarket-data/live/probabilities.json`
  - Result: `ok=true`, `state=NOWCAST`, `rows=4`.
  - Budget: `max_total_paths=120000`, `path_budget_per_input=30000`, `cpu_target_percent=20.0`, `max_rss_mb=512`, `max_cycle_runtime_ms=750`, `cycle_runtime_breached=false`.

## THEPC runtime keeper

- Installer:

```bash
./scripts/install_thepc_runtime_keeper.sh
```

- Result:
  - Installed `/home/ender/bin/polymarket-runtime-keeper-loop.sh`.
  - Installed `/mnt/c/Users/ender/polymarket-runtime-keeper.ps1`.
  - Registered scheduled task `Polymarket Runtime Keeper`.
  - Provisioned host CLI at `/home/ender/.local/bin/polymarket-engine`.

- Manual keeper run:

```bash
/home/ender/.local/bin/polymarket-engine runtime-keeper \
  --repo /home/ender/polymarket \
  --data-dir /home/ender/polymarket-data \
  --api-base-url http://127.0.0.1:8000
```

- Result: `ok=true`.
- Checks:
  - `docker:info`: ok.
  - `compose:collector`: ok.
  - `compose:normalizer`: ok.
  - `compose:outcome-refresh`: ok.
  - `compose:api`: ok.
  - `container:polymarket-rust-collector-gpu-probability-worker-1`: ok.
  - `api:/health`: ok.
  - `api:/`: ok.
  - `api:/api/runtime/live`: ok.
  - `api:/api/runtime/probabilities`: ok.

## Notes

- The first runtime keeper attempt failed because THEPC WSL did not have the host
  console script. The installer now provisions the editable package with
  `python3 -m pip install --user --break-system-packages -e "$REPO"` and the loop
  uses `$HOME/.local/bin/polymarket-engine` when present.
- The TUI stale BTC/ETH/order-book/probability symptom was caused by the API
  polling path reading the full large probability event log. The live event
  stream now tails the file and returns in milliseconds.
