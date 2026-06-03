# Polymarket System Report

Date: 2026-06-02

Scope: local `/Users/goon/polymarket` state, live Spoon runtime, raw Rust
journals, normalized DuckDB health, latency artifacts, and Sections 1-4
readiness.

Inspection window: approximately 2026-06-02 21:34-21:37 UTC.

## Verdict

The Spoon live collector is up, healthy, and producing current read-only data.
The Rust state-manager is the live truth source. It writes fresh
`status.json`, Chainlink/CLOB raw JSONL journals, and hot `DecisionState`
JSONL rows. The normalizer sidecar is also healthy and keeping normalized
DuckDB health current.

The system is live-current for BTC/ETH 5-minute current and next windows. It is
not live-current for 15-minute windows. It is not probability-ready as live
authority. Probability outputs and decision snapshots remain empty by design.

The main current gaps are:

1. Local repo state is conflicted and cannot be treated as a clean development
   base.
2. Spoon is configured with `POLYMARKET_PREWARM_WINDOWS=3`, but live
   `status.json` had `next_next` empty, and the stricter state-manager verifier
   failed on missing next-next BTC/ETH assets.
3. A hot replay proof artifact exists and passed, but its watermarks are from
   2026-06-02 08:56 UTC, not the current 21:35 UTC live window.
4. Direct read-only DuckDB inspection was blocked by the live normalizer writer
   lock, so current replay-table evidence comes from
   `normalized_health.json`.

## Local Repo State

| Item | Current value |
|---|---|
| Branch | `codex/add-rust-ci-gates` |
| Upstream | `origin/main` |
| HEAD | `658238c Prepare replay-safe Monte Carlo readiness (#19)` |
| Local status | dirty with unresolved merge entries |
| Untracked report | `reports/spoon-cpu-optimization-report-2026-06-02.md` |

Unmerged files:

| File |
|---|
| `rust/crates/polymarket-live-probe/src/book_state.rs` |
| `rust/crates/polymarket-live-probe/src/main.rs` |
| `rust/crates/polymarket-live-probe/src/polymarket.rs` |
| `rust/crates/polymarket-live-probe/src/prices.rs` |

Other modified files include `.github/workflows/tests.yml` and several Rust
collector modules. I did not resolve or revert anything.

## Spoon Repo And Containers

| Item | Current value |
|---|---|
| Repo path | `/home/spoon/polymarket` |
| Branch | `main` |
| HEAD | `c4f4e7e Default normalizer to measured 100ms cadence` |
| Git status | `main...origin/main [ahead 174, behind 1]` |
| Collector container | `polymarket-rust-collector-collector-1`, healthy |
| Normalizer container | `polymarket-rust-collector-normalizer-1`, healthy |
| Data root | `/home/spoon/polymarket-data` |

Runtime env confirms the intended active scope:

| Env | Value |
|---|---|
| `POLYMARKET_ASSETS` | `BTC,ETH` |
| `POLYMARKET_INTERVAL` | `5m` |
| `POLYMARKET_PREWARM_WINDOWS` | `3` |
| `POLYMARKET_STATUS_INTERVAL_MS` | `100` |
| `POLYMARKET_STALE_CHAINLINK_AFTER_MS` | `5000` |
| `POLYMARKET_STALE_ORDERBOOK_AFTER_MS` | `30000` |
| `POLYMARKET_STATE_SNAPSHOT_INTERVAL_MS` | `5000` |

Host pressure was acceptable during inspection:

| Metric | Value |
|---|---:|
| Load average | `1.72 / 1.70 / 1.76` |
| RAM | `7.3 GiB used / 23 GiB total`, `16 GiB available` |
| Data-root filesystem | `267G used / 437G total`, `64%` |
| Collector CPU | `9.16%` |
| Normalizer CPU | `62.83%` |

## Data Path Architecture

```text
Gamma/Polymarket metadata
  -> Rust state manager contract discovery
  -> Chainlink RTDS WebSocket
  -> Polymarket CLOB WebSocket
  -> in-memory hot DecisionState builder
  -> hot decision JSONL journal
  -> raw JSONL journals and state snapshots
  -> DuckDB normalizer
  -> as-of replay/state builder
  -> future probability outputs
```

REST remains discovery and backup. WebSockets are the hot live path. DuckDB is
the replay and research layer, not the live decision hot path.

## Live Status Evidence

Collector health check passed:

| Metric | Value |
|---|---:|
| `status_age_seconds` | `0.072` |
| `price_age_ms` | `1109` |
| `orderbook_age_ms` | `211` |

Live `status.json` sample:

| Field | Value |
|---|---|
| `generated_at` | `2026-06-02T21:36:13.891201376Z` |
| Schema | `rust-live-probe-state-manager-v1` |
| Health flags | `[]` |
| Current windows | BTC/ETH 5m, 2 tokens each |
| Next windows | BTC/ETH 5m, 2 tokens each |
| Next-next windows | empty |
| Orderbooks | 12 |

Chainlink freshness:

| Asset | Price | Event age | Observed age |
|---|---:|---:|---:|
| BTC/USD | `67729.5816940518` | `1925.7 ms` | `339.6 ms` |
| ETH/USD | `1903.91968` | `1925.7 ms` | `339.6 ms` |

Order-book freshness:

| Metric | Value |
|---|---:|
| Min observed age | `47.0 ms` |
| Median observed age | `2284.1 ms` |
| Max observed age | `13432.3 ms` |

WebSocket status:

| Source | State | Reconnects | Subscriptions | Active tokens | Last event age |
|---|---|---:|---:|---:|---:|
| `polymarket_rtds_chainlink` | connected | 0 | 1 | 2 | `305 ms` |
| `polymarket_clob_market_ws` | connected | 1 | 12 | 12 | `12 ms` |

Latency marks:

| Mark | Value |
|---|---:|
| `chainlink_observed_age_ms` | `305` |
| `chainlink_event_to_observed_ms` | `1586` |
| `orderbook_observed_age_ms` | `13397` |
| `orderbook_event_to_observed_ms` | `15737` |
| `current_orderbook_observed_age_ms` | `461` |
| `current_orderbook_event_to_observed_ms` | `156` |
| `next_orderbook_observed_age_ms` | `3422` |
| `next_orderbook_event_to_observed_ms` | `581` |

Interpretation: the current-window path was fresh. Some warmed/future
order-book rows were much older and should remain decision-gated.

## Hot Decision Journals

Hot decision telemetry from `status.json`:

| Field | Value |
|---|---:|
| `states_built` | `30626` |
| `states_persist_queued` | `30626` |
| `dropped_events` | `0` |
| `last_state_age_ms` | `0` |
| `last_observed_to_state_us` | `204` |

Raw journal inventory:

| Raw group | Files | Lines | Latest file lines |
|---|---:|---:|---:|
| `polymarket_decision_state` | 15 | 1,799,984 | 60,578 |
| `polymarket_rtds_chainlink` | 15 | 99,028 | 4,192 |
| `polymarket_clob_market_ws` | 15 | 1,743,084 | 63,571 |
| `polymarket_state_manager` | 15 | 37,624 | 427 |

Data-root sizes:

| Path | Size |
|---|---:|
| `/home/spoon/polymarket-data/raw` | `5.1G` |
| `/home/spoon/polymarket-data/db` | `1.2G` |
| `/home/spoon/polymarket-data/live` | `56K` |
| `/home/spoon/polymarket-data/logs` | `4.0K` |

## Normalized DuckDB Health

`normalized_health.json` was fresh at
`2026-06-02T21:35:28.589413+00:00`.

| Table | Rows | Latest timestamp |
|---|---:|---|
| `core.contracts` | 688 | `2026-06-02T21:35:00.472124+00:00` |
| `core.contract_rules` | 0 | null |
| `core.price_ticks` | 99,010 | `2026-06-02T21:35:28.324920+00:00` |
| `core.orderbook_snapshots` | 1,747,926 | `2026-06-02T21:35:28.538237+00:00` |
| `features.asof_state_inputs` | 167,372 | `2026-06-02T21:35:28.569662+00:00` |
| `features.decision_snapshots` | 0 | null |
| `features.probability_outputs` | 0 | null |

Direct read-only DuckDB inspection was blocked by the normalizer's active write
lock. That is not a collector failure, but it means live DB audits need a
lock-safe snapshot/copy path or a verifier that coordinates with the writer.

`core.contract_rules` being empty is expected for Rust status-derived live
contracts because the status file does not contain full venue rule text. Do not
synthesize rule text.

## Replay Proof

Existing Spoon artifact:

```text
/home/spoon/polymarket-data/live/hot_decision_replay_report.json
```

Current content:

| Field | Value |
|---|---:|
| `ok` | `true` |
| `rows_scanned` | `5000` |
| `rows_checked` | `40` |
| `rows_skipped_not_replay_ready` | `451` |
| `rows_skipped_quality_blocked` | `2204` |
| `mismatch_count` | `0` |
| Price watermark | `2026-06-02T08:56:23.253620+00:00` |
| Order-book watermark | `2026-06-02T08:56:25.748309+00:00` |

This proves the replay verifier has passed on Spoon before. It does not prove
the current 21:35 UTC live rows, because the artifact watermarks are stale
relative to the current normalized health timestamps.

## Order Latency Probe

Artifact:

```text
/home/spoon/polymarket-data/live/order_latency_probe.json
```

No-auth CLOB probe to `https://clob-v2.polymarket.com`:

| Metric | Value |
|---|---:|
| Iterations | 30 |
| HTTP round-trip min | `152 ms` |
| HTTP round-trip p50 | `155 ms` |
| HTTP round-trip p95 | `402 ms` |
| HTTP round-trip max | `430 ms` |
| Payload-build p50 | `4 us` |

This is useful for latency budgeting, but it is not an authenticated order path
and does not include wallet signing, queue position, fill uncertainty, or
cancel/replace behavior.

## Sections 1-4 Status

| Section | Status | Remaining hole |
|---|---|---|
| 1. Contract rules and settlement source | Live BTC/ETH 5m current and next windows are discovered with side token ids. Chainlink RTDS remains settlement/reference truth. | Full venue rule text and rule hashes are not normalized from Rust status; `core.contract_rules` is empty. |
| 2. Data and as-of state | Live raw JSONL, normalized `core.price_ticks`, normalized `core.orderbook_snapshots`, and `features.asof_state_inputs` are current. | Current hot replay proof should be rerun against fresh watermarks; direct DB audit needs a lock-safe path. |
| 3. Probability-output readiness | Offline/replay probability code may exist, but live authority is still blocked. `features.probability_outputs` is empty. | Need fresh replay equivalence plus explicit quality gates before probability outputs become decision inputs. |
| 4. Monte Carlo readiness | Monte Carlo remains replay/research-only. Volatility and `sigma_tau` must use Chainlink rows only. | Need historical path-prior calibration and no-touch validation from as-of-safe data before any paper/live authority. |

## Gaps To Fix Next

1. Resolve the local merge conflicts before doing further Rust collector work.
2. Explain and fix the `POLYMARKET_PREWARM_WINDOWS=3` versus empty
   `next_next` mismatch, or relax the strict verifier if the current intended
   runtime is only current plus next.
3. Rerun the hot-decision replay verifier against current Spoon watermarks
   using a lock-safe method.
4. Keep the normalizer CPU cost visible. At inspection it was roughly 63
   percent CPU, while the collector was roughly 9 percent.
5. Do not start probability, paper trading, live trading, private-key handling,
   or order placement until Sections 1-2 have routine replay proof on current
   data.

## Commands Run

Representative read-only checks:

```bash
git status --short --branch
git status --porcelain=v1 -uall
git log --oneline -8
ssh spoon 'cd /home/spoon/polymarket && git status --short --branch && git log --oneline -5'
ssh spoon 'docker ps --format "{{.Names}}\t{{.Status}}\t{{.Image}}" | grep polymarket'
ssh spoon 'python3 /home/spoon/polymarket/scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json --max-status-age-seconds 30 --max-price-age-ms 30000 --max-orderbook-age-ms 30000 --max-websocket-event-age-ms 30000 --raw-root /home/spoon/polymarket-data/raw'
ssh spoon 'python3 /home/spoon/polymarket/scripts/verify_state_manager_report.py /home/spoon/polymarket-data/live/status.json'
ssh spoon 'du -sh /home/spoon/polymarket-data/*'
ssh spoon 'docker stats --no-stream'
```

Verification outcomes:

| Check | Result |
|---|---|
| Collector health script | pass |
| State-manager report verifier | fail: `next_next missing assets: BTC, ETH` |
| Direct host DuckDB import | fail: no host `duckdb` module |
| Direct container DuckDB read-only open | fail: active normalizer writer lock |

