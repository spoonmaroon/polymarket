from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timezone
from pathlib import Path
from typing import Any, NoReturn
import json
import os
import subprocess

import duckdb
from fastapi import FastAPI
import pytest
from fastapi.testclient import TestClient

from polymarket_engine.app import create_app
from polymarket_engine.app import create_app_from_env
from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import DataQualityFlag
from polymarket_engine.domain.market_state import DecisionState
from polymarket_engine.probability.generator_fragments import GeneratorFragment
from polymarket_engine.probability.generator_fragments import write_probability_fragments
from polymarket_engine.probability.hot_inputs import write_hot_probability_inputs
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput
from polymarket_engine import runtime_api as runtime_api_module
from polymarket_engine.runtime_api import _probability_events_payload
from polymarket_engine.runtime_api import build_runtime_router
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


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


def test_create_app_from_env_uses_runtime_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_path = tmp_path / "live" / "status.json"
    status_path.parent.mkdir()
    _write_status(status_path)
    duckdb_path = tmp_path / "db" / "polymarket.duckdb"
    normalized_health_path = tmp_path / "live" / "normalized_health.json"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("POLYMARKET_STATUS_PATH", str(status_path))
    monkeypatch.setenv("POLYMARKET_DUCKDB_PATH", str(duckdb_path))
    monkeypatch.setenv("POLYMARKET_NORMALIZED_HEALTH_PATH", str(normalized_health_path))
    monkeypatch.setenv("POLYMARKET_DATA_DIR", str(data_dir))

    response = TestClient(create_app_from_env()).get("/api/runtime/status")

    assert response.status_code == 200
    assert response.json()["status_path"] == str(status_path)


def test_create_app_from_env_uses_probability_inputs_path_for_runtime_probabilities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_path = tmp_path / "live" / "status.json"
    status_path.parent.mkdir()
    _write_status(status_path)
    probability_inputs_path = tmp_path / "live" / "probability_inputs.json"
    probability_fragments_path = tmp_path / "live" / "probability_fragments.json"
    state = _decision_state()
    write_hot_probability_inputs(
        out_path=probability_inputs_path,
        states=(state,),
        generated_at=datetime.now(UTC),
    )
    write_probability_fragments(
        out_path=probability_fragments_path,
        fragments=(
            GeneratorFragment(
                fragment_id="btc-prior-one",
                asset="BTC",
                asof_ts=state.asof_ts,
                prices=(70_000.0, 70_050.0, 70_100.0),
                horizon_seconds=300,
                z_path_bucket="near",
                quality_bucket="OK",
            ),
            GeneratorFragment(
                fragment_id="btc-prior-two",
                asset="BTC",
                asof_ts=state.asof_ts,
                prices=(70_000.0, 70_020.0, 70_080.0),
                horizon_seconds=300,
                z_path_bucket="near",
                quality_bucket="OK",
            ),
        ),
        generated_at=datetime.now(UTC),
    )
    monkeypatch.setenv("POLYMARKET_STATUS_PATH", str(status_path))
    monkeypatch.setenv("POLYMARKET_DUCKDB_PATH", str(tmp_path / "missing.duckdb"))
    monkeypatch.setenv("POLYMARKET_ENABLE_RUNTIME_PROBABILITIES", "1")
    monkeypatch.setenv("POLYMARKET_PROBABILITY_INPUTS_PATH", str(probability_inputs_path))
    monkeypatch.setenv(
        "POLYMARKET_PROBABILITY_FRAGMENTS_PATH",
        str(probability_fragments_path),
    )

    response = TestClient(create_app_from_env()).get("/api/runtime/probabilities?limit=4")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["state"] == "OK"
    assert payload["errors"] == []
    assert payload["rows"][0]["contract"] == "BTC 5m UP"
    assert payload["rows"][0]["prior_fragment_count"] == 2
    assert payload["rows"][0]["prior_fragment_reason"] == "exact"


def test_runtime_router_defaults_to_live_probability_inputs_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    probability_inputs_path = tmp_path / "data" / "live" / "probability_inputs.json"
    write_hot_probability_inputs(
        out_path=probability_inputs_path,
        states=(_decision_state(),),
        generated_at=datetime.now(UTC),
    )
    app = FastAPI()
    app.include_router(
        build_runtime_router(
            status_path=tmp_path / "missing-status.json",
            duckdb_path=tmp_path / "missing.duckdb",
            enable_runtime_probabilities=True,
            allow_probability_compute_fallback=True,
        )
    )

    response = TestClient(app).get("/api/runtime/probabilities?limit=4")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["state"] == "OK"
    assert payload["rows"][0]["contract"] == "BTC 5m UP"


def test_create_app_from_env_uses_status_sibling_target_cache_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_path = tmp_path / "live" / "status.json"
    status_path.parent.mkdir()
    start_ts = datetime(2026, 6, 4, 20, 0, tzinfo=timezone.utc)
    status_path.write_text(
        json.dumps(
            {
                "schema_version": "rust-live-probe-state-manager-v1",
                "mode": "state-manager",
                "generated_at": "2026-06-04T20:02:00Z",
                "current": [
                    {
                        "window": {
                            "asset": "BTC",
                            "interval": "5m",
                            "start_ts": start_ts.isoformat(),
                            "end_ts": (start_ts.replace(minute=start_ts.minute + 5)).isoformat(),
                        },
                        "up": {"asset": "BTC", "side": "Up", "token_id": "btc-up"},
                        "down": {"asset": "BTC", "side": "Down", "token_id": "btc-down"},
                    }
                ],
                "next": [],
                "next_next": [],
                "chainlink_prices": [],
                "orderbooks": [],
                "freshness": [],
                "health_flags": [],
                "websocket_status": [],
                "latency_marks": [],
            }
        ),
        encoding="utf-8",
    )
    status_path.with_name("targets.json").write_text(
        json.dumps(
            {
                "schema_version": "polymarket-target-cache-v1",
                "generated_at": "2026-06-04T20:02:00Z",
                "rows": [
                    {
                        "market_slug": "btc-updown-5m-1780603200",
                        "threshold_price": "63500.12",
                        "threshold_event_ts": "2026-06-04T20:00:00Z",
                        "threshold_observed_ts": "2026-06-04T20:00:03Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("POLYMARKET_STATUS_PATH", str(status_path))
    monkeypatch.delenv("POLYMARKET_TARGET_STATUS_PATH", raising=False)

    response = TestClient(create_app_from_env()).get("/api/runtime/live?limit=8")

    assert response.status_code == 200
    rows = response.json()["monitor"]["orderbooks"]
    assert [row["threshold_price"] for row in rows] == ["63500.12", "63500.12"]


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


def test_runtime_live_combines_status_gates_monitor_and_latency(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    _write_status(status_path)
    app = create_app(status_path=status_path)

    response = TestClient(app).get("/api/runtime/live?limit=8")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"]["counts"]["orderbooks"] == 1
    assert payload["gates"]["ok"] is True
    assert payload["monitor"]["orderbooks"][0]["contract_id"] == "btc-5m-up"
    assert payload["latency"]["status_age_ms"] >= 0
    assert payload["latency"]["api_build_ms"] >= 0
    assert payload["latency"]["server_sent_at"].endswith("+00:00")


def test_runtime_api_exposes_recovery_status(tmp_path: Path) -> None:
    recovery_path = tmp_path / "live" / "recovery_status.json"
    recovery_path.parent.mkdir(parents=True)
    recovery_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-recovery-runtime-v1",
                "generated_at": "2026-06-11T12:00:00+00:00",
                "runtime_phase": "WARMING",
                "ready": False,
                "reasons": ["warmup_active"],
                "boot_id": "boot-1",
            }
        ),
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(build_runtime_router(data_dir=tmp_path, recovery_status_path=recovery_path))
    client = TestClient(app)

    response = client.get("/api/runtime/recovery")

    assert response.status_code == 200
    assert response.json()["runtime_phase"] == "WARMING"
    assert response.json()["reasons"] == ["warmup_active"]


def test_runtime_live_includes_compact_recovery_and_offload(tmp_path: Path) -> None:
    live_dir = tmp_path / "live"
    live_dir.mkdir(parents=True)
    recovery_path = live_dir / "recovery_status.json"
    offload_path = live_dir / "offload_status.json"
    recovery_path.write_text(
        json.dumps({"runtime_phase": "READY", "ready": True, "reasons": [], "boot_id": "boot-1"}),
        encoding="utf-8",
    )
    offload_path.write_text(
        json.dumps(
            {"offload_allowed": True, "reason_codes": [], "recommended_worker_mode": "gpu_mc"}
        ),
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(
        build_runtime_router(
            data_dir=tmp_path,
            recovery_status_path=recovery_path,
            offload_status_path=offload_path,
        )
    )
    client = TestClient(app)

    payload = client.get("/api/runtime/live").json()

    assert payload["recovery"]["runtime_phase"] == "READY"
    assert payload["offload"]["offload_allowed"] is True


def test_runtime_api_optional_status_missing_fallbacks(tmp_path: Path) -> None:
    status_path = tmp_path / "live" / "status.json"
    status_path.parent.mkdir(parents=True)
    _write_status(status_path)
    recovery_path = status_path.with_name("missing_recovery_status.json")
    offload_path = status_path.with_name("missing_offload_status.json")
    app = FastAPI()
    app.include_router(
        build_runtime_router(
            status_path=status_path,
            data_dir=tmp_path,
            recovery_status_path=recovery_path,
            offload_status_path=offload_path,
        )
    )
    client = TestClient(app)

    recovery = client.get("/api/runtime/recovery")
    offload = client.get("/api/runtime/offload")
    live = client.get("/api/runtime/live")

    assert recovery.status_code == 200
    assert offload.status_code == 200
    assert live.status_code == 200
    recovery_payload = recovery.json()
    offload_payload = offload.json()
    live_payload = live.json()
    assert recovery_payload["state"] == "MISSING"
    assert recovery_payload["runtime_phase"] == "UNKNOWN"
    assert recovery_payload["ready"] is False
    assert recovery_payload["reasons"] == ["recovery_status_missing"]
    assert offload_payload["state"] == "MISSING"
    assert offload_payload["offload_allowed"] is False
    assert offload_payload["reason_codes"] == ["offload_status_missing"]
    assert offload_payload["recommended_worker_mode"] == "disabled"
    assert live_payload["recovery"]["state"] == "MISSING"
    assert live_payload["recovery"]["runtime_phase"] == "UNKNOWN"
    assert live_payload["recovery"]["reasons"] == ["recovery_status_missing"]
    assert live_payload["offload"]["state"] == "MISSING"
    assert live_payload["offload"]["offload_allowed"] is False
    assert live_payload["offload"]["reason_codes"] == ["offload_status_missing"]


@pytest.mark.parametrize("invalid_json", ["{not-json", "[]", '"not-an-object"'])
def test_runtime_api_optional_status_invalid_fallbacks(
    tmp_path: Path,
    invalid_json: str,
) -> None:
    status_path = tmp_path / "live" / "status.json"
    status_path.parent.mkdir(parents=True)
    _write_status(status_path)
    recovery_path = status_path.with_name("recovery_status.json")
    offload_path = status_path.with_name("offload_status.json")
    recovery_path.write_text(invalid_json, encoding="utf-8")
    offload_path.write_text(invalid_json, encoding="utf-8")
    app = FastAPI()
    app.include_router(
        build_runtime_router(
            status_path=status_path,
            data_dir=tmp_path,
            recovery_status_path=recovery_path,
            offload_status_path=offload_path,
        )
    )
    client = TestClient(app)

    recovery = client.get("/api/runtime/recovery")
    offload = client.get("/api/runtime/offload")
    live = client.get("/api/runtime/live")

    assert recovery.status_code == 200
    assert offload.status_code == 200
    assert live.status_code == 200
    recovery_payload = recovery.json()
    offload_payload = offload.json()
    live_payload = live.json()
    assert recovery_payload["state"] == "INVALID"
    assert recovery_payload["error"]
    assert recovery_payload["runtime_phase"] == "UNKNOWN"
    assert recovery_payload["ready"] is False
    assert recovery_payload["reasons"] == ["recovery_status_missing"]
    assert offload_payload["state"] == "INVALID"
    assert offload_payload["error"]
    assert offload_payload["offload_allowed"] is False
    assert offload_payload["reason_codes"] == ["offload_status_missing"]
    assert offload_payload["recommended_worker_mode"] == "disabled"
    assert live_payload["recovery"]["state"] == "INVALID"
    assert live_payload["recovery"]["error"]
    assert live_payload["recovery"]["runtime_phase"] == "UNKNOWN"
    assert live_payload["recovery"]["reasons"] == ["recovery_status_missing"]
    assert live_payload["offload"]["state"] == "INVALID"
    assert live_payload["offload"]["error"]
    assert live_payload["offload"]["offload_allowed"] is False
    assert live_payload["offload"]["reason_codes"] == ["offload_status_missing"]


def test_runtime_bug_reports_missing_dir_returns_controlled_state(tmp_path: Path) -> None:
    missing_dir = tmp_path / "missing-bug-reports"
    app = FastAPI()
    app.include_router(build_runtime_router(bug_report_dir=missing_dir))

    response = TestClient(app).get("/api/runtime/bug-reports")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "MISSING"
    assert payload["path"] == str(missing_dir)
    assert payload["reports"] == []
    assert payload["errors"] == [f"{missing_dir} missing"]


def test_runtime_bug_reports_lists_newest_json_reports(tmp_path: Path) -> None:
    bug_report_dir = tmp_path / "bug-reports"
    bug_report_dir.mkdir()
    older = bug_report_dir / "older.json"
    newer = bug_report_dir / "newer.json"
    malformed = bug_report_dir / "malformed.json"
    older.write_text(json.dumps({"bug_id": "bug-old", "severity": "WARN"}), encoding="utf-8")
    newer.write_text(json.dumps({"bug_id": "bug-new", "severity": "CRITICAL"}), encoding="utf-8")
    malformed.write_text("{not-json", encoding="utf-8")
    os.utime(older, (100.0, 100.0))
    os.utime(newer, (300.0, 300.0))
    os.utime(malformed, (200.0, 200.0))
    app = FastAPI()
    app.include_router(build_runtime_router(bug_report_dir=bug_report_dir))

    response = TestClient(app).get("/api/runtime/bug-reports?limit=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "PARTIAL"
    assert payload["path"] == str(bug_report_dir)
    assert [report["bug_id"] for report in payload["reports"]] == ["bug-new", "bug-old"]
    assert payload["reports"][0]["source_path"] == str(newer)
    assert len(payload["errors"]) == 1
    assert str(malformed) in payload["errors"][0]


def test_runtime_bug_reports_report_non_object_json_without_crashing(
    tmp_path: Path,
) -> None:
    bug_report_dir = tmp_path / "bug-reports"
    bug_report_dir.mkdir()
    invalid = bug_report_dir / "array.json"
    invalid.write_text("[]", encoding="utf-8")
    app = FastAPI()
    app.include_router(build_runtime_router(bug_report_dir=bug_report_dir))

    response = TestClient(app).get("/api/runtime/bug-reports")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "INVALID"
    assert payload["reports"] == []
    assert payload["errors"] == [f"{invalid}: JSON root must be an object"]


def test_runtime_bug_reports_clamps_limit_and_allows_zero(tmp_path: Path) -> None:
    bug_report_dir = tmp_path / "bug-reports"
    bug_report_dir.mkdir()
    for index in range(runtime_api_module.BUG_REPORT_MAX_REPORTS + 3):
        path = bug_report_dir / f"report-{index:03}.json"
        path.write_text(json.dumps({"bug_id": f"bug-{index:03}"}), encoding="utf-8")
        os.utime(path, (float(index), float(index)))
    app = FastAPI()
    app.include_router(build_runtime_router(bug_report_dir=bug_report_dir))
    client = TestClient(app)

    zero = client.get("/api/runtime/bug-reports?limit=0").json()
    clamped = client.get("/api/runtime/bug-reports?limit=999999").json()

    assert zero["ok"] is True
    assert zero["reports"] == []
    assert len(clamped["reports"]) == runtime_api_module.BUG_REPORT_MAX_REPORTS
    assert clamped["reports"][0]["bug_id"] == (
        f"bug-{runtime_api_module.BUG_REPORT_MAX_REPORTS + 2:03}"
    )


def test_runtime_bug_reports_skips_oversized_json_files(tmp_path: Path) -> None:
    bug_report_dir = tmp_path / "bug-reports"
    bug_report_dir.mkdir()
    oversized = bug_report_dir / "oversized.json"
    valid = bug_report_dir / "valid.json"
    oversized.write_text(
        "{" + f'"payload":"{"x" * runtime_api_module.BUG_REPORT_MAX_FILE_BYTES}"' + "}",
        encoding="utf-8",
    )
    valid.write_text(json.dumps({"bug_id": "bug-valid"}), encoding="utf-8")
    os.utime(oversized, (300.0, 300.0))
    os.utime(valid, (200.0, 200.0))
    app = FastAPI()
    app.include_router(build_runtime_router(bug_report_dir=bug_report_dir))

    response = TestClient(app).get("/api/runtime/bug-reports?limit=2")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "PARTIAL"
    assert [report["bug_id"] for report in payload["reports"]] == ["bug-valid"]
    assert len(payload["errors"]) == 1
    assert str(oversized) in payload["errors"][0]
    assert "exceeds max size" in payload["errors"][0]


def test_create_app_uses_configured_bug_report_dir(tmp_path: Path) -> None:
    bug_report_dir = tmp_path / "configured-bugs"
    bug_report_dir.mkdir()
    report_path = bug_report_dir / "report.json"
    report_path.write_text(json.dumps({"bug_id": "bug-configured"}), encoding="utf-8")

    response = TestClient(create_app(bug_report_dir=bug_report_dir)).get(
        "/api/runtime/bug-reports"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == str(bug_report_dir)
    assert payload["reports"][0]["bug_id"] == "bug-configured"


def test_create_app_from_env_uses_bug_report_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bug_report_dir = tmp_path / "env-bugs"
    bug_report_dir.mkdir()
    report_path = bug_report_dir / "report.json"
    report_path.write_text(json.dumps({"bug_id": "bug-env"}), encoding="utf-8")
    monkeypatch.setenv("POLYMARKET_BUG_REPORT_DIR", str(bug_report_dir))

    response = TestClient(create_app_from_env()).get("/api/runtime/bug-reports")

    assert response.status_code == 200
    payload = response.json()
    assert payload["path"] == str(bug_report_dir)
    assert payload["reports"][0]["bug_id"] == "bug-env"


def test_runtime_live_includes_volatility_diagnostics(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    _write_status(status_path)
    db_path = tmp_path / "polymarket.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    btc_state = replace(
        _decision_state(),
        state_id="state-btc-up",
        short_realized_vol=0.0001,
        medium_realized_vol=0.0002,
        long_realized_vol=0.0003,
        sigma_tau=0.0012,
        volatility_regime="normal",
    )
    eth_contract = replace(
        btc_state.contract,
        contract_id="eth-market:UP",
        market_id="eth-market",
        condition_id="0xeth",
        slug="eth-updown-5m-1780264500",
        asset="ETH",
        token_id="eth-up-token",
        settlement_symbol="ETH/USD",
    )
    eth_state = replace(
        btc_state,
        state_id="state-eth-up",
        contract=eth_contract,
        threshold=1800.0,
        settlement_price=1802.0,
        short_realized_vol=0.0004,
        medium_realized_vol=0.0005,
        long_realized_vol=0.0006,
        sigma_tau=0.0024,
        volatility_regime="expanding",
    )
    store.upsert_contract_spec(btc_state.contract)
    store.upsert_contract_spec(eth_state.contract)
    store.upsert_asof_state_inputs((btc_state, eth_state))
    app = create_app(status_path=status_path, duckdb_path=db_path)

    response = TestClient(app).get("/api/runtime/live?limit=8")

    assert response.status_code == 200
    volatility = response.json()["volatility"]
    assert volatility["state"] == "OK"
    assert volatility["errors"] == []
    assert len(volatility["rows"]) == 2
    btc_row = volatility["rows"][0]
    eth_row = volatility["rows"][1]
    assert btc_row["asset"] == "BTC"
    assert btc_row["sigma_tau"] == pytest.approx(0.0012)
    assert btc_row["short_realized_vol"] == pytest.approx(0.0001)
    assert btc_row["medium_realized_vol"] == pytest.approx(0.0002)
    assert btc_row["long_realized_vol"] == pytest.approx(0.0003)
    assert btc_row["volatility_regime"] == "normal"
    assert btc_row["age_ms"] >= 0
    assert btc_row["flags"] == ["OK"]
    assert eth_row["asset"] == "ETH"
    assert eth_row["sigma_tau"] == pytest.approx(0.0024)
    assert eth_row["volatility_regime"] == "expanding"


def test_runtime_live_reads_file_backed_volatility_when_duckdb_is_locked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_path = tmp_path / "live" / "status.json"
    status_path.parent.mkdir()
    _write_status(status_path)
    volatility_status_path = status_path.with_name("volatility.json")
    volatility_status_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-volatility-runtime-v1",
                "ok": True,
                "state": "OK",
                "generated_at": datetime.now(UTC).isoformat(),
                "source_key": "polymarket_rtds_chainlink",
                "lookback_limit": 180,
                "rows": [
                    {
                        "asset": "BTC",
                        "asof_ts": datetime.now(UTC).isoformat(),
                        "short_realized_vol": 0.0001,
                        "medium_realized_vol": 0.0002,
                        "long_realized_vol": 0.0003,
                        "sigma_tau": 0.0012,
                        "volatility_regime": "normal",
                        "flags": ["OK"],
                    }
                ],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    duckdb_path = tmp_path / "polymarket.duckdb"
    duckdb_path.touch()

    def locked_connect(*_: object, **__: object) -> NoReturn:
        raise duckdb.IOException("Could not set lock on file")

    monkeypatch.setattr(duckdb, "connect", locked_connect)
    app = create_app(
        status_path=status_path,
        duckdb_path=duckdb_path,
        volatility_status_path=volatility_status_path,
    )

    response = TestClient(app).get("/api/runtime/live?limit=8")

    assert response.status_code == 200
    volatility = response.json()["volatility"]
    assert volatility["state"] == "OK"
    assert volatility["errors"] == []
    assert volatility["rows"][0]["asset"] == "BTC"
    assert volatility["rows"][0]["sigma_tau"] == pytest.approx(0.0012)


def test_runtime_live_reads_status_once_for_status_backed_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status_path = tmp_path / "status.json"
    _write_status(status_path)
    read_count = 0
    real_read_text = Path.read_text

    def counting_read_text(path: Path, *args: Any, **kwargs: Any) -> str:
        nonlocal read_count
        if path == status_path:
            read_count += 1
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    app = create_app(status_path=status_path)

    response = TestClient(app).get("/api/runtime/live?limit=8")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert read_count == 1


def test_runtime_live_stream_emits_sse_payload(tmp_path: Path) -> None:
    status_path = tmp_path / "live" / "status.json"
    status_path.parent.mkdir(parents=True)
    _write_status(status_path)
    recovery_path = status_path.with_name("recovery_status.json")
    offload_path = status_path.with_name("offload_status.json")
    recovery_path.write_text(
        json.dumps({"runtime_phase": "READY", "ready": True, "reasons": [], "boot_id": "boot-1"}),
        encoding="utf-8",
    )
    offload_path.write_text(
        json.dumps(
            {"offload_allowed": True, "reason_codes": [], "recommended_worker_mode": "gpu_mc"}
        ),
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(
        build_runtime_router(
            status_path=status_path,
            data_dir=tmp_path,
            recovery_status_path=recovery_path,
            offload_status_path=offload_path,
        )
    )

    with TestClient(app).stream(
        "GET",
        "/api/runtime/live/stream?limit=8&interval_ms=1&max_events=1",
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: live" in body
    assert "data: " in body
    data_line = next(line for line in body.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["status"]["counts"]["orderbooks"] == 1
    assert payload["monitor"]["orderbooks"][0]["contract_id"] == "btc-5m-up"
    assert payload["recovery"]["runtime_phase"] == "READY"
    assert payload["offload"]["offload_allowed"] is True


def test_runtime_outcomes_returns_market_level_history(tmp_path: Path) -> None:
    store = _seeded_store_with_outcome(tmp_path, official_winner="UP")
    app = create_app(status_path=tmp_path / "missing-status.json", duckdb_path=store.db_path)

    response = TestClient(app).get("/api/runtime/outcomes?limit=4")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["rows"][0]["market"] == "BTC 5m"
    assert payload["rows"][0]["computed_winner"] is None
    assert payload["rows"][0]["official_winner"] == "UP"
    assert payload["rows"][0]["winning_token_id"] == "up-token"
    assert payload["rows"][0]["official_resolution_status"] == "resolved"


def test_runtime_outcomes_includes_threshold_fields(tmp_path: Path) -> None:
    store = _seeded_store_with_outcome(tmp_path, official_winner="UP")
    app = create_app(status_path=tmp_path / "missing-status.json", duckdb_path=store.db_path)

    response = TestClient(app).get("/api/runtime/outcomes?limit=4")

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["threshold_price"] == 70_000.0
    assert row["threshold_event_ts"] == "2026-06-03T20:00:00+00:00"
    assert row["threshold_observed_ts"] == "2026-06-03T20:00:00+00:00"
    assert row["end_price"] == 70_100.0
    assert row["end_event_ts"] == "2026-06-03T20:05:00+00:00"
    assert row["end_observed_ts"] == "2026-06-03T20:05:00+00:00"


def test_runtime_outcomes_reads_live_outcome_status_file(tmp_path: Path) -> None:
    outcome_status_path = tmp_path / "live" / "outcomes.json"
    outcome_status_path.parent.mkdir()
    outcome_status_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-outcome-runtime-v1",
                "ok": True,
                "state": "OK",
                "generated_at": datetime.now(UTC).isoformat(),
                "rows": [
                    _runtime_outcome_row("BTC", "UP"),
                    _runtime_outcome_row("ETH", "DOWN"),
                ],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=tmp_path / "locked-or-missing.duckdb",
        outcome_status_path=outcome_status_path,
    )

    response = TestClient(app).get("/api/runtime/outcomes?limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["rows"] == [_runtime_outcome_row("BTC", "UP")]


def test_runtime_outcomes_limits_large_status_file_rows(tmp_path: Path) -> None:
    outcome_status_path = tmp_path / "live" / "outcomes.json"
    outcome_status_path.parent.mkdir()
    outcome_status_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-outcome-runtime-v1",
                "ok": True,
                "state": "OK",
                "generated_at": datetime.now(UTC).isoformat(),
                "rows": [
                    _runtime_outcome_row("BTC", "UP"),
                    _runtime_outcome_row("ETH", "DOWN"),
                ],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=tmp_path / "locked-or-missing.duckdb",
        outcome_status_path=outcome_status_path,
    )

    response = TestClient(app).get("/api/runtime/outcomes?limit=1")

    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["asset"] == "BTC"


def test_runtime_outcomes_rejects_stale_live_outcome_status_file(tmp_path: Path) -> None:
    outcome_status_path = tmp_path / "live" / "outcomes.json"
    outcome_status_path.parent.mkdir()
    outcome_status_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-outcome-runtime-v1",
                "ok": True,
                "state": "OK",
                "generated_at": datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                "rows": [_runtime_outcome_row("BTC", "UP")],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=tmp_path / "locked-or-missing.duckdb",
        outcome_status_path=outcome_status_path,
    )

    response = TestClient(app).get("/api/runtime/outcomes?limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "STALE"
    assert "stale" in payload["error"]
    assert payload["rows"] == [_runtime_outcome_row("BTC", "UP")]


def test_runtime_outcomes_rejects_malformed_live_outcome_status_rows(
    tmp_path: Path,
) -> None:
    outcome_status_path = tmp_path / "live" / "outcomes.json"
    outcome_status_path.parent.mkdir()
    outcome_status_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-outcome-runtime-v1",
                "ok": True,
                "state": "OK",
                "generated_at": datetime.now(UTC).isoformat(),
                "rows": [{"market": "BTC 5m", "official_winner": "UP"}],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=tmp_path / "locked-or-missing.duckdb",
        outcome_status_path=outcome_status_path,
    )

    response = TestClient(app).get("/api/runtime/outcomes?limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "INVALID"
    assert "missing row field" in payload["error"]
    assert payload["rows"] == []


def test_runtime_outcomes_accepts_legacy_rows_without_winning_token_id(
    tmp_path: Path,
) -> None:
    outcome_status_path = tmp_path / "live" / "outcomes.json"
    outcome_status_path.parent.mkdir()
    legacy_row = _runtime_outcome_row("BTC", "UP")
    legacy_row.pop("winning_token_id")
    outcome_status_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-outcome-runtime-v1",
                "ok": True,
                "state": "OK",
                "generated_at": datetime.now(UTC).isoformat(),
                "rows": [legacy_row],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=tmp_path / "locked-or-missing.duckdb",
        outcome_status_path=outcome_status_path,
    )

    response = TestClient(app).get("/api/runtime/outcomes?limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["rows"][0]["winning_token_id"] is None


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


def test_runtime_probabilities_disabled_by_default_returns_empty_envelope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = _decision_state()
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)

    def fail_compute(*_: object, **__: object) -> NoReturn:
        raise AssertionError("runtime probabilities should be disabled by default")

    monkeypatch.setattr(
        "polymarket_engine.probability.runtime._compute_and_persist_rows",
        fail_compute,
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=db_path,
    )

    response = TestClient(app).get("/api/runtime/probabilities?limit=4")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["state"] == "DISABLED"
    assert payload["rows"] == []
    assert payload["errors"] == []


def test_runtime_probabilities_skips_compute_when_fallback_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = _decision_state()
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)

    def fail_compute(*_: object, **__: object) -> NoReturn:
        raise AssertionError("runtime probability display should not compute CPU fallback")

    monkeypatch.setattr(
        "polymarket_engine.probability.runtime._compute_and_persist_rows",
        fail_compute,
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=db_path,
        enable_runtime_probabilities=True,
    )

    response = TestClient(app).get("/api/runtime/probabilities?limit=4")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "COMPUTE_DISABLED"
    assert payload["rows"] == []
    assert payload["errors"] == [
        "probability status missing and runtime probability compute fallback disabled"
    ]


def test_runtime_probabilities_runs_cached_read_only_mc_when_compute_fallback_allowed(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = _decision_state()
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=db_path,
        enable_runtime_probabilities=True,
        allow_runtime_probability_compute=True,
    )
    client = TestClient(app)

    first = client.get("/api/runtime/probabilities?limit=4")
    second = client.get("/api/runtime/probabilities?limit=4")

    assert first.status_code == 200
    assert second.status_code == 200
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["ok"] is True
    assert first_payload["cached"] is False
    assert second_payload["cached"] is True
    assert len(first_payload["rows"]) == 1
    row = first_payload["rows"][0]
    assert row["contract"] == "BTC 5m UP"
    assert 0.0 <= row["p_finish"] <= 1.0
    assert 0.0 <= row["p_no_touch"] <= 1.0
    assert row["sigma_tau"] == pytest.approx(0.01)
    assert row["flags"] == ["OK"]
    with duckdb.connect(str(db_path)) as conn:
        assert conn.execute("select count(*) from features.probability_outputs").fetchone() == (1,)


def test_runtime_probabilities_returns_empty_envelope_for_missing_duckdb(
    tmp_path: Path,
) -> None:
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=tmp_path / "missing.duckdb",
        enable_runtime_probabilities=True,
    )

    response = TestClient(app).get("/api/runtime/probabilities")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["state"] == "MISSING"
    assert payload["rows"] == []


def test_runtime_probabilities_skips_quality_blocked_asof_states(tmp_path: Path) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = _decision_state(data_quality_flags=("stale_source",))
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=db_path,
        enable_runtime_probabilities=True,
        allow_runtime_probability_compute=True,
    )

    response = TestClient(app).get("/api/runtime/probabilities")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["state"] == "OK"
    assert payload["rows"] == []
    assert payload["skipped"] == 1


def test_runtime_probabilities_reads_live_probability_status_file(tmp_path: Path) -> None:
    probability_status_path = tmp_path / "live" / "probabilities.json"
    probability_status_path.parent.mkdir()
    probability_status_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-probability-runtime-v1",
                "ok": True,
                "state": "OK",
                "generated_at": datetime.now(UTC).isoformat(),
                "cached": False,
                "model_version": "fixture-mc-v1",
                "rows": [
                    {"contract": "BTC 5m UP", "output_id": "btc-up"},
                    {"contract": "BTC 5m DOWN", "output_id": "btc-down"},
                ],
                "skipped": 0,
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=tmp_path / "missing.duckdb",
        probability_status_path=probability_status_path,
        enable_runtime_probabilities=True,
    )

    response = TestClient(app).get("/api/runtime/probabilities?limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["rows"] == [{"contract": "BTC 5m UP", "output_id": "btc-up"}]


def test_runtime_probabilities_uses_last_good_rows_when_status_rows_empty(
    tmp_path: Path,
) -> None:
    probability_status_path = tmp_path / "live" / "probabilities.json"
    probability_status_path.parent.mkdir()
    probability_status_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-probability-runtime-v1",
                "ok": False,
                "state": "PARTIAL",
                "generated_at": datetime.now(UTC).isoformat(),
                "cached": False,
                "model_version": None,
                "rows": [],
                "last_good_rows": [
                    {"contract": "BTC 5m UP", "output_id": "retained-up"},
                    {"contract": "BTC 5m DOWN", "output_id": "retained-down"},
                ],
                "skipped": 4,
                "errors": ["probability input unavailable"],
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=tmp_path / "missing.duckdb",
        probability_status_path=probability_status_path,
        enable_runtime_probabilities=True,
    )

    response = TestClient(app).get("/api/runtime/probabilities?limit=1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "PARTIAL"
    assert payload["rows"] == [{"contract": "BTC 5m UP", "output_id": "retained-up"}]
    assert payload["last_good_rows"] == [
        {"contract": "BTC 5m UP", "output_id": "retained-up"},
        {"contract": "BTC 5m DOWN", "output_id": "retained-down"},
    ]


def test_runtime_probabilities_prefers_non_empty_status_file_over_empty_hot_inputs(
    tmp_path: Path,
) -> None:
    probability_status_path = tmp_path / "live" / "probabilities.json"
    probability_inputs_path = tmp_path / "live" / "probability_inputs.json"
    probability_status_path.parent.mkdir()
    probability_status_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-probability-runtime-v1",
                "ok": True,
                "state": "OK",
                "generated_at": datetime(2026, 6, 1, tzinfo=UTC).isoformat(),
                "cached": False,
                "model_version": "fixture-mc-v1",
                "rows": [{"contract": "BTC 5m UP", "output_id": "gpu-row"}],
                "skipped": 0,
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    probability_inputs_path.write_text(
        json.dumps(
            {
                "schema_version": "polymarket-hot-probability-inputs-v1",
                "generated_at": datetime.now(UTC).isoformat(),
                "inputs": [],
                "skipped": 4,
            }
        ),
        encoding="utf-8",
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=tmp_path / "missing.duckdb",
        probability_status_path=probability_status_path,
        probability_inputs_path=probability_inputs_path,
        enable_runtime_probabilities=True,
    )

    response = TestClient(app).get("/api/runtime/probabilities?limit=4")

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_version"] == "fixture-mc-v1"
    assert payload["rows"] == [{"contract": "BTC 5m UP", "output_id": "gpu-row"}]


def test_runtime_probabilities_uses_hot_inputs_when_rows_exist_and_no_status_file(
    tmp_path: Path,
) -> None:
    probability_inputs_path = tmp_path / "live" / "probability_inputs.json"
    write_hot_probability_inputs(
        out_path=probability_inputs_path,
        states=(_decision_state(),),
        generated_at=datetime.now(UTC),
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=tmp_path / "missing.duckdb",
        probability_status_path=tmp_path / "live" / "probabilities.json",
        probability_inputs_path=probability_inputs_path,
        enable_runtime_probabilities=True,
    )

    response = TestClient(app).get("/api/runtime/probabilities?limit=4")

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "hot_inputs"
    row = payload["rows"][0]
    assert row["contract"] == "BTC 5m UP"
    assert row["model_version"] == "ensemble-v1"
    assert row["backend"] == "ensemble"
    assert row["generator_version"] == "four-generator-ensemble-v1"
    assert row["generator_summary"]
    assert row["effective_weights"]


def test_probability_events_stream_reads_newest_drain_when_jsonl_missing(
    tmp_path: Path,
) -> None:
    probability_status_path = tmp_path / "live" / "probabilities.json"
    probability_status_path.parent.mkdir()
    older_drain = probability_status_path.with_name("probability-events.jsonl.older.drain")
    newer_drain = probability_status_path.with_name("probability-events.jsonl.newer.drain")
    older_drain.write_text(
        json.dumps({"event_id": "old", "state_id": "state-old"}) + "\n",
        encoding="utf-8",
    )
    newer_drain.write_text(
        json.dumps({"event_id": "new", "state_id": "state-new"}) + "\n",
        encoding="utf-8",
    )
    newer_mtime_ns = older_drain.stat().st_mtime_ns + 1_000_000
    os.utime(newer_drain, ns=(newer_mtime_ns, newer_mtime_ns))
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=tmp_path / "missing.duckdb",
        probability_status_path=probability_status_path,
        enable_runtime_probabilities=True,
    )

    response = TestClient(app).get(
        "/api/runtime/probability-events/stream?limit=4&max_events=1"
    )

    assert response.status_code == 200
    body = response.text
    assert "event: probability" in body
    assert '"event_id":"new"' in body
    assert '"event_id":"old"' not in body


def test_probability_events_payload_reuses_unchanged_event_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probability_event_path = tmp_path / "live" / "probability-events.jsonl"
    probability_event_path.parent.mkdir()
    probability_event_path.write_text(
        json.dumps({"event_id": "event-1", "state_id": "state-btc-up"}) + "\n",
        encoding="utf-8",
    )
    read_count = 0
    real_tail_text_lines = runtime_api_module._tail_text_lines

    def counting_tail_text_lines(
        path: Path,
        *,
        max_lines: int,
        block_size: int = 64 * 1024,
    ) -> list[str]:
        nonlocal read_count
        if path == probability_event_path:
            read_count += 1
        return real_tail_text_lines(
            path,
            max_lines=max_lines,
            block_size=block_size,
        )

    monkeypatch.setattr(runtime_api_module, "_tail_text_lines", counting_tail_text_lines)

    first = _probability_events_payload(
        probability_event_path=probability_event_path,
        limit=4,
        after_event_id=None,
    )
    second = _probability_events_payload(
        probability_event_path=probability_event_path,
        limit=4,
        after_event_id=None,
    )

    assert first["events"] == second["events"]
    assert read_count == 1


def test_probability_events_payload_tails_large_event_file_without_full_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probability_event_path = tmp_path / "live" / "probability-events.jsonl"
    probability_event_path.parent.mkdir()
    with probability_event_path.open("w", encoding="utf-8") as handle:
        for index in range(10_000):
            handle.write(
                json.dumps({"event_id": f"event-{index}", "state_id": f"state-{index}"})
                + "\n"
            )

    real_read_text = Path.read_text

    def fail_full_read(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == probability_event_path:
            raise AssertionError("probability event reader must not read the full JSONL file")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_full_read)

    payload = _probability_events_payload(
        probability_event_path=probability_event_path,
        limit=4,
        after_event_id=None,
    )

    assert [event["event_id"] for event in payload["events"]] == [
        "event-9996",
        "event-9997",
        "event-9998",
        "event-9999",
    ]


def test_runtime_probabilities_prefers_persisted_outputs_without_recomputing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    state = _decision_state()
    probability_input = ProbabilityInput.from_decision_state(state)
    output = ProbabilityOutput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        p_finish=0.62,
        p_no_touch=0.58,
        z_path=probability_input.z_path,
        model_version="ensemble-v1",
        seed=123,
        diagnostics={
            "backend": "ensemble",
            "generator_version": "four-generator-ensemble-v1",
            "path_count": 1,
            "steps": 1,
            "effective_weights": {"empirical_conditional": 0.4},
            "generator_summary": {
                "empirical_conditional": {
                    "p_finish": 0.62,
                    "p_no_touch": 0.58,
                    "weight": 0.4,
                    "sparse": False,
                }
            },
        },
    )
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)
    store.insert_probability_output(
        output_id="prob-fixture",
        probability_input=probability_input,
        output=output,
    )

    def fail_compute(*_: object, **__: object) -> NoReturn:
        raise AssertionError("probability API should read persisted rows first")

    monkeypatch.setattr(
        "polymarket_engine.probability.runtime._compute_and_persist_rows",
        fail_compute,
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=db_path,
        enable_runtime_probabilities=True,
    )
    response = TestClient(app).get("/api/runtime/probabilities?limit=4")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["state"] == "OK"
    assert payload["rows"][0]["output_id"] == "prob-fixture"
    assert payload["rows"][0]["p_finish"] == pytest.approx(0.62)


def test_runtime_probabilities_reads_persisted_outputs_without_decision_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    state = _decision_state()
    probability_input = ProbabilityInput.from_decision_state(state)
    output = ProbabilityOutput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        p_finish=0.62,
        p_no_touch=0.58,
        z_path=probability_input.z_path,
        model_version="ensemble-v1",
        seed=123,
        diagnostics={
            "backend": "ensemble",
            "generator_version": "four-generator-ensemble-v1",
            "path_count": 1,
            "steps": 1,
            "effective_weights": {"empirical_conditional": 0.4},
            "generator_summary": {
                "empirical_conditional": {
                    "p_finish": 0.62,
                    "p_no_touch": 0.58,
                    "weight": 0.4,
                    "sparse": False,
                }
            },
        },
    )
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)
    store.insert_probability_output(
        output_id="prob-fixture",
        probability_input=probability_input,
        output=output,
    )
    with duckdb.connect(str(db_path)) as conn:
        conn.execute("drop table features.ensemble_decisions")

    def fail_compute(*_: object, **__: object) -> NoReturn:
        raise AssertionError("probability API should read persisted rows first")

    monkeypatch.setattr(
        "polymarket_engine.probability.runtime._compute_and_persist_rows",
        fail_compute,
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=db_path,
        enable_runtime_probabilities=True,
    )

    response = TestClient(app).get("/api/runtime/probabilities?limit=4")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["state"] == "OK"
    row = payload["rows"][0]
    assert row["output_id"] == "prob-fixture"
    assert row["p_finish"] == pytest.approx(0.62)
    assert row["model_version"] == "ensemble-v1"
    assert row["backend"] == "ensemble"
    assert row["generator_version"] == "four-generator-ensemble-v1"
    assert row["path_count"] == 1
    assert row["steps"] == 1
    assert row["effective_weights"] == {"empirical_conditional": 0.4}
    assert row["generator_summary"]["empirical_conditional"]["weight"] == 0.4
    assert "decision_hint" not in row
    assert "skip_reasons" not in row


def test_runtime_probabilities_include_optional_ensemble_decision_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "polymarket.duckdb"
    state = _decision_state()
    probability_input = ProbabilityInput.from_decision_state(state)
    output = ProbabilityOutput(
        state_id=probability_input.state_id,
        asof_ts=probability_input.asof_ts,
        p_finish=0.62,
        p_no_touch=0.58,
        z_path=probability_input.z_path,
        model_version="fixture-mc-v1",
        seed=123,
        diagnostics={"path_count": 1, "steps": 1},
    )
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    store.upsert_contract_spec(state.contract)
    store.upsert_asof_state_input(state)
    store.insert_probability_output(
        output_id="prob-fixture",
        probability_input=probability_input,
        output=output,
    )
    older_decision_ts = probability_input.asof_ts.replace(minute=2)
    store.insert_ensemble_decisions(
        (
            {
                "decision_id": "decision-old",
                "state_id": probability_input.state_id,
                "contract_id": state.contract.contract_id,
                "asof_ts": older_decision_ts,
                "execution_mode": "paper",
                "decision_hint": "SKIP",
                "p_finish": 0.51,
                "p_no_touch": 0.49,
                "z_path": 0.1,
                "edge_after_costs": 0.01,
                "required_edge": 0.05,
                "skip_reasons_json": '["old"]',
                "edge_components_json": "{}",
                "generator_summary_json": '{"winner":"old"}',
                "execution_summary_json": '{"book":"old"}',
                "supervised_live_json": '{"action":"DISABLED","reason":"old"}',
                "created_at": older_decision_ts,
            },
            {
                "decision_id": "decision-latest",
                "state_id": probability_input.state_id,
                "contract_id": state.contract.contract_id,
                "asof_ts": probability_input.asof_ts,
                "execution_mode": "paper",
                "decision_hint": "PAPER_TRADE",
                "p_finish": 0.62,
                "p_no_touch": 0.58,
                "z_path": probability_input.z_path,
                "edge_after_costs": 0.074,
                "required_edge": 0.05,
                "skip_reasons_json": '["thin_book","latency_guard"]',
                "edge_components_json": '{"base_edge":0.09,"fees":0.016}',
                "generator_summary_json": '{"winner":"bootstrap","count":4}',
                "execution_summary_json": '{"target_size":12.5,"spread":0.03}',
                "supervised_live_json": '{"action":"DISABLED","reason":"read_only"}',
                "created_at": probability_input.asof_ts,
            },
        )
    )

    def fail_compute(*_: object, **__: object) -> NoReturn:
        raise AssertionError("probability API should read persisted rows first")

    monkeypatch.setattr(
        "polymarket_engine.probability.runtime._compute_and_persist_rows",
        fail_compute,
    )
    app = create_app(
        status_path=tmp_path / "missing-status.json",
        duckdb_path=db_path,
        enable_runtime_probabilities=True,
    )

    response = TestClient(app).get("/api/runtime/probabilities?limit=4")

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["output_id"] == "prob-fixture"
    assert row["decision_hint"] == "PAPER_TRADE"
    assert row["edge_after_costs"] == pytest.approx(0.074)
    assert row["required_edge"] == pytest.approx(0.05)
    assert row["skip_reasons"] == ["thin_book", "latency_guard"]
    assert row["generator_summary"] == {"winner": "bootstrap", "count": 4}
    assert row["execution_summary"] == {"target_size": 12.5, "spread": 0.03}
    assert row["supervised_live"] == {"action": "DISABLED", "reason": "read_only"}


def _contract() -> ContractSpec:
    return ContractSpec(
        contract_id="btc-market:UP",
        venue="polymarket",
        market_id="btc-market",
        condition_id="0xbtc",
        slug="btc-updown-5m-1780264500",
        asset="BTC",
        side="UP",
        token_id="btc-up-token",
        threshold_type="start_price",
        threshold_price=None,
        comparison_operator=">=",
        start_ts=datetime(2026, 6, 3, 20, 0, tzinfo=timezone.utc),
        expiry_ts=datetime(2026, 6, 3, 20, 5, tzinfo=timezone.utc),
        settlement_source_name="chainlink_data_streams",
        settlement_source_url="https://data.chain.link/streams/btc-usd",
        settlement_symbol="BTC/USD",
        rule_text="fixture",
        rule_hash="hash",
        parser_version="test",
    )


def _decision_state(
    *,
    data_quality_flags: tuple[DataQualityFlag, ...] = (),
) -> DecisionState:
    contract = _contract()
    asof_ts = datetime(2026, 6, 3, 20, 3, tzinfo=timezone.utc)
    return DecisionState(
        state_id="state-btc-up",
        asof_ts=asof_ts,
        contract=contract,
        threshold=100.0,
        threshold_source_key="polymarket_rtds_chainlink",
        threshold_event_ts=datetime(2026, 6, 3, 20, 0, tzinfo=timezone.utc),
        threshold_observed_ts=datetime(2026, 6, 3, 20, 0, 1, tzinfo=timezone.utc),
        seconds_left=2.0,
        settlement_price=101.0,
        settlement_source_key="polymarket_rtds_chainlink",
        settlement_event_ts=asof_ts,
        settlement_observed_ts=asof_ts,
        proxy_prices={"coinbase_advanced_ws": 101.01},
        source_disagreement_bps=0.1,
        best_bid=0.61,
        best_ask=0.64,
        executable_price=0.64,
        spread=0.03,
        book_event_ts=asof_ts,
        book_observed_ts=asof_ts,
        quote_age_ms=100,
        source_age_ms=100,
        source_observed_lag_ms=0,
        book_age_ms=120,
        book_observed_lag_ms=0,
        realized_returns=(0.001, -0.0005),
        short_realized_vol=0.01,
        medium_realized_vol=0.01,
        long_realized_vol=0.01,
        sigma_tau=0.01,
        volatility_regime="normal",
        data_quality_flags=data_quality_flags,
    )


def _seeded_store_with_outcome(
    tmp_path: Path,
    *,
    official_winner: str,
) -> DuckDbIngestStore:
    from polymarket_engine.storage.duckdb_store import MarketOutcomeRecord

    db_path = tmp_path / "polymarket.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    start_ts = datetime(2026, 6, 3, 20, 0, tzinfo=timezone.utc)
    expiry_ts = datetime(2026, 6, 3, 20, 5, tzinfo=timezone.utc)
    store.upsert_market_outcome_records(
        (
            MarketOutcomeRecord(
                market_id="btc-updown-5m-1780502400",
                condition_id="0xbtc",
                market_slug="btc-updown-5m-1780502400",
                asset="BTC",
                interval="5m",
                start_ts=start_ts,
                expiry_ts=expiry_ts,
                up_token_id="up-token",
                down_token_id="down-token",
                threshold_price=70_000.0,
                threshold_event_ts=start_ts,
                threshold_observed_ts=start_ts,
                end_price=70_100.0,
                end_event_ts=expiry_ts,
                end_observed_ts=expiry_ts,
                computed_winner=None,
                computed_label_source=None,
                computed_at=None,
                official_winner=official_winner,
                winning_token_id="up-token" if official_winner == "UP" else "down-token",
                official_resolution_status="resolved",
                official_label_source="polymarket_clob_market",
                official_resolved_at=expiry_ts,
                rule_hash="hash",
                mismatch=None,
            ),
        )
    )
    return store


def _runtime_outcome_row(asset: str, official_winner: str) -> dict[str, object]:
    return {
        "market": f"{asset} 5m",
        "market_id": f"{asset.lower()}-updown-5m-1780502400",
        "market_slug": f"{asset.lower()}-updown-5m-1780502400",
        "asset": asset,
        "interval": "5m",
        "start_ts": "2026-06-03T20:00:00+00:00",
        "expiry_ts": "2026-06-03T20:05:00+00:00",
        "threshold_price": None,
        "threshold_event_ts": None,
        "threshold_observed_ts": None,
        "end_price": None,
        "end_event_ts": None,
        "end_observed_ts": None,
        "computed_winner": None,
        "official_winner": official_winner,
        "winning_token_id": "up-token" if official_winner == "UP" else "down-token",
        "official_resolution_status": "resolved",
        "mismatch": None,
    }


def test_runtime_containers_enabled_missing_docker_returns_controlled_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_docker(*_args: object, **_kwargs: object) -> NoReturn:
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


def test_runtime_containers_enabled_timeout_returns_controlled_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timed_out(*_args: object, **_kwargs: object) -> NoReturn:
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
