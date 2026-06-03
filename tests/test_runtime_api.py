from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import json
import subprocess

import duckdb
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


def test_runtime_status_malformed_json_returns_controlled_state(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text("{not-json", encoding="utf-8")
    app = create_app(status_path=status_path)

    response = TestClient(app, raise_server_exceptions=False).get("/api/runtime/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "INVALID"
    assert "JSON" in payload["error"]
    assert payload["counts"]["prices"] == 0
    assert payload["counts"]["orderbooks"] == 0


def test_runtime_status_malformed_generated_at_returns_controlled_state(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    _write_status(status_path)
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    payload["generated_at"] = "not-a-timestamp"
    status_path.write_text(json.dumps(payload), encoding="utf-8")
    app = create_app(status_path=status_path)

    response = TestClient(app, raise_server_exceptions=False).get("/api/runtime/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "INVALID"
    assert "generated_at" in payload["error"]
    assert payload["counts"]["prices"] == 1
    assert payload["counts"]["orderbooks"] == 1


def test_runtime_status_wrong_field_shapes_return_controlled_state(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "prices": 1,
                "orderbooks": None,
                "websocket_status": None,
            }
        ),
        encoding="utf-8",
    )
    app = create_app(status_path=status_path)

    response = TestClient(app, raise_server_exceptions=False).get("/api/runtime/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "INVALID"
    assert "status shape invalid" in payload["error"]
    assert payload["counts"]["prices"] == 0
    assert payload["counts"]["orderbooks"] == 0
    assert payload["counts"]["websocket_status"] == 0


def test_runtime_status_wrong_object_shape_returns_controlled_state(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "prices": [],
                "orderbooks": [],
                "source_errors": [],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(status_path=status_path)

    response = TestClient(app, raise_server_exceptions=False).get("/api/runtime/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "INVALID"
    assert "source_errors" in payload["error"]
    assert isinstance(payload["source_errors"], dict)


def test_runtime_status_bad_price_row_contents_return_controlled_state(
    tmp_path: Path,
) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "chainlink_prices": [{}],
                "orderbooks": [],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(status_path=status_path)

    response = TestClient(app, raise_server_exceptions=False).get("/api/runtime/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "INVALID"
    assert "status shape invalid" in payload["error"]
    assert "missing required price field" in payload["error"]
    assert payload["counts"]["prices"] == 1
    assert payload["counts"]["orderbooks"] == 0


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


def test_runtime_monitor_wrong_row_shapes_return_empty_envelope(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "prices": [1],
                "orderbooks": [1],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(status_path=status_path)

    response = TestClient(app, raise_server_exceptions=False).get("/api/runtime/monitor")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "INVALID"
    assert "status shape invalid" in payload["error"]
    assert payload["price_rows"] == []
    assert payload["orderbooks"] == []
    assert "prices" not in payload


def test_runtime_monitor_bad_price_row_contents_return_empty_envelope(
    tmp_path: Path,
) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "chainlink_prices": [{}],
                "orderbooks": [],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(status_path=status_path)

    response = TestClient(app, raise_server_exceptions=False).get("/api/runtime/monitor")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "INVALID"
    assert "status shape invalid" in payload["error"]
    assert "missing required price field" in payload["error"]
    assert payload["price_rows"] == []
    assert payload["orderbooks"] == []
    assert "prices" not in payload


def test_runtime_monitor_parseable_schema_malformed_status_returns_empty_envelope(
    tmp_path: Path,
) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps({"schema_version": "rust-live-probe-state-manager-v1"}),
        encoding="utf-8",
    )
    app = create_app(status_path=status_path)

    response = TestClient(app, raise_server_exceptions=False).get("/api/runtime/monitor")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "INVALID"
    assert "status shape invalid" in payload["error"]
    assert payload["price_rows"] == []
    assert payload["orderbooks"] == []
    assert "prices" not in payload


def test_runtime_monitor_malformed_status_returns_empty_envelope(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text("{not-json", encoding="utf-8")
    app = create_app(status_path=status_path)

    response = TestClient(app, raise_server_exceptions=False).get("/api/runtime/monitor")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "INVALID"
    assert "JSON" in payload["error"]
    assert payload["price_rows"] == []
    assert payload["orderbooks"] == []
    assert payload["health_flags"] == ["runtime_status_invalid"]
    assert "prices" not in payload


def test_runtime_monitor_missing_status_and_uninitialized_duckdb_returns_empty_envelope(
    tmp_path: Path,
) -> None:
    status_path = tmp_path / "missing-status.json"
    duckdb_path = tmp_path / "empty.duckdb"
    duckdb.connect(str(duckdb_path)).close()
    app = create_app(status_path=status_path, duckdb_path=duckdb_path)

    response = TestClient(app, raise_server_exceptions=False).get("/api/runtime/monitor")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "INVALID"
    assert "DuckDB" in payload["error"]
    assert payload["price_rows"] == []
    assert payload["orderbooks"] == []
    assert payload["health_flags"] == ["runtime_status_invalid"]
    assert "prices" not in payload


def test_runtime_monitor_missing_status_and_missing_duckdb_returns_empty_envelope(
    tmp_path: Path,
) -> None:
    status_path = tmp_path / "missing-status.json"
    duckdb_path = tmp_path / "missing.duckdb"
    app = create_app(status_path=status_path, duckdb_path=duckdb_path)

    response = TestClient(app, raise_server_exceptions=False).get("/api/runtime/monitor")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "MISSING"
    assert "missing" in payload["error"]
    assert "runtime" in payload["error"]
    assert payload["price_rows"] == []
    assert payload["orderbooks"] == []
    assert "prices" not in payload


def test_runtime_normalized_health_missing_file_does_not_crash(tmp_path: Path) -> None:
    app = create_app(normalized_health_path=tmp_path / "missing.json")

    response = TestClient(app).get("/api/runtime/normalized-health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "MISSING"


def test_runtime_normalized_health_malformed_json_returns_controlled_state(
    tmp_path: Path,
) -> None:
    normalized_health_path = tmp_path / "normalized_health.json"
    normalized_health_path.write_text("{not-json", encoding="utf-8")
    app = create_app(normalized_health_path=normalized_health_path)

    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/runtime/normalized-health"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "INVALID"
    assert "JSON" in payload["error"]
    assert payload["tables"] == []


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


def test_runtime_storage_reports_data_dir_file_without_crashing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.write_bytes(b"x" * 128)
    app = create_app(data_dir=data_dir)

    response = TestClient(app, raise_server_exceptions=False).get("/api/runtime/storage")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "data_dir": str(data_dir),
        "bytes": 128,
        "estimated": False,
        "children": [
            {"name": data_dir.name, "bytes": 128, "estimated": False, "type": "file"}
        ],
    }


def test_runtime_storage_uses_shallow_directory_accounting(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    raw_dir = data_dir / "raw"
    nested_dir = raw_dir / "nested"
    nested_dir.mkdir(parents=True)
    (raw_dir / "sample.parquet").write_bytes(b"x" * 128)
    (nested_dir / "deep.parquet").write_bytes(b"x" * 4096)
    app = create_app(data_dir=data_dir)

    response = TestClient(app).get("/api/runtime/storage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["bytes"] == 128
    assert payload["estimated"] is True
    assert payload["children"] == [
        {"name": "raw", "bytes": 128, "estimated": True, "type": "directory"}
    ]


def test_runtime_containers_disabled_by_default() -> None:
    app = create_app()

    response = TestClient(app).get("/api/runtime/containers")

    assert response.status_code == 403
    assert response.json()["detail"] == "container status disabled"


def test_runtime_containers_enabled_missing_docker_returns_controlled_state(
    monkeypatch,
) -> None:
    def missing_docker(*args, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(
        "polymarket_engine.runtime_api.subprocess.run",
        missing_docker,
    )
    app = create_app(enable_container_status=True)

    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/runtime/containers"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert "FileNotFoundError" in payload["error"]


def test_runtime_containers_enabled_timeout_returns_controlled_state(monkeypatch) -> None:
    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["docker", "compose", "ps"], timeout=5)

    monkeypatch.setattr(
        "polymarket_engine.runtime_api.subprocess.run",
        timed_out,
    )
    app = create_app(enable_container_status=True)

    response = TestClient(app, raise_server_exceptions=False).get(
        "/api/runtime/containers"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert "TimeoutExpired" in payload["error"]
