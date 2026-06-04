# Polymarket Engine TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only Polymarket engine cockpit TUI from the useful parts of `penso/polymarket-tui`, with live engine status, source freshness, orderbook freshness, latency, storage, container status, and block reasons visible before any execution workflow exists.

**Architecture:** Create a new `polymarket-cockpit-tui` Rust crate in this repo and adapt only upstream's terminal shell patterns: ratatui layout, event loop, focused panels, logs, header status dots, keyboard handling, and orderbook rendering. Do not vendor upstream's API crate, auth files, trade popup, portfolio/favorites, or order-management surfaces. Serve all engine truth through FastAPI `/api/runtime/*` endpoints backed by `MonitorSnapshot`, `status.json`, `normalized_health.json`, and read-only gate logic.

**Tech Stack:** Rust 2024, ratatui, crossterm, tokio, reqwest 0.12, serde, Python FastAPI, DuckDB read-only access, pytest, cargo test.

---

## Source Findings

- Upstream commit inspected: `73e3a5a`.
- Reuse upstream terminal patterns from `/tmp/polymarket-tui-upstream/crates/cli/src/trending_tui/`: `event_loop.rs`, `layout.rs`, `render/main_render.rs`, `render/header.rs`, `render/orderbook.rs`, `state/navigation.rs`, and `state/orderbook.rs`.
- Do not reuse upstream auth or trade surfaces: `auth.rs`, `state/auth.rs`, `state/trade.rs`, trade popups, login/profile popups, portfolio/favorites polling, click-to-trade routing, and CLOB order-management types.
- Do not vendor upstream `polymarket-api`: it uses older dependency versions and includes balance/open-order/cancel/request structs that are outside this project's read-only boundary.
- Local engine truth already exists in `src/polymarket_engine/monitor.py`, `scripts/check_collector_status.py`, `rust/crates/polymarket-live-probe/src/report.rs`, and `src/polymarket_engine/health/normalized_status.py`.
- Local `data/live/status.json` may be stale or legacy-shaped, and `data/live/normalized_health.json` may be absent. The TUI must show `STALE`, `SCHEMA STALE`, or `MISSING`, not crash.

## Best Solution

Fork and reshape upstream by copying only the terminal UX skeleton into a new crate. This keeps the work easier to maintain than a full subtree while preserving attribution for copied MIT code.

Put engine status behind FastAPI before the TUI. A TUI that shells into Docker, SSH, or parser scripts directly will be fragile across Mac, spoon, PC, and VPS. The first API surfaces should be:

- `GET /health`
- `GET /api/runtime/status`
- `GET /api/runtime/monitor`
- `GET /api/runtime/normalized-health`
- `GET /api/runtime/gates`
- `GET /api/runtime/storage`
- `GET /api/runtime/containers`

Keep container status disabled unless `POLYMARKET_ENABLE_CONTAINER_STATUS=1`. Keep all endpoints read-only.

## Subagent Deployment Split

Use one worker per task with disjoint write ownership:

- Task 1 worker owns the new Rust TUI crate and provenance docs.
- Task 2 worker owns FastAPI monitor/status/storage endpoints and tests.
- Task 3 worker owns read-only gate analysis and tests.
- Task 4 worker owns Rust endpoint DTOs/client and tests.
- Task 5 worker owns TUI state, tabs, layout, and render helpers.
- Task 6 worker owns polling, CLI flags, docs, and smoke verification.

After each worker reports done, dispatch a spec reviewer subagent, then a code-quality reviewer subagent. Do not let workers edit overlapping files unless the previous task has been reviewed and committed.

---

### Task 1: Create The Read-Only Cockpit TUI Crate

**Files:**
- Modify: `rust/Cargo.toml`
- Create: `rust/crates/polymarket-cockpit-tui/Cargo.toml`
- Create: `rust/crates/polymarket-cockpit-tui/src/main.rs`
- Create: `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
- Create: `rust/crates/polymarket-cockpit-tui/src/state.rs`
- Create: `rust/crates/polymarket-cockpit-tui/src/layout.rs`
- Create: `rust/crates/polymarket-cockpit-tui/src/render/mod.rs`
- Create: `rust/crates/polymarket-cockpit-tui/src/render/header.rs`
- Create: `rust/crates/polymarket-cockpit-tui/src/render/logs.rs`
- Create: `rust/crates/polymarket-cockpit-tui/src/render/footer.rs`
- Create: `rust/crates/polymarket-cockpit-tui/src/render/orderbook.rs`
- Create: `rust/THIRD_PARTY.md`

- [ ] **Step 1: Write the failing crate-level read-only tests**

Create `rust/crates/polymarket-cockpit-tui/src/state.rs`:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MainTab {
    Live,
    Systems,
    Market,
    Logs,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppState {
    pub active_tab: MainTab,
    pub logs: Vec<String>,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            active_tab: MainTab::Live,
            logs: Vec::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{AppState, MainTab};

    #[test]
    fn cockpit_defaults_to_live_read_only_tab() {
        let app = AppState::default();

        assert_eq!(app.active_tab, MainTab::Live);
    }

    #[test]
    fn cockpit_tabs_are_operator_surfaces() {
        let labels: Vec<&'static str> = MainTab::all().iter().map(MainTab::label).collect();

        assert_eq!(labels, vec!["Live", "Systems", "Market", "Logs"]);
    }
}
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
cargo test -p polymarket-cockpit-tui cockpit_tabs_are_operator_surfaces
```

Expected: FAIL because the crate is not registered and `MainTab::all` does not exist.

- [ ] **Step 3: Add the crate and dependencies**

Modify `rust/Cargo.toml`:

```toml
[workspace]
resolver = "2"
members = [
    "crates/polymarket-runtime-types",
    "crates/polymarket-live-probe",
    "crates/polymarket-cockpit-tui",
]

[workspace.dependencies]
anyhow = "1.0"
chrono = { version = "0.4", features = ["serde"] }
clap = { version = "4.5", features = ["derive"] }
crossterm = "0.28"
futures = "0.3"
ratatui = "0.30"
reqwest = { version = "0.12", features = ["json", "rustls-tls"], default-features = false }
rustls = { version = "0.23", default-features = false, features = ["aws_lc_rs"] }
rust_decimal = { version = "1.36", features = ["serde"] }
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
tokio = { version = "1.40", features = ["rt-multi-thread", "macros", "time", "sync"] }
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter"] }
unicode-width = "0.2"
polymarket_client_sdk_v2 = { git = "https://github.com/Polymarket/rs-clob-client-v2", features = ["clob", "gamma", "rtds", "ws", "tracing"] }
```

Create `rust/crates/polymarket-cockpit-tui/Cargo.toml`:

```toml
[package]
name = "polymarket-cockpit-tui"
version = "0.1.0"
edition = "2024"
license = "MIT"
description = "Read-only terminal cockpit for the Polymarket binary engine"

[[bin]]
name = "polymarket-cockpit-tui"
path = "src/main.rs"

[dependencies]
anyhow = { workspace = true }
chrono = { workspace = true }
clap = { workspace = true }
crossterm = { workspace = true }
ratatui = { workspace = true }
reqwest = { workspace = true }
serde = { workspace = true }
serde_json = { workspace = true }
tokio = { workspace = true }
tracing = { workspace = true }
tracing-subscriber = { workspace = true }
unicode-width = { workspace = true }
```

- [ ] **Step 4: Implement the minimal state API**

Update `state.rs`:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MainTab {
    Live,
    Systems,
    Market,
    Logs,
}

impl MainTab {
    pub fn all() -> &'static [MainTab] {
        &[MainTab::Live, MainTab::Systems, MainTab::Market, MainTab::Logs]
    }

    pub fn label(&self) -> &'static str {
        match self {
            MainTab::Live => "Live",
            MainTab::Systems => "Systems",
            MainTab::Market => "Market",
            MainTab::Logs => "Logs",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AppState {
    pub active_tab: MainTab,
    pub logs: Vec<String>,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            active_tab: MainTab::Live,
            logs: Vec::new(),
        }
    }
}
```

- [ ] **Step 5: Add shell modules adapted from upstream**

Copy/adapt only these upstream files:

```bash
cp /tmp/polymarket-tui-upstream/crates/cli/src/trending_tui/layout.rs rust/crates/polymarket-cockpit-tui/src/layout.rs
cp /tmp/polymarket-tui-upstream/crates/cli/src/trending_tui/render/logs.rs rust/crates/polymarket-cockpit-tui/src/render/logs.rs
```

Then strip imports that reference upstream auth, trade, favorites, yield, and `polymarket_api`.

Create minimal `main.rs`, `event_loop.rs`, `render/mod.rs`, `render/header.rs`, `render/footer.rs`, and `render/orderbook.rs` that compile and use `AppState`. The first binary may render static placeholder panels, but it must not contain `AuthConfig`, `TradeForm`, `OrderRequest`, `Balance`, `Cancel`, `Login`, `Buy`, or `Sell` symbols.

- [ ] **Step 6: Preserve upstream MIT provenance**

Create `rust/THIRD_PARTY.md`:

```markdown
# Third Party Rust Code

## penso/polymarket-tui

- Source: https://github.com/penso/polymarket-tui
- Inspected commit: 73e3a5a
- License: MIT
- Copyright: Copyright (c) 2026 Fabien Penso
- Use in this repo: selected terminal UI patterns adapted into `crates/polymarket-cockpit-tui`.

Only read-only terminal layout, event-loop, panel, log, header, and orderbook rendering patterns may be reused in the cockpit. Auth, account, trade popup, balance, order, and cancellation surfaces are intentionally excluded.
```

- [ ] **Step 7: Verify no forbidden trading/auth strings are present**

Run:

```bash
cargo test -p polymarket-cockpit-tui cockpit_tabs_are_operator_surfaces
rg -n "AuthConfig|TradeForm|OrderRequest|Balance|Cancel|Login|Buy|Sell|private_key|api_secret" rust/crates/polymarket-cockpit-tui
```

Expected: tests PASS and `rg` finds no forbidden strings.

- [ ] **Step 8: Commit**

Run:

```bash
git add rust/Cargo.toml rust/THIRD_PARTY.md rust/crates/polymarket-cockpit-tui
git commit -m "feat: add read-only cockpit tui crate"
```

---

### Task 2: Add Runtime Monitor And Storage API

**Files:**
- Modify: `src/polymarket_engine/app.py`
- Create: `src/polymarket_engine/runtime_api.py`
- Create: `tests/test_runtime_api.py`

- [ ] **Step 1: Write failing FastAPI tests**

Create `tests/test_runtime_api.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import json

from fastapi.testclient import TestClient

from polymarket_engine.app import create_app


def _write_status(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "rust-live-probe-state-manager-v1",
                "mode": "state-manager",
                "generated_at": datetime.now(UTC).isoformat(),
                "chainlink_prices": [
                    {
                        "source_key": "polymarket_rtds_chainlink",
                        "symbol": "BTC/USD",
                        "observed_ts": datetime.now(UTC).isoformat(),
                        "price": 100.0,
                    }
                ],
                "current": [],
                "next": [],
                "next_next": [],
                "orderbooks": [
                    {
                        "contract_id": "btc-5m-up",
                        "token_id": "token-1",
                        "observed_ts": datetime.now(UTC).isoformat(),
                        "best_bid": 0.44,
                        "best_ask": 0.46,
                        "spread": 0.02,
                    }
                ],
                "freshness": [],
                "health_flags": [],
                "websocket_status": [],
                "latency_marks": [{"name": "current_orderbook_age_ms", "elapsed_ms": 3}],
            }
        ),
        encoding="utf-8",
    )


def test_runtime_status_reads_state_manager_file(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    _write_status(status_path)
    app = create_app(status_path=status_path)

    response = TestClient(app).get("/api/runtime/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["schema_kind"] == "rust-live-probe-state-manager-v1"
    assert payload["mode"] == "state-manager"
    assert payload["counts"]["prices"] == 1
    assert payload["counts"]["orderbooks"] == 1


def test_runtime_monitor_returns_json_safe_snapshot(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    _write_status(status_path)
    app = create_app(status_path=status_path)

    response = TestClient(app).get("/api/runtime/monitor")

    assert response.status_code == 200
    payload = response.json()
    assert payload["price_rows"][0]["symbol"] == "BTC/USD"
    assert payload["orderbooks"][0]["contract_id"] == "btc-5m-up"
    assert payload["health_flags"] == []
    assert "prices" not in payload


def test_runtime_storage_reports_data_dir_size(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "sample.parquet").write_bytes(b"x" * 128)
    app = create_app(data_dir=data_dir)

    response = TestClient(app).get("/api/runtime/storage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["data_dir"] == str(data_dir)
    assert payload["bytes"] >= 128
    assert payload["children"][0]["name"] == "raw"


def test_runtime_containers_disabled_by_default() -> None:
    app = create_app()

    response = TestClient(app).get("/api/runtime/containers")

    assert response.status_code == 403
    assert response.json()["detail"] == "container status disabled"
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
uv run pytest -q tests/test_runtime_api.py
```

Expected: FAIL because `create_app` and `/api/runtime/*` endpoints do not exist.

- [ ] **Step 3: Implement app factory and runtime router**

Create `src/polymarket_engine/runtime_api.py`:

```python
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os
import subprocess

from fastapi import APIRouter, HTTPException

from polymarket_engine.monitor import MonitorSnapshot, fetch_monitor_snapshot


def build_runtime_router(
    *,
    status_path: Path = Path("data/live/status.json"),
    duckdb_path: Path = Path("data/db/polymarket.duckdb"),
    normalized_health_path: Path = Path("data/live/normalized_health.json"),
    data_dir: Path = Path("data"),
    enable_container_status: bool = False,
) -> APIRouter:
    router = APIRouter(prefix="/api/runtime")

    @router.get("/status")
    def runtime_status() -> dict[str, Any]:
        payload = _read_json(status_path)
        generated_at = _parse_timestamp(payload.get("generated_at"))
        prices = payload.get("prices") or payload.get("chainlink_prices") or []
        orderbooks = payload.get("orderbooks") or []
        return {
            "ok": not bool(payload.get("health_flags")),
            "status_path": str(status_path),
            "schema_kind": payload.get("schema_version", "legacy"),
            "mode": payload.get("mode", "legacy"),
            "generated_at": payload.get("generated_at"),
            "age_ms": _age_ms(generated_at),
            "counts": {
                "prices": len(prices),
                "orderbooks": len(orderbooks),
                "current": len(payload.get("current", [])),
                "next": len(payload.get("next", [])),
                "next_next": len(payload.get("next_next", [])),
                "websocket_status": len(payload.get("websocket_status", [])),
            },
            "websocket_status": payload.get("websocket_status", []),
            "latency_marks": payload.get("latency_marks", []),
            "source_errors": payload.get("source_errors", {}),
            "health_flags": payload.get("health_flags", []),
        }

    @router.get("/monitor")
    def runtime_monitor(limit: int = 12) -> dict[str, Any]:
        snapshot = fetch_monitor_snapshot(
            duckdb_path=duckdb_path,
            limit=limit,
            status_path=status_path if status_path.exists() else None,
        )
        return _snapshot_to_json(snapshot)

    @router.get("/normalized-health")
    def normalized_health() -> dict[str, Any]:
        if not normalized_health_path.exists():
            return {
                "ok": False,
                "path": str(normalized_health_path),
                "state": "MISSING",
                "error": "normalized health file missing",
                "tables": [],
            }
        payload = _read_json(normalized_health_path)
        return {
            "ok": payload.get("schema_version") == "polymarket-normalized-health-v1",
            "path": str(normalized_health_path),
            "state": "OK"
            if payload.get("schema_version") == "polymarket-normalized-health-v1"
            else "SCHEMA STALE",
            "schema_version": payload.get("schema_version"),
            "generated_at": payload.get("generated_at"),
            "tables": payload.get("tables", []),
        }

    @router.get("/storage")
    def storage() -> dict[str, Any]:
        children = []
        if data_dir.exists():
            for child in sorted(data_dir.iterdir(), key=lambda item: item.name):
                children.append({"name": child.name, "bytes": _path_size(child)})
        return {"data_dir": str(data_dir), "bytes": _path_size(data_dir), "children": children}

    @router.get("/containers")
    def containers() -> dict[str, Any]:
        if not enable_container_status:
            raise HTTPException(status_code=403, detail="container status disabled")
        result = subprocess.run(
            ["docker", "compose", "ps", "--format", "json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return {
            "ok": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    return router


def container_status_enabled_from_env() -> bool:
    return os.getenv("POLYMARKET_ENABLE_CONTAINER_STATUS") == "1"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{path} not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot_to_json(snapshot: MonitorSnapshot) -> dict[str, Any]:
    payload = asdict(snapshot)
    payload.pop("prices", None)
    payload["generated_at"] = snapshot.generated_at.isoformat()
    return payload


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    raw = str(value)
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _age_ms(value: datetime | None) -> int | None:
    if value is None:
        return None
    return int((datetime.now(timezone.utc) - value.astimezone(timezone.utc)).total_seconds() * 1000)


def _path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
```

Modify `src/polymarket_engine/app.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from polymarket_engine.runtime_api import build_runtime_router, container_status_enabled_from_env


def create_app(
    *,
    status_path: Path = Path("data/live/status.json"),
    duckdb_path: Path = Path("data/db/polymarket.duckdb"),
    normalized_health_path: Path = Path("data/live/normalized_health.json"),
    data_dir: Path = Path("data"),
    enable_container_status: bool | None = None,
) -> FastAPI:
    app = FastAPI(title="Polymarket Engine", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(
        build_runtime_router(
            status_path=status_path,
            duckdb_path=duckdb_path,
            normalized_health_path=normalized_health_path,
            data_dir=data_dir,
            enable_container_status=container_status_enabled_from_env()
            if enable_container_status is None
            else enable_container_status,
        )
    )
    return app


app = create_app()
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
uv run pytest -q tests/test_runtime_api.py tests/test_health.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/polymarket_engine/app.py src/polymarket_engine/runtime_api.py tests/test_runtime_api.py tests/test_health.py
git commit -m "feat: expose runtime monitor api"
```

---

### Task 3: Add Read-Only Runtime Gate Analysis

**Files:**
- Create: `src/polymarket_engine/runtime_gates.py`
- Modify: `src/polymarket_engine/runtime_api.py`
- Create: `tests/test_runtime_gates.py`

- [ ] **Step 1: Write failing gate tests**

Create `tests/test_runtime_gates.py`:

```python
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import json

from fastapi.testclient import TestClient

from polymarket_engine.app import create_app
from polymarket_engine.runtime_gates import evaluate_runtime_gates


def test_gates_report_stale_status_without_raising(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "schema_version": "rust-live-probe-state-manager-v1",
                "mode": "state-manager",
                "generated_at": (datetime.now(UTC) - timedelta(seconds=60)).isoformat(),
                "chainlink_prices": [],
                "orderbooks": [],
                "health_flags": [],
            }
        ),
        encoding="utf-8",
    )

    result = evaluate_runtime_gates(status_path=status_path, max_status_age_seconds=20)

    assert result["ok"] is False
    assert any("status file stale" in failure for failure in result["failures"])
    assert any("status has no price rows" in failure for failure in result["failures"])


def test_gates_endpoint_returns_missing_normalized_health(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "prices": [{"observed_ts": datetime.now(UTC).isoformat()}],
                "orderbooks": [{"observed_ts": datetime.now(UTC).isoformat()}],
                "health_flags": [],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(status_path=status_path, normalized_health_path=tmp_path / "missing.json")

    response = TestClient(app).get("/api/runtime/gates")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert any("normalized health missing" in failure for failure in payload["failures"])
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
uv run pytest -q tests/test_runtime_gates.py
```

Expected: FAIL because `runtime_gates.py` and `/api/runtime/gates` do not exist.

- [ ] **Step 3: Implement non-raising gate evaluator**

Create `src/polymarket_engine/runtime_gates.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json


def evaluate_runtime_gates(
    *,
    status_path: Path,
    normalized_health_path: Path | None = None,
    max_status_age_seconds: float = 30.0,
    max_price_age_ms: int = 30_000,
    max_orderbook_age_ms: int = 30_000,
    expected_prewarm_windows: int = 2,
) -> dict[str, Any]:
    failures: list[str] = []
    payload: dict[str, Any] = {}
    if not status_path.exists():
        failures.append(f"status missing: path={status_path}")
    else:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        generated_at = _parse_timestamp(payload.get("generated_at"))
        if generated_at is None:
            failures.append("status missing generated_at")
        else:
            status_age_seconds = (
                datetime.now(timezone.utc) - generated_at.astimezone(timezone.utc)
            ).total_seconds()
            if status_age_seconds > max_status_age_seconds:
                failures.append(f"status file stale: age_seconds={status_age_seconds:.2f}")
        prices = payload.get("prices") or payload.get("chainlink_prices") or []
        orderbooks = payload.get("orderbooks") or []
        if not prices:
            failures.append("status has no price rows")
        if not orderbooks:
            failures.append("status has no orderbook rows")
        _append_age_failure(
            failures,
            rows=prices,
            label="price rows",
            max_age_ms=max_price_age_ms,
        )
        _append_age_failure(
            failures,
            rows=orderbooks,
            label="orderbook rows",
            max_age_ms=max_orderbook_age_ms,
        )
        if payload.get("mode") == "state-manager":
            if payload.get("schema_version") != "rust-live-probe-state-manager-v1":
                failures.append("state-manager status has unexpected schema_version")
            if len(payload.get("current", [])) < 2:
                failures.append("state-manager missing current BTC/ETH contracts")
            if expected_prewarm_windows >= 2 and len(payload.get("next", [])) < 2:
                failures.append("state-manager missing next BTC/ETH contracts")
            failures.extend(str(flag) for flag in payload.get("health_flags", []))
    if normalized_health_path is not None:
        if not normalized_health_path.exists():
            failures.append(f"normalized health missing: path={normalized_health_path}")
        else:
            normalized = json.loads(normalized_health_path.read_text(encoding="utf-8"))
            if normalized.get("schema_version") != "polymarket-normalized-health-v1":
                failures.append("normalized health has unexpected schema_version")
    return {
        "ok": not failures,
        "status_path": str(status_path),
        "normalized_health_path": str(normalized_health_path)
        if normalized_health_path is not None
        else None,
        "thresholds": {
            "max_status_age_seconds": max_status_age_seconds,
            "max_price_age_ms": max_price_age_ms,
            "max_orderbook_age_ms": max_orderbook_age_ms,
            "expected_prewarm_windows": expected_prewarm_windows,
        },
        "failures": failures,
    }


def _append_age_failure(
    failures: list[str],
    *,
    rows: list[dict[str, Any]],
    label: str,
    max_age_ms: int,
) -> None:
    newest = None
    for row in rows:
        observed = _parse_timestamp(row.get("observed_ts"))
        if observed is not None and (newest is None or observed > newest):
            newest = observed
    if newest is None:
        return
    age_ms = int((datetime.now(timezone.utc) - newest.astimezone(timezone.utc)).total_seconds() * 1000)
    if age_ms > max_age_ms:
        failures.append(f"{label} stale: age_ms={age_ms}")


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    raw = str(value)
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
```

Add `/api/runtime/gates` in `runtime_api.py`:

```python
from polymarket_engine.runtime_gates import evaluate_runtime_gates

@router.get("/gates")
def runtime_gates() -> dict[str, Any]:
    return evaluate_runtime_gates(
        status_path=status_path,
        normalized_health_path=normalized_health_path,
    )
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
uv run pytest -q tests/test_runtime_gates.py tests/test_runtime_api.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/polymarket_engine/runtime_gates.py src/polymarket_engine/runtime_api.py tests/test_runtime_gates.py
git commit -m "feat: report runtime gates for cockpit"
```

---

### Task 4: Add Rust Runtime DTOs And Client

**Files:**
- Create: `rust/crates/polymarket-cockpit-tui/src/status.rs`
- Create: `rust/crates/polymarket-cockpit-tui/src/client.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/main.rs`

- [ ] **Step 1: Write failing DTO tests**

Create `rust/crates/polymarket-cockpit-tui/src/status.rs`:

```rust
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeStatus {
    pub ok: bool,
    pub schema_kind: String,
    pub mode: String,
    pub age_ms: Option<u64>,
    pub counts: RuntimeCounts,
    pub latency_marks: Vec<RuntimeLatencyMark>,
    pub health_flags: Vec<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeCounts {
    pub prices: usize,
    pub orderbooks: usize,
    pub current: usize,
    pub next: usize,
    pub next_next: usize,
    pub websocket_status: usize,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeLatencyMark {
    pub name: String,
    pub elapsed_ms: Option<u64>,
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct RuntimeGates {
    pub ok: bool,
    pub failures: Vec<String>,
}

impl RuntimeStatus {
    pub fn state_label(&self) -> &'static str {
        if self.ok && self.health_flags.is_empty() {
            "OK"
        } else {
            "BLOCKED"
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{RuntimeGates, RuntimeStatus};

    #[test]
    fn status_payload_parses_and_labels_ok() {
        let payload = r#"{
            "ok": true,
            "schema_kind": "rust-live-probe-state-manager-v1",
            "mode": "state-manager",
            "age_ms": 10,
            "counts": {"prices": 2, "orderbooks": 4, "current": 2, "next": 2, "next_next": 0, "websocket_status": 2},
            "latency_marks": [{"name": "current_orderbook_age_ms", "elapsed_ms": 3}],
            "health_flags": []
        }"#;

        let status: RuntimeStatus = serde_json::from_str(payload).unwrap();

        assert_eq!(status.state_label(), "OK");
        assert_eq!(status.counts.current, 2);
    }

    #[test]
    fn gate_payload_keeps_block_reasons() {
        let payload = r#"{"ok": false, "failures": ["status file stale"]}"#;

        let gates: RuntimeGates = serde_json::from_str(payload).unwrap();

        assert!(!gates.ok);
        assert_eq!(gates.failures, vec!["status file stale"]);
    }
}
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
cargo test -p polymarket-cockpit-tui status_payload_parses_and_labels_ok
```

Expected: FAIL because `status` is not registered in the crate.

- [ ] **Step 3: Register DTOs and add client**

Add in `main.rs`:

```rust
mod client;
mod status;
```

Create `client.rs`:

```rust
use crate::status::{RuntimeGates, RuntimeStatus};

#[derive(Debug, Clone)]
pub struct EngineClient {
    base_url: String,
    client: reqwest::Client,
}

impl EngineClient {
    pub fn new(base_url: impl Into<String>) -> Self {
        Self {
            base_url: base_url.into().trim_end_matches('/').to_string(),
            client: reqwest::Client::new(),
        }
    }

    pub async fn status(&self) -> anyhow::Result<RuntimeStatus> {
        self.get_json("/api/runtime/status").await
    }

    pub async fn gates(&self) -> anyhow::Result<RuntimeGates> {
        self.get_json("/api/runtime/gates").await
    }

    async fn get_json<T>(&self, path: &str) -> anyhow::Result<T>
    where
        T: serde::de::DeserializeOwned,
    {
        let url = format!("{}{}", self.base_url, path);
        Ok(self.client.get(url).send().await?.error_for_status()?.json().await?)
    }
}
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
cargo test -p polymarket-cockpit-tui status_payload_parses_and_labels_ok gate_payload_keeps_block_reasons
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add rust/crates/polymarket-cockpit-tui/src/status.rs rust/crates/polymarket-cockpit-tui/src/client.rs rust/crates/polymarket-cockpit-tui/src/main.rs
git commit -m "feat: add cockpit runtime status client"
```

---

### Task 5: Render Live, Systems, Market, And Logs Panels

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/state.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/header.rs`
- Create: `rust/crates/polymarket-cockpit-tui/src/render/live.rs`
- Create: `rust/crates/polymarket-cockpit-tui/src/render/systems.rs`
- Create: `rust/crates/polymarket-cockpit-tui/src/render/market.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/mod.rs`

- [ ] **Step 1: Write failing render summary tests**

Create tests in `render/systems.rs`:

```rust
use crate::state::AppState;

pub fn systems_summary_lines(app: &AppState) -> Vec<String> {
    match (&app.runtime_status, &app.runtime_gates) {
        (Some(status), Some(gates)) => {
            let mut lines = vec![
                format!("Engine API {}", status.state_label()),
                format!("status_age_ms={}", status.age_ms.map_or("-".to_string(), |value| value.to_string())),
                format!("prices={}", status.counts.prices),
                format!("orderbooks={}", status.counts.orderbooks),
            ];
            for failure in &gates.failures {
                lines.push(format!("block={failure}"));
            }
            lines
        }
        (Some(status), None) => vec![format!("Engine API {}", status.state_label())],
        _ => vec!["Engine API UNKNOWN".to_string()],
    }
}

#[cfg(test)]
mod tests {
    use crate::{
        status::{RuntimeCounts, RuntimeGates, RuntimeStatus},
        state::AppState,
    };

    use super::systems_summary_lines;

    #[test]
    fn systems_summary_shows_counts_and_gate_failures() {
        let mut app = AppState::default();
        app.runtime_status = Some(RuntimeStatus {
            ok: false,
            schema_kind: "rust-live-probe-state-manager-v1".to_string(),
            mode: "state-manager".to_string(),
            age_ms: Some(42),
            counts: RuntimeCounts {
                prices: 2,
                orderbooks: 4,
                current: 2,
                next: 2,
                next_next: 0,
                websocket_status: 2,
            },
            latency_marks: vec![],
            health_flags: vec!["source stale".to_string()],
        });
        app.runtime_gates = Some(RuntimeGates {
            ok: false,
            failures: vec!["status file stale".to_string()],
        });

        let text = systems_summary_lines(&app).join("\n");

        assert!(text.contains("Engine API BLOCKED"));
        assert!(text.contains("status_age_ms=42"));
        assert!(text.contains("prices=2"));
        assert!(text.contains("block=status file stale"));
    }
}
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
cargo test -p polymarket-cockpit-tui systems_summary_shows_counts_and_gate_failures
```

Expected: FAIL because `runtime_status`, `runtime_gates`, and render module wiring are missing.

- [ ] **Step 3: Add runtime fields and render modules**

Update `state.rs`:

```rust
use crate::status::{RuntimeGates, RuntimeStatus};

#[derive(Debug, Clone, PartialEq)]
pub struct AppState {
    pub active_tab: MainTab,
    pub logs: Vec<String>,
    pub runtime_status: Option<RuntimeStatus>,
    pub runtime_gates: Option<RuntimeGates>,
    pub runtime_error: Option<String>,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            active_tab: MainTab::Live,
            logs: Vec::new(),
            runtime_status: None,
            runtime_gates: None,
            runtime_error: None,
        }
    }
}
```

Add `live.rs`, `systems.rs`, and `market.rs` renderers that convert current state into ratatui `Paragraph` or `List` widgets. The `Live` panel should show prices/orderbooks from monitor data after Task 6, but in this task it may show status counts and latest latency marks.

Update `render/mod.rs`:

```rust
pub mod footer;
pub mod header;
pub mod live;
pub mod logs;
pub mod market;
pub mod orderbook;
pub mod systems;
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
cargo test -p polymarket-cockpit-tui systems_summary_shows_counts_and_gate_failures cockpit_tabs_are_operator_surfaces
```

Expected: PASS.

- [ ] **Step 5: Commit**

Run:

```bash
git add rust/crates/polymarket-cockpit-tui/src/state.rs rust/crates/polymarket-cockpit-tui/src/render
git commit -m "feat: render cockpit status panels"
```

---

### Task 6: Wire Polling, CLI Flags, Docs, And Smoke Checks

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/main.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
- Modify: `docs/SPOON_DEPLOYMENT.md`
- Modify: `docs/PLAN.md`
- Modify: `tests/docs/test_active_runtime_docs.py`

- [ ] **Step 1: Write failing CLI and docs tests**

Add CLI tests in `main.rs`:

```rust
#[cfg(test)]
mod cli_tests {
    use clap::Parser;

    use super::Cli;

    #[test]
    fn default_engine_api_url_is_localhost() {
        let cli = Cli::parse_from(["polymarket-cockpit-tui"]);

        assert_eq!(cli.engine_api_url, "http://127.0.0.1:8000");
    }

    #[test]
    fn custom_engine_api_url_is_accepted() {
        let cli = Cli::parse_from([
            "polymarket-cockpit-tui",
            "--engine-api-url",
            "http://100.126.126.1:8082",
        ]);

        assert_eq!(cli.engine_api_url, "http://100.126.126.1:8082");
    }
}
```

Add docs test to `tests/docs/test_active_runtime_docs.py`:

```python
def test_spoon_docs_include_read_only_cockpit_tui() -> None:
    text = (ROOT / "docs" / "SPOON_DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "polymarket-cockpit-tui" in text
    assert "--engine-api-url" in text
    assert "read-only" in text
    assert "POLYMARKET_ENABLE_CONTAINER_STATUS=1" in text
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
cargo test -p polymarket-cockpit-tui default_engine_api_url_is_localhost
uv run pytest -q tests/docs/test_active_runtime_docs.py::test_spoon_docs_include_read_only_cockpit_tui
```

Expected: both FAIL because CLI flags and docs are missing.

- [ ] **Step 3: Add CLI config and polling loop**

In `main.rs`, add:

```rust
use clap::Parser;

#[derive(Debug, Clone, Parser)]
pub struct Cli {
    #[arg(long, default_value = "http://127.0.0.1:8000")]
    pub engine_api_url: String,
}
```

In `event_loop.rs`, poll every second:

```rust
let client = crate::client::EngineClient::new(engine_api_url);
let mut interval = tokio::time::interval(std::time::Duration::from_secs(1));
loop {
    interval.tick().await;
    match client.status().await {
        Ok(status) => app.runtime_status = Some(status),
        Err(error) => app.runtime_error = Some(error.to_string()),
    }
    match client.gates().await {
        Ok(gates) => app.runtime_gates = Some(gates),
        Err(error) => app.runtime_error = Some(error.to_string()),
    }
}
```

Keep this polling read-only. It must not call deploy scripts, write status files, restart containers, or submit orders.

- [ ] **Step 4: Update docs**

Add to `docs/SPOON_DEPLOYMENT.md`:

```markdown
## Read-Only Cockpit TUI

The cockpit TUI is read-only. It polls the engine API for status, gate failures,
freshness, latency, storage, and optional container state. It must not place
orders, deploy containers, rebuild images, or write collector state.

Local:

```bash
uvicorn polymarket_engine.app:app --host 127.0.0.1 --port 8000
cargo run --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui -- --engine-api-url http://127.0.0.1:8000
```

Spoon over Tailscale:

```bash
cargo run --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui -- --engine-api-url http://100.126.126.1:8082
```

Container status is operator-only and remains disabled unless the API process is
started with `POLYMARKET_ENABLE_CONTAINER_STATUS=1`.
```

Add to `docs/PLAN.md`:

```markdown
The first TUI is an operator cockpit, not a trade terminal. The first tabs are
Live, Systems, Market, and Logs. It should explain freshness, latency, health,
storage, and block reasons before it offers any paper or live execution workflow.
```

- [ ] **Step 5: Run focused verification**

Run:

```bash
uv run pytest -q tests/test_runtime_api.py tests/test_runtime_gates.py tests/test_health.py tests/docs/test_active_runtime_docs.py
cargo test --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui
```

Expected: PASS.

- [ ] **Step 6: Run smoke checks**

Run in one terminal:

```bash
uvicorn polymarket_engine.app:app --host 127.0.0.1 --port 8000
```

Run in another terminal:

```bash
curl -s http://127.0.0.1:8000/api/runtime/status
timeout 5 cargo run --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui -- --engine-api-url http://127.0.0.1:8000
```

Expected: the API returns JSON and the TUI starts without auth prompts, trade prompts, or order-placement UI.

- [ ] **Step 7: Commit**

Run:

```bash
git add rust/crates/polymarket-cockpit-tui docs/SPOON_DEPLOYMENT.md docs/PLAN.md tests/docs/test_active_runtime_docs.py
git commit -m "feat: wire read-only cockpit tui"
```

---

## Execution Gate

Stop here until Enoch approves execution. This plan touches Python API code, the Rust workspace, a new terminal UI crate, docs, and deployment-facing operator surfaces. Recommended execution: subagent-driven development, one worker per task, with spec and code-quality review after each task.
