# Live Polymarket Runtime Audit Handoff

As of: 2026-06-05 21:35 CDT / 2026-06-06 02:35 UTC

Scope: live THEPC runtime check over SSH, focused on collection, Monte Carlo/probability publication, decision/trading boundary, TUI/API visibility, and current handoff risks.

## Relevance Update

Checked: 2026-06-05 23:22 CDT / 2026-06-06 04:22 UTC

This handoff is still relevant for the probability publication diagnosis: the GPU worker can produce CUDA Monte Carlo rows, but the UI/API can briefly show NOWCAST-only because the worker publishes an immediate nowcast status before the CUDA pass finishes. The deployed fix preserves still-valid MC rows during that interim NOWCAST publication and exposes `retained_mc_rows` / `previous_mc_retained` diagnostics.

The Docker/runtime identity details below are partly stale after the Docker Desktop migration. The active Docker engine is now Docker Desktop's WSL backend, with the collector, normalizer, API, and GPU probability worker running there. The old line saying `docker-desktop stopped` should be treated as historical evidence from this audit, not current state.

Operational follow-up from this check: visible Windows scheduled tasks can spawn terminal windows while keeping the stack alive. `PolymarketEngineStart`, `PolymarketEngineWatchdog`, and `PolymarketWslKeepalive` were moved to hidden `wscript.exe` launchers and marked `Hidden=true`.

Deployment result: THEPC is running `polymarket-cuda-probability:5d1e4bf6a`. Live `/api/runtime/probabilities?limit=24` was observed returning `state=NOWCAST`, `rows=4`, `nowcast_rows=4`, `lanes={"MC":4,"NOWCAST":4}`, `retained_mc_rows=4`, and `previous_mc_retained=true`, which confirms the UI/API should not blank the MC graph during the fast-nowcast interval.

## Executive Summary

The live stack is up and collecting. THEPC is reachable over Tailscale at `ender@100.72.104.49`, WSL Ubuntu is running, and the compose stack has four containers up: collector, normalizer, API, and GPU probability worker.

Collector/runtime health is good at the time of inspection: `/api/runtime/status` returned `ok: true`, `/api/runtime/gates` returned no failures, and the live status/health files were sub-second fresh.

CUDA is available and being used by the GPU worker. The GPU container sees one `NVIDIA GeForce RTX 5060 Ti`, CuPy `14.1.1`, CUDA `13.2`, and the worker process is running `run-cuda-probability-worker`.

The main problem is probability publication, not raw collection or GPU availability. The GPU worker can produce MC rows with `model_version: cached-grid-v1`, `path_count: 120000`, and roughly `83ms` average GPU runtime in sampled logs. But the same worker also frequently overwrites `probabilities.json` with empty or nowcast-only payloads. At the time of inspection the live `/api/runtime/probabilities?limit=24` response had no MC rows and only four nowcast rows. A few seconds earlier and later, logs showed repeated `rows_seen: 0`, `rows_written: 0`, `skipped: 4` payloads.

There is still no evidence of live trading or order placement in the deployed runtime checked here. The live DB health shows `features.decision_snapshots` at `0` rows. The previous local source audit also found no wallet/private-key/order-placement surface in source/deploy.

## Live Target

SSH target:

```text
ender@100.72.104.49
```

Windows host:

```text
COMPUTERNAME=THEPC
USER=ender
DATE=2026-06-05T21:32:44.9088779-05:00
```

WSL state:

```text
Ubuntu running, WSL2
docker-desktop stopped
```

WSL identity:

```text
host=thePC
user=ender
cwd=/mnt/c/Users/ender
date=2026-06-05T21:33:37-05:00
```

## Deployment Identity

Compose stack:

```text
project=polymarket-rust-collector
working_dir=/home/ender/polymarket/deploy/collector
config_files=/home/ender/polymarket/deploy/collector/docker-compose.yml
```

Remote repo:

```text
path=/home/ender/polymarket
branch=codex/stabilize-tui-layout
commit=c0bef717f
```

Live data dir:

```text
/home/ender/polymarket-data
```

Containers:

```text
polymarket-rust-collector-api-1                      Up 26 minutes (healthy), 0.0.0.0:8000->8000
polymarket-rust-collector-normalizer-1               Up 26 minutes (healthy)
polymarket-rust-collector-gpu-probability-worker-1   Up 26 minutes
polymarket-rust-collector-collector-1                Up 26 minutes (healthy)
```

Images:

```text
api:                    polymarket-normalizer:c0bef717fd5a
normalizer:             polymarket-normalizer:c0bef717fd5a
gpu-probability-worker: polymarket-cuda-probability:fef0a7a6a822
collector:              polymarket-rust-collector:fef0a7a6a822
```

## Live Files

At inspection time:

```text
/home/ender/polymarket-data/db/polymarket.duckdb              6.2 GB
/home/ender/polymarket-data/live/status.json                  fresh, ~33-36 KB
/home/ender/polymarket-data/live/normalized_health.json       fresh, ~1.1 KB
/home/ender/polymarket-data/live/probabilities.json           fresh, often 374 bytes when empty
/home/ender/polymarket-data/live/probability_inputs.json      fresh, 2.7 KB
/home/ender/polymarket-data/live/outcomes.json                fresh, ~629 KB
/home/ender/polymarket-data/live/volatility.json              fresh, ~749 bytes
```

Important mismatch: `probability-events.jsonl` was not present in `/home/ender/polymarket-data/live` during the later check, even though earlier deployment notes expected that file and the API route is wired to `/var/lib/polymarket/live/probability-events.jsonl`.

## API Health

Health endpoint:

```text
GET /health -> {"status": "ok"}
```

Runtime status:

```text
GET /api/runtime/status
ok=true
state=OK
schema_kind=rust-live-probe-state-manager-v1
mode=state-manager
age_ms=26 in one sample
prices=2
orderbooks=8
current=2
next=2
websocket_status=2
health_flags=[]
```

WebSocket status sample:

```text
polymarket_rtds_chainlink: Connected, reconnect_count=0, active_token_count=2
polymarket_clob_market_ws: Connected, reconnect_count=6, active_token_count=8
```

Runtime gates:

```text
GET /api/runtime/gates
ok=true
failures=[]
status age ~0.06s in sample
normalized_health age ~0.38s in sample
```

Outcomes:

```text
GET /api/runtime/outcomes?limit=4
ok=true
rows present
recent 5m BTC/ETH rows returned
pending/resolved official status surfaced
```

Volatility:

```text
GET /api/runtime/live?limit=4 includes volatility
volatility.json ok=true state=OK rows=2
BTC sigma_tau sample=0.0003199603624739746
ETH sigma_tau sample=0.0005709222884262055
```

## Normalized Storage Health

`/api/runtime/normalized-health` reported:

```text
core.contracts:                 1,904 rows, latest 2026-06-06T02:30:06.697658+00:00
core.contract_rules:            0 rows
core.price_ticks:               271,853 rows, latest 2026-06-06T02:34:16.874636+00:00
core.orderbook_snapshots:       4,691,380 rows, latest 2026-06-06T02:34:13.702314+00:00
features.asof_state_inputs:     1,162,216 rows, latest 2026-06-06T02:34:17.351795+00:00
features.decision_snapshots:    0 rows
features.probability_outputs:   50,996 rows, latest 2026-06-05T13:41:03.299182+00:00
features.probability_event_log: 87,784 rows, latest 2026-06-06T02:34:06.494605+00:00
features.simulation_artifacts:  0 rows
validation.market_outcome_history: 948 rows, latest 2026-06-06T02:34:13.917725+00:00
```

Interpretation:

- Collection and normalization are live.
- `features.probability_outputs` is stale relative to the current runtime. The active CUDA worker is publishing status/event style payloads, not updating that old table as the primary current surface.
- `features.probability_event_log` is live, but direct read-only DuckDB inspection from another process failed because the normalizer owns the live DuckDB lock.

DuckDB lock evidence:

```text
connect failed IOException IO Error:
Could not set lock on file "/var/lib/polymarket/db/polymarket.duckdb":
Conflicting lock is held in /usr/local/bin/python3.14 (PID 7).
```

Use disposable DB/WAL snapshots for deeper DB analysis.

## Probability/CUDA Configuration

API env:

```text
POLYMARKET_ENABLE_RUNTIME_PROBABILITIES=0
POLYMARKET_PROBABILITY_STATUS_PATH=/var/lib/polymarket/live/probabilities.json
```

Remote API behavior at commit `c0bef717f`:

```python
if not enable_runtime_probabilities:
    if probability_status_path.exists():
        return _probability_status_payload(...)
    return _probabilities_disabled_payload()
```

So the flag is misleading in the current deployed code: with the flag off, the API still serves the status file if it exists.

Normalizer env:

```text
POLYMARKET_NORMALIZER_ENABLE_PROBABILITIES=0
POLYMARKET_NORMALIZER_INTERVAL_SECONDS=1.0
```

Normalizer logs confirm:

```text
probability_outputs_written=0
probability_events_drained=0/4/8/16 depending on cycle
```

GPU worker env:

```text
POLYMARKET_CUDA_PROBABILITY_INTERVAL_SECONDS=1.0
POLYMARKET_CUDA_PROBABILITY_LIMIT=24
POLYMARKET_CUDA_PROBABILITY_VALID_SECONDS=30
POLYMARKET_DUCKDB_PATH=/var/lib/polymarket/db/polymarket.duckdb
POLYMARKET_PROBABILITY_STATUS_PATH=/var/lib/polymarket/live/probabilities.json
```

GPU worker process:

```text
/usr/bin/python3 /usr/local/bin/polymarket-engine run-cuda-probability-worker \
  --duckdb-path /var/lib/polymarket/db/polymarket.duckdb \
  --probability-status-path /var/lib/polymarket/live/probabilities.json \
  --probability-inputs-path /var/lib/polymarket/live/probability_inputs.json \
  --interval-seconds 1.0 \
  --limit 24 \
  --valid-seconds 30 \
  --max-input-snapshot-age-seconds 10.0
```

GPU/CUDA evidence:

```text
Host GPU: NVIDIA GeForce RTX 5060 Ti
Driver: 595.79
CUDA: 13.2
GPU memory: 2077 MiB / 16311 MiB in one sample
GPU util: 10% in one sample
Container CuPy: 14.1.1
Container CUDA devices: 1
Container device_name: b'NVIDIA GeForce RTX 5060 Ti'
```

## Probability Inputs

`probability_inputs.json` was healthy at `2026-06-06T02:35:22.254873+00:00`:

```text
schema_version=polymarket-probability-inputs-v1
ok=true
state=OK
rows=4
skipped=0
```

Rows:

```text
BTC 5m UP:   flags=["OK"], seconds_left=277.996182, executable_price=0.52, sigma_tau=0.0015974093851706523, z_path=0.12307936577498627
BTC 5m DOWN: flags=["OK"], seconds_left=277.996182, executable_price=0.52, sigma_tau=0.0015974093851706523, z_path=-0.12307936577498627
ETH 5m UP:   flags=["OK"], seconds_left=277.996182, executable_price=0.41, sigma_tau=0.002065437665039072, z_path=0.07097530300958496
ETH 5m DOWN: flags=["OK"], seconds_left=277.996182, executable_price=0.60, sigma_tau=0.002065437665039072, z_path=-0.07097530300958496
```

This rules out "no probability inputs exist" as the broad failure.

## Current Probability Surface

One API sample at `2026-06-06T02:35:21.903378+00:00`:

```text
GET /api/runtime/probabilities?limit=24
ok=true
state=NOWCAST
rows=[]
nowcast_rows=4
rows_seen=4
rows_written=0
skipped=0
lanes={"NOWCAST": 4}
avg_runtime_ms=0.0
avg_total_lag_ms=860.564
max_total_lag_ms=860.564
```

Nowcast rows existed for BTC/ETH UP/DOWN, all with `flags=["OK"]` and `model_version=fast-nowcast-v1`.

Another sample immediately before showed the worse empty state:

```text
GET /api/runtime/probabilities?limit=4
ok=true
state=OK
rows=[]
nowcast_rows=[]
rows_seen=0
rows_written=0
skipped=4
lanes={}
latency all null
model_version=null
```

Live `probabilities.json` was often only `374` bytes in this state.

## MC Rows Are Being Produced, But Not Retained Reliably

The GPU worker logs showed successful MC publication snapshots, for example:

```text
generated_at=2026-06-06T02:34:01.185536+00:00
lanes={"MC": 2, "NOWCAST": 2}
avg_runtime_ms=83.09
max_runtime_ms=166.179
avg_total_lag_ms=359.289
max_total_lag_ms=448.403
model_version=cached-grid-v1
```

Example MC row details from logs:

```text
contract=BTC 5m UP
probability_kind=MC
backend=cuda
model_version=cached-grid-v1
generator_version=cuda-lognormal-chainlink-sigma-batch-v1
path_count=120000
paths_per_seed=30000
seed_count=4
cache_status=REFRESH
valid_until=2026-06-06T02:34:31.185536+00:00
runtime_ms=166.179
total_lag_ms=448.403
p_finish=1.0
p_no_touch=1.0
decision_hint=DEMAND_MORE_EDGE
gate_reasons=["INSUFFICIENT_EDGE"]
```

But those useful MC payloads are surrounded by repeated empty writes:

```text
rows=[]
nowcast_rows=[]
rows_seen=0
rows_written=0
skipped=4
lanes={}
model_version=null
```

Practical effect: the user-visible `/api/runtime/probabilities` endpoint and the TUI can miss MC rows even while the GPU worker is healthy, because a later empty/nowcast-only worker tick overwrites the last useful `probabilities.json`.

## Probability Event Surface

`GET /api/runtime/probability-events?limit=4` returned 404.

`GET /api/runtime/probability-events/stream?limit=4&max_events=1` returned SSE data:

```text
event: probability
schema_version=polymarket-probability-events-v1
ok=true
state=OK
event_source=status
events=[...]
```

The stream can emit recent rows from the current status fallback. It is not currently backed by a visible live `probability-events.jsonl` file in `/home/ender/polymarket-data/live`.

Handoff implication: if the UI/TUI is expected to recover rows through event history, the durable event file/path needs verification. Right now the easiest user-visible surface is still `probabilities.json`, and that file is vulnerable to empty overwrites.

## Decision and Trading Boundary

Live health reported:

```text
features.decision_snapshots: 0 rows
```

No live trading/order-placement environment was observed in the compose env inspected here.

Prior local source audit found:

- no wallet/private-key/order-placement/cancel/balance path in source/deploy;
- the only `BUY`-looking path was a synthetic no-auth order-latency probe using a fake payload and HTTP GET;
- current decision logic is readiness/snapshot/gate logic, not live execution.

Current live state is therefore still read-only/research/paper-surface oriented.

## TUI/UI Implications

The Rust TUI polls:

```text
/api/runtime/live
/api/runtime/probabilities
/api/runtime/outcomes
```

Given the live API samples above, the TUI can show:

- fresh live prices/books/outcomes;
- volatility rows;
- probability pending/empty rows when `probabilities.json` has just been overwritten by an empty status;
- nowcast rows only when the worker publishes `state=NOWCAST`;
- MC rows only during windows when the file currently contains MC rows.

The local Mac branch now has an uncommitted TUI/API boundary fix from the earlier audit:

- API compute fallback is separately gated by `POLYMARKET_ALLOW_RUNTIME_PROBABILITY_COMPUTE`;
- TUI renders empty probability runtime states explicitly.

Those local changes are not deployed on THEPC. The deployed remote commit is `c0bef717f`.

## Root-Cause Hypothesis

Most likely root cause:

The GPU probability worker writes every tick to the single canonical `probabilities.json` file. When the current tick has no MC rows, or only nowcast rows, it replaces the last useful MC payload instead of preserving valid MC rows until their `valid_until` or merging lanes.

Evidence:

- Probability inputs are present and `OK`.
- CUDA/CuPy/GPU are available.
- Logs show successful MC rows with `path_count=120000`.
- Logs also show repeated empty payloads with `skipped=4`.
- Current API samples often expose those empty or nowcast-only payloads.
- Live `probabilities.json` shrinks to `374` bytes during empty state.

Secondary issue:

The probability event path is confusing. The API has only an SSE stream route; no normal GET route exists. The live `probability-events.jsonl` file was absent during inspection, even though normalized health reports a live `features.probability_event_log` table.

## Recommended Handoff Actions

1. Fix GPU worker publication semantics.

   Do not overwrite `probabilities.json` with an empty MC payload while prior MC rows are still valid. Preserve last valid MC rows until `valid_until`, then separately mark health/input state. Merge `MC` and `NOWCAST` lanes instead of letting one erase the other.

2. Add explicit status diagnostics.

   Add fields such as:

   ```text
   last_mc_generated_at
   last_mc_rows_written
   last_nowcast_generated_at
   last_empty_generated_at
   empty_reason
   input_snapshot_age_seconds
   skip_reasons_by_contract
   previous_mc_retained=true/false
   ```

3. Add a non-stream event endpoint or wire TUI to the stream.

   `/api/runtime/probability-events?limit=N` currently 404s. Either implement it or make the TUI consume `/probability-events/stream` deliberately.

4. Verify durable event-file handling.

   Confirm whether `probability-events.jsonl` should exist in `/var/lib/polymarket/live`. If not, update docs and API naming. If yes, fix the writer/mount/drain path.

5. Clarify runtime probability flags.

   Current deployed API serves `probabilities.json` even with `POLYMARKET_ENABLE_RUNTIME_PROBABILITIES=0`. Either set the env flag to match behavior, rename the flag, or change the code so display/read fallback has its own explicit flag.

6. Validate after patch with a 2-minute visibility test.

   Suggested check:

   ```bash
   for i in $(seq 1 120); do
     curl -fsS 'http://127.0.0.1:8000/api/runtime/probabilities?limit=24' |
       jq -c '{generated_at,state,rows:(.rows|length),nowcast:(.nowcast_rows|length),rows_seen,rows_written,skipped,lanes,latency}'
     sleep 0.5
   done
   ```

   Passing condition: once MC rows exist, they do not disappear from the display payload while still within their valid window.

7. Use DB/WAL snapshots for deeper table analysis.

   Direct read-only DuckDB access is blocked by the normalizer lock. Snapshot the DB and WAL before querying `features.probability_event_log`.

## Commands Used

Host identity:

```bash
ssh ender@100.72.104.49 'powershell -NoProfile -Command "...; wsl.exe -l -v"'
```

WSL compose status:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "docker ps --format ..."' 
```

Runtime API checks:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/api/runtime/status
curl -fsS http://127.0.0.1:8000/api/runtime/gates
curl -fsS http://127.0.0.1:8000/api/runtime/normalized-health
curl -fsS 'http://127.0.0.1:8000/api/runtime/probabilities?limit=24'
curl -fsS 'http://127.0.0.1:8000/api/runtime/probability-events/stream?limit=4&max_events=1'
```

GPU checks:

```bash
nvidia-smi
docker exec polymarket-rust-collector-gpu-probability-worker-1 python3 -c 'import cupy as cp; print(cp.__version__, cp.cuda.runtime.getDeviceCount())'
```

Logs:

```bash
docker logs --tail 80 polymarket-rust-collector-gpu-probability-worker-1
docker logs --since 5m --tail 80 polymarket-rust-collector-api-1
docker logs --since 5m --tail 80 polymarket-rust-collector-normalizer-1
```

## Bottom Line

Collector and live runtime health are good.

CUDA is installed and the GPU probability worker is actually generating MC rows.

The handoff bug is probability visibility/persistence: the current single-file status writer lets empty or nowcast-only ticks wipe the user-visible MC rows. Fix that before pushing this toward decision gates or claiming the probability UI is stable.
