from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

import pytest


def _load_script() -> ModuleType:
    script_path = Path(__file__).parents[2] / "scripts" / "check_collector_status.py"
    spec = importlib.util.spec_from_file_location("check_collector_status", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fresh_status() -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "generated_at": now,
        "prices": [{"source_key": "polymarket_rtds_chainlink", "observed_ts": now}],
        "orderbooks": [{"contract_id": "0xabc", "observed_ts": now}],
        "source_freshness": [
            {
                "source_key": "polymarket_rtds_chainlink",
                "symbol": "ETH/USD",
                "observed_ts": now,
                "age_ms": 100,
                "stale": False,
                "missing": False,
            }
        ],
        "orderbook_freshness": [
            {
                "contract_id": "eth-market:UP",
                "symbol": "111",
                "observed_ts": now,
                "age_ms": 100,
                "stale": False,
                "missing": False,
            }
        ],
    }


def test_status_check_rejects_stale_source_freshness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    status = _fresh_status()
    status["source_freshness"] = [
        {
            "source_key": "polymarket_rtds_chainlink",
            "symbol": "ETH/USD",
            "observed_ts": "2026-06-01T09:48:01+00:00",
            "age_ms": 6_000_000,
            "stale": True,
            "missing": False,
        }
    ]
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["check_collector_status.py", "--status-path", str(status_path)],
    )

    with pytest.raises(SystemExit, match="source_freshness stale"):
        script.main()


def test_status_check_rejects_missing_orderbook_freshness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    status = _fresh_status()
    status["orderbook_freshness"] = [
        {
            "contract_id": "eth-market:UP",
            "symbol": "111",
            "observed_ts": None,
            "age_ms": None,
            "stale": True,
            "missing": True,
        }
    ]
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["check_collector_status.py", "--status-path", str(status_path)],
    )

    with pytest.raises(SystemExit, match="orderbook_freshness missing"):
        script.main()


def test_status_check_rejects_required_source_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    status = _fresh_status()
    status["source_errors"] = {
        "polymarket_market_ws": "ConnectionClosed: websocket closed",
    }
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["check_collector_status.py", "--status-path", str(status_path)],
    )

    with pytest.raises(SystemExit, match="required source error"):
        script.main()
