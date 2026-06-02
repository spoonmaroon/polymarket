# Combined Architecture And Durability Spec

Date: 2026-06-02

Scope: current Spoon deployment, live read-only data path, hot
`DecisionState` persistence, raw journal durability, deploy collision handling,
and the gate before probability work.

## 1. Purpose

This spec exists to stop architectural confusion from leaking into future work.
The system has moved away from the old Python live collector. The live runtime is
now the Rust state-manager on Spoon. Python and DuckDB remain important, but
they are replay and research infrastructure, not the hot decision path.

The immediate operating rule is:

```text
Do not add probability, Monte Carlo, XGBoost, paper trading, private keys,
or order placement until the live state path is durable, replay-safe, and
documented.
```

## 2. Architecture

The current architecture is read-only and split into two lanes.

Hot live lane:

```text
Polymarket Gamma REST
  -> discover BTC/ETH 5m current, next, and next-next markets
Polymarket CLOB REST
  -> seed and backup-refresh order books
Chainlink RTDS WebSocket
  -> BTC/USD and ETH/USD settlement/reference ticks
Polymarket CLOB WebSocket
  -> best-bid/best-ask updates for warmed token ids
Rust state-manager
  -> keeps current state in memory
  -> writes atomic live status
  -> builds exact hot DecisionState snapshots on relevant WebSocket events
  -> appends hot DecisionState JSONL
  -> appends raw Chainlink/CLOB/state journals
```

Replay and research lane:

```text
Raw Rust journals
  -> Python normalizer
  -> DuckDB core tables
  -> normalized health file
  -> current/as-of DecisionState snapshot builder
  -> future probability/replay/validation
```

The hot live lane must not route decisions through DuckDB, Python normalization,
normalized health, or status-file polling. Those components are allowed to
observe, normalize, replay, audit, and validate after the live state already
exists.

## 3. Component Roles

| Component | Role | Hot path? |
|---|---|---|
| Gamma REST | Contract discovery for active 5m slugs and token ids | Startup/refresh only |
| CLOB REST | Full order-book seed and backup refresh | Startup/recovery only |
| Chainlink RTDS WebSocket | BTC/ETH settlement/reference price truth | Yes |
| CLOB WebSocket | Live top-of-book updates for warmed tokens | Yes |
| Rust state-manager | In-memory live state and health authority | Yes |
| Hot DecisionState builder | Builds exact decision snapshots on WebSocket events | Yes |
| Hot DecisionState journal | Append-only snapshot persistence | Yes |
| Raw JSONL journals | Append-only replay/audit source | Yes, as persistence |
| DuckDB | Normalized replay/research database | No |
| Python normalizer | Raw-to-DuckDB bridge | No |
| Normalized health JSON | Operator status for normalized rows | No |
| Python monitor | Read-only operator display | No |

Chainlink RTDS is the reference and settlement source for BTC/USD and ETH/USD.
Coinbase, Binance, and proxy feeds are diagnostics only. Noise is not a trading
signal; it is something to suppress, reconcile, haircut, or block.

## 4. Durability Contract

The accepted durability decision is:

```text
Persist exact hot DecisionState snapshots before probability decisions.
Keep append-only Chainlink/CLOB raw journals as the replay/audit source.
Do not require every raw event to be normalized into DuckDB before each live
decision.
Require replay tests proving raw journals can reconstruct sampled states before
probability or trading work starts.
```

Reasoning:

- DuckDB is too slow and too operationally fragile to sit inside the hot live
  decision path.
- Raw journals preserve event lineage and let replay reconstruct what the Rust
  runtime saw.
- Exact `DecisionState` snapshots preserve the state the future probability
  engine would have consumed.
- Normalized DuckDB rows are still required for replay, validation, reporting,
  and model research.

This gives both speed and auditability:

```text
Fast path = Rust memory -> exact DecisionState -> decide
Audit path = raw journals + snapshots -> normalize -> replay -> compare
```

## 5. Deploy And Collision Handling

Deploy collisions are operationally tied to durability because two agents
deploying at once can restart the collector, wipe/reseed runtime state, or race
normalization against the wrong raw data root.

Current rule:

- Use the Spoon deploy lock.
- A second deploy attempt should exit when a deploy is already running.
- Treat "deploy already running" as a contained collision, not as a reason to
  manually force a second restart.
- After deploy, verify repo HEAD, collector status, state-manager report,
  normalized health, and monitor output.

Current deployed state from read-only inspection:

| Item | Value |
|---|---|
| Spoon repo | `/home/spoon/polymarket` |
| Spoon HEAD | `881b97d Label Rust orderbook freshness in monitor` |
| Collector container | `polymarket-rust-collector-collector-1`, healthy |
| Normalizer container | `polymarket-rust-collector-normalizer-1`, healthy |
| State-manager health | `current=2 next=2 next_next=2 orderbooks=12 subscriptions=12 health_flags=0` |
| Collector status check | `ok=True`, status age about 0.5s, price age about 1.0s, orderbook age about 0.6s |

## 6. Live Data Written And Read

Spoon live paths:

| Path | Meaning |
|---|---|
| `/home/spoon/polymarket-data/live/status.json` | Atomic Rust live status |
| `/home/spoon/polymarket-data/live/normalized_health.json` | Normalized DuckDB health |
| `/home/spoon/polymarket-data/live/order_latency_probe.json` | No-auth order latency probe output when run |
| `/home/spoon/polymarket-data/raw/polymarket_rtds_chainlink/price_update/.../events.jsonl` | Chainlink RTDS raw events |
| `/home/spoon/polymarket-data/raw/polymarket_clob_market_ws/best_bid_ask/.../events.jsonl` | CLOB WebSocket raw events |
| `/home/spoon/polymarket-data/raw/polymarket_state_manager/state_snapshot/.../state-manager.jsonl` | State-manager status snapshots |
| `/home/spoon/polymarket-data/raw/polymarket_decision_state/.../decision-state.jsonl` | Exact hot `DecisionState` snapshots |
| `/home/spoon/polymarket-data/db/polymarket.duckdb` | Normalized replay/research database |

Current data sizes from read-only inspection:

| Path | Size |
|---|---:|
| `/home/spoon/polymarket-data/raw` | 144M |
| `/home/spoon/polymarket-data/db` | 20M |
| `/home/spoon/polymarket-data/live` | 208K |
| `/home/spoon/polymarket-data/logs` | 4K |

Hot decision journal:

| Metric | Value |
|---|---:|
| JSONL rows | 29242 |
| Bytes | 35889137 |

## 7. Current Live Evidence

Latest inspected `status.json`:

| Field | Value |
|---|---|
| Schema | `rust-live-probe-state-manager-v1` |
| Mode | `state-manager` |
| Generated at | `2026-06-02T07:51:57.247861194Z` |
| Current contracts | 2 |
| Next contracts | 2 |
| Next-next contracts | 2 |
| Order books | 12 |
| Subscriptions | 12 |
| WebSocket status rows | 2 |
| Health flags | 0 |

Latest Chainlink rows:

| Symbol | Price | Observed |
|---|---:|---|
| BTC/USD | 70050.69638534349 | `2026-06-02T07:51:56.327690262Z` |
| ETH/USD | 1981.1512364401187 | `2026-06-02T07:51:56.328983709Z` |

Latest first-class latency marks:

| Mark | ms |
|---|---:|
| `chainlink_observed_age_ms` | 920 |
| `chainlink_event_to_observed_ms` | 1328 |
| `orderbook_observed_age_ms` | 8548 |
| `orderbook_event_to_observed_ms` | 10690 |

Latest hot decision telemetry:

| Metric | Value |
|---|---:|
| `states_built` | 29238 |
| `states_persist_queued` | 29238 |
| `dropped_events` | 0 |
| `last_state_age_ms` | 1 |
| `last_observed_to_state_us` | 405 |

Normalized health:

| Table | Rows | Latest timestamp |
|---|---:|---|
| `core.contracts` | 28 | `2026-06-02T07:51:43.962357+00:00` |
| `core.contract_rules` | 0 | null |
| `core.price_ticks` | 2582 | `2026-06-02T07:51:37.431089+00:00` |
| `core.orderbook_snapshots` | 32614 | `2026-06-02T07:51:37.170727+00:00` |
| `features.asof_state_inputs` | 436 | `2026-06-02T07:51:43.539030+00:00` |
| `features.decision_snapshots` | 0 | null |

Interpretation:

- Live Rust state is healthy.
- Normalized DuckDB rows are current.
- Current/as-of `DecisionState` rows exist.
- Hot decision snapshots are being built and queued without drops.
- `features.decision_snapshots` remains empty; that is acceptable before
  probability work if hot `DecisionState` JSONL is the live persistence boundary.

## 8. Five-Minute Bowling View

In one BTC/ETH five-minute span, the system is supposed to behave like this:

1. Discover current, next, and next-next BTC/ETH 5m contracts.
2. Warm UP and DOWN token ids for each asset/window.
3. Seed order books with CLOB REST.
4. Subscribe to CLOB WebSocket updates for all warmed token ids.
5. Subscribe to Chainlink RTDS BTC/USD and ETH/USD.
6. On each relevant WebSocket event, update Rust memory.
7. Build exact hot `DecisionState` snapshots when the event affects a current
   decision state.
8. Persist hot `DecisionState` JSONL.
9. Append raw event/state journals.
10. In the slower lane, normalize raw rows into DuckDB and write normalized
    health.
11. Operator monitor reads status and DuckDB health but does not participate in
    decision-making.

The current live window tracks 12 CLOB token subscriptions because it includes:

```text
BTC current UP/DOWN
BTC next UP/DOWN
BTC next-next UP/DOWN
ETH current UP/DOWN
ETH next UP/DOWN
ETH next-next UP/DOWN
```

## 9. Latency Path

Hot reaction path:

```text
WebSocket event observed
  -> Rust parses event
  -> Rust updates in-memory price/book state
  -> Rust builds exact DecisionState
  -> Rust queues/persists snapshot
  -> future probability function runs in Rust memory
  -> future order submission path signs and sends order
```

Do not measure the hot decision path by polling `status.json` or DuckDB. Those
are observability and replay systems.

The current no-auth order probe previously measured CLOB REST response samples
around the low hundreds of milliseconds. That probe does not include signing,
private key handling, real order validation, matching behavior, or fill
confirmation. Future order latency must be measured separately as:

```text
sign time
REST connect/write time
server response time
full round trip
fill/ack behavior
```

## 10. Sections 1-4 Status

| Section | Status | Remaining gate |
|---|---|---|
| 1. Contract rules and settlement source | Ready for current Rust live 5m collection | Keep Chainlink as truth; maintain fail-closed behavior for ambiguous rules |
| 2. Data and as-of state | Mostly ready for pre-probability read-only work | Add replay comparison tests from raw journals to sampled persisted `DecisionState` rows |
| 3. Core probability outputs | Not started by design | Wait until replay comparison passes |
| 4. Monte Carlo path generation | Not started by design | Wait until Section 2 replay gate passes |

The remaining real hole is not "collect more live data." It is:

```text
Prove raw journals can reconstruct sampled exact DecisionState snapshots.
```

## 11. Operator Commands

Live monitor:

```bash
ssh -t spoon 'export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"; cd /home/spoon/polymarket && uv run polymarket-engine monitor --duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb --status-path /home/spoon/polymarket-data/live/status.json --refresh 1 --limit 12'
```

State-manager health:

```bash
ssh spoon 'python3 /home/spoon/polymarket/scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json --max-status-age-seconds 30 --max-price-age-ms 30000 --max-orderbook-age-ms 30000 --max-websocket-event-age-ms 30000 --raw-root /home/spoon/polymarket-data/raw'
ssh spoon 'python3 /home/spoon/polymarket/scripts/verify_state_manager_report.py /home/spoon/polymarket-data/live/status.json'
```

Normalize current Rust raw journals:

```bash
ssh spoon 'cd /home/spoon/polymarket && uv run polymarket-engine normalize-rust-events --raw-root /home/spoon/polymarket-data/raw --duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb'
```

Write normalized health:

```bash
ssh spoon 'cd /home/spoon/polymarket && uv run polymarket-engine write-normalized-health --duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb --out /home/spoon/polymarket-data/live/normalized_health.json'
```

Build current DuckDB `DecisionState` snapshots:

```bash
ssh spoon 'cd /home/spoon/polymarket && uv run polymarket-engine build-current-decision-states --duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb --status-path /home/spoon/polymarket-data/live/status.json'
```

## 12. Next Required Work

1. Add replay tests that rebuild sampled `DecisionState` snapshots from raw
   Chainlink/CLOB journals and compare them to persisted exact snapshots.
2. Decide whether `features.decision_snapshots` should mirror the hot
   `polymarket_decision_state` JSONL journal or remain reserved for probability
   decisions.
3. Keep probability, Monte Carlo, XGBoost, and trading blocked until item 1
   passes.

