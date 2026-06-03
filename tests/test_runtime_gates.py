from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path

from fastapi.testclient import TestClient

from polymarket_engine.app import create_app
from polymarket_engine.runtime_gates import evaluate_runtime_gates


def _write_status(path: Path, *, generated_at: datetime) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "rust-live-probe-state-manager-v1",
                "mode": "state-manager",
                "generated_at": generated_at.isoformat(),
                "chainlink_prices": [
                    {
                        "source_key": "polymarket_rtds_chainlink",
                        "symbol": "BTC/USD",
                        "observed_ts": generated_at.isoformat(),
                        "price": 100.0,
                    }
                ],
                "orderbooks": [
                    {
                        "contract_id": "btc-5m-up",
                        "token_id": "token-1",
                        "observed_ts": generated_at.isoformat(),
                        "best_bid": 0.44,
                        "best_ask": 0.46,
                        "spread": 0.02,
                    }
                ],
                "health_flags": [],
            }
        ),
        encoding="utf-8",
    )


def test_gates_report_stale_status_without_raising(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "generated_at": (datetime.now(UTC) - timedelta(seconds=60)).isoformat(),
                "prices": [],
                "orderbooks": [],
                "health_flags": [],
            }
        ),
        encoding="utf-8",
    )

    payload = evaluate_runtime_gates(
        status_path=status_path,
        max_status_age_seconds=20,
    )

    assert payload["ok"] is False
    assert "status file stale" in payload["failures"]
    assert "status has no price rows" in payload["failures"]
    assert "status has no orderbook rows" in payload["failures"]


def test_gates_endpoint_returns_missing_normalized_health(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    normalized_health_path = tmp_path / "missing-normalized-health.json"
    _write_status(status_path, generated_at=datetime.now(UTC))
    app = create_app(
        status_path=status_path,
        normalized_health_path=normalized_health_path,
    )

    response = TestClient(app).get("/api/runtime/gates")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert "normalized health missing" in payload["failures"]
