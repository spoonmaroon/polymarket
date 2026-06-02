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


def _orderbook(asset: str, side: str, token_id: str) -> dict[str, object]:
    return {
        "venue": "polymarket",
        "source_key": "polymarket_clob_market_ws",
        "market_slug": f"{asset.lower()}-updown-5m-1780383600",
        "contract_id": f"{asset.lower()}-current",
        "token_id": token_id,
        "asset": asset,
        "side": side,
        "event_ts": "2026-06-02T07:00:01+00:00",
        "observed_ts": "2026-06-02T07:00:01+00:00",
        "bids": [],
        "asks": [],
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
            {
                "source_key": "polymarket_rtds_chainlink",
                "symbol": "BTC/USD",
                "age_ms": 10,
                "stale": False,
            },
            {
                "source_key": "polymarket_rtds_chainlink",
                "symbol": "ETH/USD",
                "age_ms": 10,
                "stale": False,
            },
        ],
        "latency_marks": [
            {"name": "chainlink_observed_age_ms", "elapsed_ms": 10},
            {"name": "chainlink_event_to_observed_ms", "elapsed_ms": 10},
            {"name": "orderbook_observed_age_ms", "elapsed_ms": 10},
            {"name": "orderbook_event_to_observed_ms", "elapsed_ms": 10},
        ],
        "orderbooks": [
            _orderbook("BTC", "UP", "btc-up"),
            _orderbook("BTC", "DOWN", "btc-down"),
            _orderbook("ETH", "UP", "eth-up"),
            _orderbook("ETH", "DOWN", "eth-down"),
        ],
        "subscriptions": [
            {
                "source_key": "polymarket_clob_market_ws",
                "channel": "market",
                "asset": "BTC",
                "token_id": "btc-up",
            },
            {
                "source_key": "polymarket_clob_market_ws",
                "channel": "market",
                "asset": "BTC",
                "token_id": "btc-down",
            },
            {
                "source_key": "polymarket_clob_market_ws",
                "channel": "market",
                "asset": "ETH",
                "token_id": "eth-up",
            },
            {
                "source_key": "polymarket_clob_market_ws",
                "channel": "market",
                "asset": "ETH",
                "token_id": "eth-down",
            },
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


def test_verifier_accepts_missing_next_next_for_two_window_experiment() -> None:
    script = _load_script()
    payload = _report()
    payload["next_next"] = []

    assert script.validate(payload, expected_prewarm_windows=2) == []


def test_verifier_accepts_next_next_assets(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    report_path = tmp_path / "state-manager.json"
    report_path.write_text(json.dumps(_report()), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["verify_state_manager_report.py", str(report_path)])

    assert script.main() == 0
    assert "next_next=2" in capsys.readouterr().out


def test_verifier_rejects_incomplete_hot_decision_telemetry() -> None:
    script = _load_script()
    payload = _report()
    payload["hot_decision_telemetry"] = {
        "states_built": 1,
    }

    with pytest.raises(SystemExit, match="hot_decision_telemetry missing states_persist_queued"):
        script.validate(payload)


def test_verifier_accepts_hot_decision_telemetry() -> None:
    script = _load_script()
    payload = _report()
    payload["hot_decision_telemetry"] = {
        "states_built": 2,
        "states_persist_queued": 2,
        "dropped_events": 0,
        "last_state_age_ms": 3,
        "last_observed_to_state_us": 700,
    }

    assert script.validate(payload) == []
