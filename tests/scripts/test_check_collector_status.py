from __future__ import annotations

import importlib.util
import json
import os
import time
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


def _fresh_state_manager_status() -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": "rust-live-probe-state-manager-v1",
        "mode": "state-manager",
        "generated_at": now,
        "current": [{}, {}],
        "next": [{}, {}],
        "next_next": [{}, {}],
        "chainlink_prices": [
            {
                "source_key": "polymarket_rtds_chainlink",
                "symbol": "BTC/USD",
                "observed_ts": now,
            },
            {
                "source_key": "polymarket_rtds_chainlink",
                "symbol": "ETH/USD",
                "observed_ts": now,
            },
        ],
        "orderbooks": [
            {"token_id": "btc-up", "observed_ts": now},
            {"token_id": "btc-down", "observed_ts": now},
            {"token_id": "eth-up", "observed_ts": now},
            {"token_id": "eth-down", "observed_ts": now},
        ],
        "websocket_status": [
            {
                "source_key": "polymarket_rtds_chainlink",
                "channel": "crypto_prices_chainlink",
                "connection_state": "Connected { since: Instant { tv_sec: 1, tv_nsec: 0 } }",
                "reconnect_count": 0,
                "subscription_count": 1,
                "active_token_count": 2,
                "ended_stream_count": 0,
                "stream_error_count": 0,
                "last_event_age_ms": 100,
            },
            {
                "source_key": "polymarket_clob_market_ws",
                "channel": "market",
                "connection_state": "Connected { since: Instant { tv_sec: 1, tv_nsec: 0 } }",
                "reconnect_count": 0,
                "subscription_count": 8,
                "active_token_count": 8,
                "ended_stream_count": 0,
                "stream_error_count": 0,
                "last_event_age_ms": 100,
            },
        ],
        "health_flags": [],
    }


def _write_raw_event_journal(root: Path, relative: str, *, mtime_age_seconds: float) -> None:
    path = root / relative / "date=2026-06-02" / "hour=04" / "events.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"ok": true}\n', encoding="utf-8")
    mtime = time.time() - mtime_age_seconds
    os.utime(path, (mtime, mtime))


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
                    {
                        "table": "core.price_ticks",
                        "rows": 1,
                        "latest_ts": latest_iso,
                    },
                    {
                        "table": "core.orderbook_snapshots",
                        "rows": 1,
                        "latest_ts": latest_iso,
                    },
                    {
                        "table": "features.asof_state_inputs",
                        "rows": 1,
                        "latest_ts": latest_iso,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    mtime = time.time() - mtime_age_seconds
    os.utime(path, (mtime, mtime))


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


def test_status_check_tolerates_missing_future_orderbook_freshness(
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

    assert script.main() == 0


def test_status_check_rejects_empty_orderbooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    status = _fresh_status()
    status["orderbooks"] = []
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["check_collector_status.py", "--status-path", str(status_path)],
    )

    with pytest.raises(SystemExit, match="status has no orderbook rows"):
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


def test_status_check_allows_transient_market_discovery_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    status = _fresh_status()
    status["source_errors"] = {
        "polymarket_markets": "ReadError: transient gamma fetch failure",
    }
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["check_collector_status.py", "--status-path", str(status_path)],
    )

    assert script.main() == 0


def test_status_check_allows_stale_optional_proxy_freshness(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    status = _fresh_status()
    status["source_freshness"] = [
        {
            "source_key": "polymarket_rtds_crypto",
            "symbol": "BTC/USDT",
            "observed_ts": "2026-06-01T11:19:00+00:00",
            "age_ms": 60_000,
            "stale": True,
            "missing": False,
            "required": False,
        }
    ]
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["check_collector_status.py", "--status-path", str(status_path)],
    )

    assert script.main() == 0


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("connection_state", "Disconnected", "not connected"),
        ("ended_stream_count", 1, "ended streams"),
        ("stream_error_count", 1, "stream errors"),
        ("subscription_count", 0, "subscription_count"),
        ("active_token_count", 0, "active_token_count"),
        ("last_event_age_ms", 60_000, "event stale"),
    ],
)
def test_state_manager_status_rejects_bad_websocket_health(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    expected: str,
) -> None:
    script = _load_script()
    status = _fresh_state_manager_status()
    websocket_status = status["websocket_status"]
    assert isinstance(websocket_status, list)
    assert isinstance(websocket_status[0], dict)
    websocket_status[0][field] = value
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["check_collector_status.py", "--status-path", str(status_path)],
    )

    with pytest.raises(SystemExit, match=expected):
        script.main()


def test_state_manager_status_accepts_missing_next_next_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
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

    assert script.main() == 0
    assert "'ok': True" in capsys.readouterr().out


def test_state_manager_status_rejects_missing_next_next_when_three_windows_expected(
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
        [
            "check_collector_status.py",
            "--status-path",
            str(status_path),
            "--expected-prewarm-windows",
            "3",
        ],
    )

    with pytest.raises(
        SystemExit,
        match="state-manager missing next_next BTC/ETH contracts",
    ):
        script.main()


def test_state_manager_status_accepts_healthy_websocket_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(_fresh_state_manager_status()), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["check_collector_status.py", "--status-path", str(status_path)],
    )

    assert script.main() == 0


def test_state_manager_status_accepts_connected_websocket_without_first_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    status = _fresh_state_manager_status()
    websocket_status = status["websocket_status"]
    assert isinstance(websocket_status, list)
    assert isinstance(websocket_status[1], dict)
    websocket_status[1]["last_event_age_ms"] = None
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    monkeypatch.setattr(
        "sys.argv",
        ["check_collector_status.py", "--status-path", str(status_path)],
    )

    assert script.main() == 0


def test_state_manager_status_rejects_stale_raw_websocket_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    status_path = tmp_path / "status.json"
    raw_root = tmp_path / "raw"
    status_path.write_text(json.dumps(_fresh_state_manager_status()), encoding="utf-8")
    _write_raw_event_journal(
        raw_root,
        "polymarket_rtds_chainlink/price_update",
        mtime_age_seconds=60.0,
    )
    _write_raw_event_journal(
        raw_root,
        "polymarket_clob_market_ws/best_bid_ask",
        mtime_age_seconds=0.0,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_collector_status.py",
            "--status-path",
            str(status_path),
            "--raw-root",
            str(raw_root),
            "--max-raw-event-age-ms",
            "10000",
        ],
    )

    with pytest.raises(SystemExit, match="raw journal stale"):
        script.main()


def test_state_manager_status_accepts_fresh_raw_websocket_journals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    status_path = tmp_path / "status.json"
    raw_root = tmp_path / "raw"
    status_path.write_text(json.dumps(_fresh_state_manager_status()), encoding="utf-8")
    _write_raw_event_journal(
        raw_root,
        "polymarket_rtds_chainlink/price_update",
        mtime_age_seconds=0.0,
    )
    _write_raw_event_journal(
        raw_root,
        "polymarket_clob_market_ws/best_bid_ask",
        mtime_age_seconds=0.0,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_collector_status.py",
            "--status-path",
            str(status_path),
            "--raw-root",
            str(raw_root),
            "--max-raw-event-age-ms",
            "10000",
        ],
    )

    assert script.main() == 0


def test_state_manager_status_does_not_require_fresh_clob_raw_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    status_path = tmp_path / "status.json"
    raw_root = tmp_path / "raw"
    status_path.write_text(json.dumps(_fresh_state_manager_status()), encoding="utf-8")
    _write_raw_event_journal(
        raw_root,
        "polymarket_rtds_chainlink/price_update",
        mtime_age_seconds=0.0,
    )
    _write_raw_event_journal(
        raw_root,
        "polymarket_clob_market_ws/best_bid_ask",
        mtime_age_seconds=60.0,
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "check_collector_status.py",
            "--status-path",
            str(status_path),
            "--raw-root",
            str(raw_root),
            "--max-raw-event-age-ms",
            "10000",
        ],
    )

    assert script.main() == 0


def test_state_manager_status_rejects_stale_normalized_health_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script()
    status_path = tmp_path / "status.json"
    raw_root = tmp_path / "raw"
    normalized_health_path = tmp_path / "live" / "normalized_health.json"
    status_path.write_text(json.dumps(_fresh_state_manager_status()), encoding="utf-8")
    _write_raw_event_journal(
        raw_root,
        "polymarket_rtds_chainlink/price_update",
        mtime_age_seconds=0.0,
    )
    _write_raw_event_journal(
        raw_root,
        "polymarket_clob_market_ws/best_bid_ask",
        mtime_age_seconds=0.0,
    )
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
    _write_raw_event_journal(
        raw_root,
        "polymarket_rtds_chainlink/price_update",
        mtime_age_seconds=0.0,
    )
    _write_raw_event_journal(
        raw_root,
        "polymarket_clob_market_ws/best_bid_ask",
        mtime_age_seconds=0.0,
    )
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
