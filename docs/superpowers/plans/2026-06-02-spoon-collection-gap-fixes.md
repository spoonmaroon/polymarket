# Spoon Collection Gap Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the deployed spoon collection gaps so BTC/ETH 5m live collection has current/next/next-next warm windows, fresh raw journals, fresh normalized DuckDB health, and clear database expectations before probability work starts.

**Architecture:** Spoon is the live source of truth; local Mac data is out of scope. The Rust state manager remains the read-only collector and writes status plus raw JSONL journals. A separate Python normalizer sidecar converts raw JSONL/status state into DuckDB and writes `normalized_health.json`, so the Rust collector does not block on DuckDB locks or Python research code.

**Tech Stack:** Python 3.11+, pytest, DuckDB, Docker Compose, Rust state-manager JSON status, append-only JSONL raw journals, shell deploy scripts.

---

## Current Spoon Findings This Plan Fixes

- Spoon collector is live and fresh at commit `fadcc182ce73c78976c4074f22ac451a98c4e0c4`.
- Spoon raw JSONL journals are fresh for:
  - `polymarket_rtds_chainlink/price_update`
  - `polymarket_clob_market_ws/best_bid_ask`
  - `polymarket_state_manager/state_snapshot`
- Spoon status reports `current=2`, `next=2`, `next_next=0` because the runtime uses `--prewarm-windows 2`.
- `normalized_health.json` exists but is not continuously fresh.
- Spoon normalized DuckDB has zero `core.contract_rules` and zero `features.decision_snapshots`.
- `features.decision_snapshots=0` is acceptable until probability exists.
- `core.contract_rules=0` is acceptable only if explicitly documented as unavailable from Rust status snapshots; do not synthesize venue rule text.
- Spoon branch topology is ahead of `origin/main`; deploy must use `origin/codex/rust-raw-normalizer` or a merged/pushed main to avoid downgrade.

## File Structure

- Modify `scripts/check_collector_status.py`
  - Enforce next-next BTC/ETH contracts when state-manager status is active.
  - Add optional normalized-health freshness validation.
- Modify `scripts/verify_state_manager_report.py`
  - Require BTC/ETH assets in `next_next`.
  - Print `next_next` count in smoke output.
- Modify `tests/scripts/test_check_collector_status.py`
  - Add failing tests for missing `next_next` and stale normalized health.
- Create `tests/scripts/test_verify_state_manager_report.py`
  - Add script-level tests for missing `next_next`.
- Modify `deploy/collector/.env.example`
  - Change default prewarm windows to `3`.
- Modify `deploy/collector/docker-compose.yml`
  - Change collector default `POLYMARKET_PREWARM_WINDOWS` to `3`.
  - Add a `normalizer` service.
  - Add health checks for normalized health.
- Modify `deploy/collector/collector-entrypoint.sh`
  - Change shell fallback `POLYMARKET_PREWARM_WINDOWS` to `3`.
- Create `deploy/normalizer/Dockerfile`
  - Python sidecar image for `polymarket-engine` CLI.
- Create `deploy/normalizer/normalizer-entrypoint.sh`
  - Loop raw normalization, current decision-state snapshots, and normalized health writes.
- Modify `scripts/deploy.sh`
  - Start both collector and normalizer.
  - Smoke-check collector raw/status and normalized-health freshness.
- Modify `tests/scripts/test_deploy_script.py`
  - Lock deploy and compose behavior.
- Modify `docs/PART_TWO_LIVE_COLLECTORS.md`
  - Document spoon live architecture and database expectations.
- Modify `docs/SPOON_DEPLOYMENT.md`
  - Document branch-aware deploy and post-deploy checks.
- Modify `tests/docs/test_active_runtime_docs.py`
  - Lock 3-window spoon runtime and normalizer sidecar wording.

---

### Task 1: Enforce Next-Next State-Manager Coverage

**Files:**
- Modify: `scripts/check_collector_status.py`
- Modify: `scripts/verify_state_manager_report.py`
- Modify: `tests/scripts/test_check_collector_status.py`
- Create: `tests/scripts/test_verify_state_manager_report.py`

- [ ] **Step 1: Write the failing check-status test**

Append this test to `tests/scripts/test_check_collector_status.py`:

```python
def test_state_manager_status_rejects_missing_next_next_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    status = _fresh_state_manager_status()
    status["next_next"] = []
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["check_collector_status.py", "--status-path", str(status_path)],
    )

    with pytest.raises(SystemExit, match="state-manager missing next_next BTC/ETH contracts"):
        script.main()
```

Also update `_fresh_state_manager_status()` in the same file so the normal fixture includes a valid next-next window:

```python
        "current": [{}, {}],
        "next": [{}, {}],
        "next_next": [{}, {}],
```

- [ ] **Step 2: Run the failing check-status test**

Run:

```bash
uv run pytest tests/scripts/test_check_collector_status.py::test_state_manager_status_rejects_missing_next_next_contracts -q
```

Expected: FAIL because `scripts/check_collector_status.py` currently does not reject missing `next_next`.

- [ ] **Step 3: Implement next-next rejection**

In `scripts/check_collector_status.py`, add this check immediately after the existing `next` check in `_reject_state_manager_payload()`:

```python
    if len(payload.get("next_next", [])) < 2:
        raise SystemExit("state-manager missing next_next BTC/ETH contracts")
```

- [ ] **Step 4: Run the check-status tests**

Run:

```bash
uv run pytest tests/scripts/test_check_collector_status.py -q
```

Expected: all tests in that file pass.

- [ ] **Step 5: Write verifier tests**

Create `tests/scripts/test_verify_state_manager_report.py`:

```python
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    script_path = Path(__file__).parents[2] / "scripts" / "verify_state_manager_report.py"
    spec = importlib.util.spec_from_file_location("verify_state_manager_report", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _contract(asset: str) -> dict[str, object]:
    lower = asset.lower()
    return {
        "window": {
            "asset": asset,
            "interval": "5m",
            "start_ts": "2026-06-02T07:00:00+00:00",
            "end_ts": "2026-06-02T07:05:00+00:00",
        },
        "up": {"asset": asset, "side": "Up", "token_id": f"{lower}-up"},
        "down": {"asset": asset, "side": "Down", "token_id": f"{lower}-down"},
    }


def _report() -> dict[str, object]:
    return {
        "schema_version": "rust-live-probe-state-manager-v1",
        "mode": "state-manager",
        "generated_at": "2026-06-02T07:00:01+00:00",
        "elapsed_ms": 1000,
        "current": [_contract("BTC"), _contract("ETH")],
        "next": [_contract("BTC"), _contract("ETH")],
        "next_next": [_contract("BTC"), _contract("ETH")],
        "chainlink_prices": [
            {
                "source_key": "polymarket_rtds_chainlink",
                "symbol": "BTC/USD",
                "event_ts": "2026-06-02T07:00:01+00:00",
                "observed_ts": "2026-06-02T07:00:01+00:00",
                "price": "70000.0",
            },
            {
                "source_key": "polymarket_rtds_chainlink",
                "symbol": "ETH/USD",
                "event_ts": "2026-06-02T07:00:01+00:00",
                "observed_ts": "2026-06-02T07:00:01+00:00",
                "price": "2000.0",
            },
        ],
        "proxy_prices": [],
        "freshness": [
            {"source_key": "polymarket_rtds_chainlink", "symbol": "BTC/USD", "age_ms": 10, "stale": False},
            {"source_key": "polymarket_rtds_chainlink", "symbol": "ETH/USD", "age_ms": 10, "stale": False},
        ],
        "latency_marks": [
            {"name": "chainlink_observed_age_ms", "elapsed_ms": 10},
            {"name": "chainlink_event_to_observed_ms", "elapsed_ms": 10},
            {"name": "orderbook_observed_age_ms", "elapsed_ms": 10},
            {"name": "orderbook_event_to_observed_ms", "elapsed_ms": 10},
        ],
        "orderbooks": [
            {
                "venue": "polymarket",
                "source_key": "polymarket_clob_market_ws",
                "market_slug": "btc-updown-5m-1780383600",
                "contract_id": "btc-current",
                "token_id": "btc-up",
                "asset": "BTC",
                "side": "UP",
                "event_ts": "2026-06-02T07:00:01+00:00",
                "observed_ts": "2026-06-02T07:00:01+00:00",
                "bids": [],
                "asks": [],
            },
            {
                "venue": "polymarket",
                "source_key": "polymarket_clob_market_ws",
                "market_slug": "btc-updown-5m-1780383600",
                "contract_id": "btc-current",
                "token_id": "btc-down",
                "asset": "BTC",
                "side": "DOWN",
                "event_ts": "2026-06-02T07:00:01+00:00",
                "observed_ts": "2026-06-02T07:00:01+00:00",
                "bids": [],
                "asks": [],
            },
            {
                "venue": "polymarket",
                "source_key": "polymarket_clob_market_ws",
                "market_slug": "eth-updown-5m-1780383600",
                "contract_id": "eth-current",
                "token_id": "eth-up",
                "asset": "ETH",
                "side": "UP",
                "event_ts": "2026-06-02T07:00:01+00:00",
                "observed_ts": "2026-06-02T07:00:01+00:00",
                "bids": [],
                "asks": [],
            },
            {
                "venue": "polymarket",
                "source_key": "polymarket_clob_market_ws",
                "market_slug": "eth-updown-5m-1780383600",
                "contract_id": "eth-current",
                "token_id": "eth-down",
                "asset": "ETH",
                "side": "DOWN",
                "event_ts": "2026-06-02T07:00:01+00:00",
                "observed_ts": "2026-06-02T07:00:01+00:00",
                "bids": [],
                "asks": [],
            },
        ],
        "subscriptions": [
            {"source_key": "polymarket_clob_market_ws", "channel": "market", "asset": "BTC", "token_id": "btc-up"},
            {"source_key": "polymarket_clob_market_ws", "channel": "market", "asset": "BTC", "token_id": "btc-down"},
            {"source_key": "polymarket_clob_market_ws", "channel": "market", "asset": "ETH", "token_id": "eth-up"},
            {"source_key": "polymarket_clob_market_ws", "channel": "market", "asset": "ETH", "token_id": "eth-down"},
        ],
        "websocket_status": [
            {
                "source_key": "polymarket_rtds_chainlink",
                "channel": "crypto_prices_chainlink",
                "connection_state": "Connected",
                "reconnect_count": 0,
                "subscription_count": 1,
                "active_token_count": 2,
                "ended_stream_count": 0,
                "stream_error_count": 0,
                "last_event_age_ms": 10,
            },
            {
                "source_key": "polymarket_clob_market_ws",
                "channel": "market",
                "connection_state": "Connected",
                "reconnect_count": 0,
                "subscription_count": 4,
                "active_token_count": 4,
                "ended_stream_count": 0,
                "stream_error_count": 0,
                "last_event_age_ms": 10,
            },
        ],
        "health_flags": [],
    }


def test_verifier_rejects_missing_next_next_assets() -> None:
    script = _load_script()
    payload = _report()
    payload["next_next"] = []

    with pytest.raises(SystemExit, match="next_next missing assets"):
        script.validate(payload)


def test_verifier_accepts_next_next_assets(capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script()
    report_path = tmp_path / "state-manager.json"
    report_path.write_text(json.dumps(_report()), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["verify_state_manager_report.py", str(report_path)])

    assert script.main() == 0
    assert "next_next=2" in capsys.readouterr().out
```

- [ ] **Step 6: Run the failing verifier test**

Run:

```bash
uv run pytest tests/scripts/test_verify_state_manager_report.py -q
```

Expected: FAIL because `verify_state_manager_report.py` does not require next-next assets or print `next_next=`.

- [ ] **Step 7: Implement verifier next-next enforcement**

In `scripts/verify_state_manager_report.py`, change:

```python
    validate_contracts(require_list(payload, "next_next"), "next_next", require_assets=False)
```

to:

```python
    validate_contracts(require_list(payload, "next_next"), "next_next", require_assets=True)
```

In `main()`, add `next_next` to the printed summary immediately after `next`:

```python
        f"next_next={len(payload['next_next'])}",
```

- [ ] **Step 8: Run task verification**

Run:

```bash
uv run pytest tests/scripts/test_check_collector_status.py tests/scripts/test_verify_state_manager_report.py -q
```

Expected: all selected tests pass.

- [ ] **Step 9: Commit**

Run:

```bash
git add scripts/check_collector_status.py scripts/verify_state_manager_report.py tests/scripts/test_check_collector_status.py tests/scripts/test_verify_state_manager_report.py
git commit -m "Require next-next state-manager coverage"
```

Expected: local commit created on `codex/rust-raw-normalizer`.

---

### Task 2: Configure Spoon Collector For Three Warm Windows

**Files:**
- Modify: `deploy/collector/.env.example`
- Modify: `deploy/collector/docker-compose.yml`
- Modify: `deploy/collector/collector-entrypoint.sh`
- Modify: `tests/scripts/test_deploy_script.py`

- [ ] **Step 1: Write the failing deploy config test**

Append this test to `tests/scripts/test_deploy_script.py`:

```python
def test_collector_defaults_to_three_prewarm_windows() -> None:
    env_example = (ROOT / "deploy" / "collector" / ".env.example").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    entrypoint = (
        ROOT / "deploy" / "collector" / "collector-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "POLYMARKET_PREWARM_WINDOWS=3" in env_example
    assert "POLYMARKET_PREWARM_WINDOWS:-3" in compose
    assert 'PREWARM_WINDOWS="${POLYMARKET_PREWARM_WINDOWS:-3}"' in entrypoint
```

- [ ] **Step 2: Run the failing deploy config test**

Run:

```bash
uv run pytest tests/scripts/test_deploy_script.py::test_collector_defaults_to_three_prewarm_windows -q
```

Expected: FAIL because defaults are still `2`.

- [ ] **Step 3: Change defaults to three windows**

In `deploy/collector/.env.example`, replace:

```text
POLYMARKET_PREWARM_WINDOWS=2
```

with:

```text
POLYMARKET_PREWARM_WINDOWS=3
```

In `deploy/collector/docker-compose.yml`, replace:

```yaml
      POLYMARKET_PREWARM_WINDOWS: ${POLYMARKET_PREWARM_WINDOWS:-2}
```

with:

```yaml
      POLYMARKET_PREWARM_WINDOWS: ${POLYMARKET_PREWARM_WINDOWS:-3}
```

In `deploy/collector/collector-entrypoint.sh`, replace:

```sh
PREWARM_WINDOWS="${POLYMARKET_PREWARM_WINDOWS:-2}"
```

with:

```sh
PREWARM_WINDOWS="${POLYMARKET_PREWARM_WINDOWS:-3}"
```

- [ ] **Step 4: Run task verification**

Run:

```bash
uv run pytest tests/scripts/test_deploy_script.py::test_collector_defaults_to_three_prewarm_windows -q
```

Expected: selected test passes.

- [ ] **Step 5: Commit**

Run:

```bash
git add deploy/collector/.env.example deploy/collector/docker-compose.yml deploy/collector/collector-entrypoint.sh tests/scripts/test_deploy_script.py
git commit -m "Default spoon collector to three warm windows"
```

Expected: local commit created.

---

### Task 3: Add Normalized-Health Freshness Checks

**Files:**
- Modify: `scripts/check_collector_status.py`
- Modify: `tests/scripts/test_check_collector_status.py`

- [ ] **Step 1: Write failing normalized-health tests**

Append these helpers and tests to `tests/scripts/test_check_collector_status.py`:

```python
def _write_normalized_health(
    path: Path,
    *,
    mtime_age_seconds: float,
    latest_age_seconds: float,
) -> None:
    now = datetime.now(timezone.utc)
    latest = now.timestamp() - latest_age_seconds
    latest_iso = datetime.fromtimestamp(latest, timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-normalized-health-v1",
                "generated_at": latest_iso,
                "tables": [
                    {"table": "core.price_ticks", "rows": 1, "latest_ts": latest_iso},
                    {"table": "core.orderbook_snapshots", "rows": 1, "latest_ts": latest_iso},
                    {"table": "features.asof_state_inputs", "rows": 1, "latest_ts": latest_iso},
                ],
            }
        ),
        encoding="utf-8",
    )
    mtime = time.time() - mtime_age_seconds
    os.utime(path, (mtime, mtime))


def test_state_manager_status_rejects_stale_normalized_health_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    status_path = tmp_path / "status.json"
    raw_root = tmp_path / "raw"
    normalized_health_path = tmp_path / "live" / "normalized_health.json"
    status_path.write_text(json.dumps(_fresh_state_manager_status()), encoding="utf-8")
    _write_raw_event_journal(raw_root, "polymarket_rtds_chainlink/price_update", mtime_age_seconds=0.0)
    _write_raw_event_journal(raw_root, "polymarket_clob_market_ws/best_bid_ask", mtime_age_seconds=0.0)
    _write_normalized_health(
        normalized_health_path,
        mtime_age_seconds=60.0,
        latest_age_seconds=60.0,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_collector_status.py",
            "--status-path",
            str(status_path),
            "--raw-root",
            str(raw_root),
            "--normalized-health-path",
            str(normalized_health_path),
            "--max-normalized-health-age-ms",
            "10000",
        ],
    )

    with pytest.raises(SystemExit, match="normalized health stale"):
        script.main()


def test_state_manager_status_accepts_fresh_normalized_health_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    status_path = tmp_path / "status.json"
    raw_root = tmp_path / "raw"
    normalized_health_path = tmp_path / "live" / "normalized_health.json"
    status_path.write_text(json.dumps(_fresh_state_manager_status()), encoding="utf-8")
    _write_raw_event_journal(raw_root, "polymarket_rtds_chainlink/price_update", mtime_age_seconds=0.0)
    _write_raw_event_journal(raw_root, "polymarket_clob_market_ws/best_bid_ask", mtime_age_seconds=0.0)
    _write_normalized_health(
        normalized_health_path,
        mtime_age_seconds=0.0,
        latest_age_seconds=0.0,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_collector_status.py",
            "--status-path",
            str(status_path),
            "--raw-root",
            str(raw_root),
            "--normalized-health-path",
            str(normalized_health_path),
            "--max-normalized-health-age-ms",
            "10000",
        ],
    )

    assert script.main() == 0
```

- [ ] **Step 2: Run failing normalized-health test**

Run:

```bash
uv run pytest tests/scripts/test_check_collector_status.py::test_state_manager_status_rejects_stale_normalized_health_file -q
```

Expected: FAIL because `--normalized-health-path` is not defined.

- [ ] **Step 3: Add normalized-health CLI arguments**

In `scripts/check_collector_status.py`, add these parser arguments after `--max-raw-event-age-ms`:

```python
    parser.add_argument("--normalized-health-path", type=Path)
    parser.add_argument("--max-normalized-health-age-ms", type=int, default=30_000)
```

In `main()`, after the raw journal check block, add:

```python
        if args.normalized_health_path is not None:
            _reject_stale_normalized_health(
                args.normalized_health_path,
                max_normalized_health_age_ms=args.max_normalized_health_age_ms,
            )
```

- [ ] **Step 4: Add normalized-health rejection helper**

Append this function before `if __name__ == "__main__":` in `scripts/check_collector_status.py`:

```python
def _reject_stale_normalized_health(
    path: Path,
    *,
    max_normalized_health_age_ms: int,
) -> None:
    if not path.exists():
        raise SystemExit(f"normalized health missing: path={path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "polymarket-normalized-health-v1":
        raise SystemExit("normalized health has unexpected schema_version")
    now = datetime.now(timezone.utc)
    generated_at = _parse_timestamp(payload.get("generated_at"))
    generated_age_ms = int((now - generated_at.astimezone(timezone.utc)).total_seconds() * 1000)
    file_age_ms = int((time.time() - path.stat().st_mtime) * 1000)
    if generated_age_ms > max_normalized_health_age_ms:
        raise SystemExit(f"normalized health stale: generated_age_ms={generated_age_ms}")
    if file_age_ms > max_normalized_health_age_ms:
        raise SystemExit(f"normalized health stale: file_age_ms={file_age_ms}")

    required_tables = {
        "core.price_ticks",
        "core.orderbook_snapshots",
        "features.asof_state_inputs",
    }
    rows = payload.get("tables")
    if not isinstance(rows, list):
        raise SystemExit("normalized health tables must be a list")
    by_table = {
        str(row.get("table")): row
        for row in rows
        if isinstance(row, dict) and row.get("table") is not None
    }
    missing = sorted(required_tables - set(by_table))
    if missing:
        raise SystemExit("normalized health missing tables: " + ", ".join(missing))
    for table in sorted(required_tables):
        row = by_table[table]
        latest_ts = row.get("latest_ts")
        if latest_ts is None:
            raise SystemExit(f"normalized health missing latest_ts: table={table}")
        latest = _parse_timestamp(latest_ts)
        latest_age_ms = int((now - latest.astimezone(timezone.utc)).total_seconds() * 1000)
        if latest_age_ms > max_normalized_health_age_ms:
            raise SystemExit(
                f"normalized health stale: table={table} latest_age_ms={latest_age_ms}"
            )
```

- [ ] **Step 5: Run task verification**

Run:

```bash
uv run pytest tests/scripts/test_check_collector_status.py -q
```

Expected: all tests in that file pass.

- [ ] **Step 6: Commit**

Run:

```bash
git add scripts/check_collector_status.py tests/scripts/test_check_collector_status.py
git commit -m "Check normalized health freshness"
```

Expected: local commit created.

---

### Task 4: Add Normalizer Sidecar Service

**Files:**
- Create: `deploy/normalizer/Dockerfile`
- Create: `deploy/normalizer/normalizer-entrypoint.sh`
- Modify: `deploy/collector/docker-compose.yml`
- Modify: `scripts/deploy.sh`
- Modify: `tests/scripts/test_deploy_script.py`

- [ ] **Step 1: Write failing deploy tests for normalizer sidecar**

Append this test to `tests/scripts/test_deploy_script.py`:

```python
def test_normalizer_sidecar_is_deployed_and_health_checked() -> None:
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert "normalizer:" in compose
    assert "deploy/normalizer/Dockerfile" in compose
    assert "NORMALIZER_INTERVAL_SECONDS" in compose
    assert "polymarket-engine" in compose
    assert "--normalized-health-path" in compose
    assert "/var/lib/polymarket/live/normalized_health.json" in compose
    assert "up -d --build collector normalizer" in script
    assert "--normalized-health-path" in script
    assert "$DATA_DIR/live/normalized_health.json" in script
```

- [ ] **Step 2: Run failing deploy tests**

Run:

```bash
uv run pytest tests/scripts/test_deploy_script.py::test_normalizer_sidecar_is_deployed_and_health_checked -q
```

Expected: FAIL because the normalizer service and smoke checks are not wired.

- [ ] **Step 3: Create the normalizer Dockerfile**

Create `deploy/normalizer/Dockerfile`:

```dockerfile
FROM python:3.14-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY deploy/normalizer/normalizer-entrypoint.sh /usr/local/bin/normalizer-entrypoint.sh

RUN pip install --no-cache-dir . \
    && chmod 755 /usr/local/bin/normalizer-entrypoint.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/normalizer-entrypoint.sh"]
```

- [ ] **Step 4: Create the normalizer entrypoint**

Create `deploy/normalizer/normalizer-entrypoint.sh`:

```sh
#!/usr/bin/env sh
set -eu

RAW_DIR="${POLYMARKET_RAW_DIR:-/var/lib/polymarket/raw}"
DB_PATH="${POLYMARKET_DUCKDB_PATH:-/var/lib/polymarket/db/polymarket.duckdb}"
LIVE_DIR="${POLYMARKET_LIVE_DIR:-/var/lib/polymarket/live}"
STATUS_PATH="${POLYMARKET_STATUS_PATH:-$LIVE_DIR/status.json}"
NORMALIZED_HEALTH_PATH="${POLYMARKET_NORMALIZED_HEALTH_PATH:-$LIVE_DIR/normalized_health.json}"
INTERVAL_SECONDS="${POLYMARKET_NORMALIZER_INTERVAL_SECONDS:-5}"

if [ ! -f "$RAW_DIR/.polymarket_archive_root" ]; then
  echo "missing archive sentinel: $RAW_DIR/.polymarket_archive_root" >&2
  exit 66
fi

mkdir -p "$(dirname "$DB_PATH")" "$LIVE_DIR"

while true; do
  polymarket-engine normalize-rust-events \
    --raw-root "$RAW_DIR" \
    --duckdb-path "$DB_PATH" \
    --include-state-snapshots

  if [ -f "$STATUS_PATH" ]; then
    polymarket-engine build-current-decision-states \
      --duckdb-path "$DB_PATH" \
      --status-path "$STATUS_PATH" \
      --include-next
  fi

  polymarket-engine write-normalized-health \
    --duckdb-path "$DB_PATH" \
    --out "$NORMALIZED_HEALTH_PATH"

  sleep "$INTERVAL_SECONDS"
done
```

- [ ] **Step 5: Add normalizer service to compose**

In `deploy/collector/docker-compose.yml`, add this service under `services:` after the `collector` service:

```yaml
  normalizer:
    build:
      context: ../..
      dockerfile: deploy/normalizer/Dockerfile
    image: polymarket-normalizer:latest
    restart: unless-stopped
    user: "${POLYMARKET_UID:-1000}:${POLYMARKET_GID:-1000}"
    depends_on:
      - collector
    environment:
      TZ: ${POLYMARKET_DISPLAY_TZ:-America/Chicago}
      POLYMARKET_RAW_DIR: /var/lib/polymarket/raw
      POLYMARKET_DUCKDB_PATH: /var/lib/polymarket/db/polymarket.duckdb
      POLYMARKET_LIVE_DIR: /var/lib/polymarket/live
      POLYMARKET_STATUS_PATH: /var/lib/polymarket/live/status.json
      POLYMARKET_NORMALIZED_HEALTH_PATH: /var/lib/polymarket/live/normalized_health.json
      POLYMARKET_NORMALIZER_INTERVAL_SECONDS: ${POLYMARKET_NORMALIZER_INTERVAL_SECONDS:-5}
    volumes:
      - ${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}/raw:/var/lib/polymarket/raw
      - ${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}/db:/var/lib/polymarket/db
      - ${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}/live:/var/lib/polymarket/live
      - ${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}/logs:/var/lib/polymarket/logs
    healthcheck:
      test:
        [
          "CMD",
          "python",
          "-c",
          "import json, time; p='/var/lib/polymarket/live/normalized_health.json'; payload=json.load(open(p)); assert payload['schema_version']=='polymarket-normalized-health-v1'; assert time.time()-__import__('os').stat(p).st_mtime < 30",
        ]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s
```

- [ ] **Step 6: Update deploy script to start and smoke-check normalizer**

In `scripts/deploy.sh`, replace:

```bash
if ! compose -f "$COMPOSE_FILE" up -d --build collector >> "$LOG_FILE" 2>&1; then
```

with:

```bash
if ! compose -f "$COMPOSE_FILE" up -d --build collector normalizer >> "$LOG_FILE" 2>&1; then
```

In both `check_collector_status.py` invocations in `scripts/deploy.sh`, add these arguments after the raw journal arguments:

```bash
    --normalized-health-path "$DATA_DIR/live/normalized_health.json" \
    --max-normalized-health-age-ms 30000
```

The first deploy health check block should become:

```bash
  if python3 "$REPO/scripts/check_collector_status.py" \
    --status-path "$STATUS_PATH" \
    --max-status-age-seconds 30 \
    --max-price-age-ms 30000 \
    --max-orderbook-age-ms 30000 \
    --max-websocket-event-age-ms 30000 \
    --raw-root "$DATA_DIR/raw" \
    --max-raw-event-age-ms 30000 \
    --normalized-health-path "$DATA_DIR/live/normalized_health.json" \
    --max-normalized-health-age-ms 30000 >> "$LOG_FILE" 2>&1; then
```

Make the same change in the smoke loop block.

- [ ] **Step 7: Run deploy-script tests**

Run:

```bash
uv run pytest tests/scripts/test_deploy_script.py -q
```

Expected: all deploy script tests pass.

- [ ] **Step 8: Commit**

Run:

```bash
git add deploy/normalizer deploy/collector/docker-compose.yml scripts/deploy.sh tests/scripts/test_deploy_script.py
git commit -m "Run normalized database sidecar on spoon"
```

Expected: local commit created.

---

### Task 5: Document Spoon Database Expectations

**Files:**
- Modify: `docs/PART_TWO_LIVE_COLLECTORS.md`
- Modify: `docs/SPOON_DEPLOYMENT.md`
- Modify: `tests/docs/test_active_runtime_docs.py`

- [ ] **Step 1: Write failing docs tests**

Append this test to `tests/docs/test_active_runtime_docs.py`:

```python
def test_spoon_docs_describe_three_window_runtime_and_normalizer_sidecar() -> None:
    part_two = (ROOT / "docs" / "PART_TWO_LIVE_COLLECTORS.md").read_text(
        encoding="utf-8"
    )
    deployment = (ROOT / "docs" / "SPOON_DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "current, next, and next-next 5m windows" in part_two
    assert "POLYMARKET_PREWARM_WINDOWS=3" in deployment
    assert "normalizer sidecar" in part_two
    assert "normalized_health.json" in part_two
    assert "core.contract_rules remains empty" in part_two
    assert "features.decision_snapshots remains empty until probability" in part_two
    assert "origin/codex/rust-raw-normalizer" in deployment
```

- [ ] **Step 2: Run failing docs test**

Run:

```bash
uv run pytest tests/docs/test_active_runtime_docs.py::test_spoon_docs_describe_three_window_runtime_and_normalizer_sidecar -q
```

Expected: FAIL because docs do not yet include all required text.

- [ ] **Step 3: Update live collector docs**

In `docs/PART_TWO_LIVE_COLLECTORS.md`, add this paragraph under the Rust state-manager runtime section:

```markdown
The spoon deployment tracks current, next, and next-next 5m windows with `POLYMARKET_PREWARM_WINDOWS=3`. The collector process owns live WebSocket state and append-only raw JSONL journals. A normalizer sidecar reads those raw journals, writes normalized DuckDB rows, builds current/next `DecisionState` snapshots, and refreshes `data/live/normalized_health.json`.
```

Add this paragraph near the database caveat:

```markdown
Database expectation: `core.price_ticks`, `core.orderbook_snapshots`, and `features.asof_state_inputs` should stay fresh while the normalizer sidecar is running. `core.contract_rules remains empty` for Rust status-derived contracts because the Rust status file does not contain full venue rule text; do not synthesize rule text. `features.decision_snapshots remains empty until probability` because no probability model or decision policy exists yet.
```

- [ ] **Step 4: Update spoon deployment docs**

In `docs/SPOON_DEPLOYMENT.md`, add this line to the setup or environment section:

```markdown
Set `POLYMARKET_PREWARM_WINDOWS=3` or rely on the compose default so spoon warms BTC/ETH current, next, and next-next 5m windows.
```

Add this branch-coordination note near the deploy command:

```markdown
When testing the raw-normalizer deployment before merge, deploy explicitly with `POLYMARKET_DEPLOY_REF=origin/codex/rust-raw-normalizer`. Do not let spoon's local `main` remain ahead of `origin/main` after the branch is ready; either merge/push main or keep the deploy ref explicit.
```

- [ ] **Step 5: Run docs tests**

Run:

```bash
uv run pytest tests/docs/test_active_runtime_docs.py -q
```

Expected: all docs tests pass.

- [ ] **Step 6: Commit**

Run:

```bash
git add docs/PART_TWO_LIVE_COLLECTORS.md docs/SPOON_DEPLOYMENT.md tests/docs/test_active_runtime_docs.py
git commit -m "Document spoon collection health contract"
```

Expected: local commit created.

---

### Task 6: Full Local Verification And Branch Push

**Files:**
- Verify only.

- [ ] **Step 1: Run Python checks**

Run:

```bash
uv run ruff check .
uv run mypy src tests
uv run pytest -q
```

Expected: all checks pass.

- [ ] **Step 2: Run Rust checks**

Run:

```bash
cd rust
cargo test --workspace
```

Expected: all Rust tests pass.

- [ ] **Step 3: Inspect local branch state**

Run:

```bash
git status --short --branch
git log --oneline --decorate -8
```

Expected: only unrelated untracked `graphify-out/` may remain. New commits are on `codex/rust-raw-normalizer`.

- [ ] **Step 4: Push the feature branch**

Run:

```bash
git push -u origin codex/rust-raw-normalizer
```

Expected: branch push succeeds.

---

### Task 7: Deploy Spoon From The Raw-Normalizer Branch

**Files:**
- Remote deploy only after Task 6 passes.

- [ ] **Step 1: Deploy using explicit branch ref**

Run:

```bash
ssh spoon 'cd /home/spoon/polymarket && POLYMARKET_DEPLOY_REF=origin/codex/rust-raw-normalizer DEPLOY_FORCE=1 ./scripts/deploy.sh'
```

Expected: deploy succeeds. The deploy log should show the new commit SHA and `deploy OK`.

- [ ] **Step 2: Verify status, next-next, raw journals, and normalized health**

Run:

```bash
ssh spoon 'set -euo pipefail
REPO=/home/spoon/polymarket
DATA=/home/spoon/polymarket-data
python3 "$REPO/scripts/check_collector_status.py" \
  --status-path "$DATA/live/status.json" \
  --max-status-age-seconds 30 \
  --max-price-age-ms 30000 \
  --max-orderbook-age-ms 30000 \
  --max-websocket-event-age-ms 30000 \
  --raw-root "$DATA/raw" \
  --max-raw-event-age-ms 30000 \
  --normalized-health-path "$DATA/live/normalized_health.json" \
  --max-normalized-health-age-ms 30000
python3 "$REPO/scripts/verify_state_manager_report.py" "$DATA/live/status.json"
python3 - <<PY
import json
from pathlib import Path
status=json.loads(Path("$DATA/live/status.json").read_text())
health=json.loads(Path("$DATA/live/normalized_health.json").read_text())
print("windows", len(status["current"]), len(status["next"]), len(status["next_next"]))
for row in health["tables"]:
    print(row)
PY'
```

Expected:

```text
{'ok': True, ...}
ok mode=state-manager current=2 next=2 next_next=2 ...
windows 2 2 2
```

Normalized health should show fresh nonzero rows for:

```text
core.contracts
core.price_ticks
core.orderbook_snapshots
features.asof_state_inputs
```

It may show zero rows for:

```text
core.contract_rules
features.decision_snapshots
```

- [ ] **Step 3: Verify both containers are healthy**

Run:

```bash
ssh spoon 'docker compose -f /home/spoon/polymarket/deploy/collector/docker-compose.yml ps'
```

Expected: `collector` and `normalizer` are both running. Healthy status is preferred; if Compose has not yet marked health, re-run once after 30 seconds.

- [ ] **Step 4: Record deployment SHA**

Run:

```bash
ssh spoon 'cat /home/spoon/.polymarket/last-deployed-sha && cd /home/spoon/polymarket && git rev-parse HEAD && git status --short --branch'
```

Expected: marker and repo HEAD match the pushed branch SHA. The branch state should not imply an accidental downgrade path.

---

## Move-On Criteria

The project is ready to resume probability-output work only when all are true on spoon:

- `current=2`, `next=2`, and `next_next=2`.
- Chainlink BTC/USD and ETH/USD are fresh.
- CLOB orderbooks are fresh.
- Raw Chainlink and CLOB JSONL journals are fresh.
- `normalized_health.json` is fresh.
- `core.price_ticks`, `core.orderbook_snapshots`, and `features.asof_state_inputs` are nonzero and fresh.
- `core.contract_rules=0` is documented as a Rust-status limitation, not silently ignored.
- `features.decision_snapshots=0` is documented as expected until probability exists.
- Spoon deploy uses a pushed remote ref, not private local-ahead state.
