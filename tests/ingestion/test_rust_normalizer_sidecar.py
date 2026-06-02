from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import duckdb
import pytest

from polymarket_engine.ingestion.rust_normalizer_sidecar import (
    run_rust_normalizer_cycle,
    run_rust_normalizer_loop,
)
from polymarket_engine.storage import duckdb_store


def test_sidecar_cycle_normalizes_builds_states_and_writes_health(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)

    result = run_rust_normalizer_cycle(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        include_next=False,
    )

    assert result.files == 2
    assert result.rows_read == 4
    assert result.bytes_read > 0
    assert result.price_ticks_written == 2
    assert result.orderbooks_written == 2
    assert result.contracts_upserted == 2
    assert result.states_written == 2
    assert result.elapsed_ms >= 0
    assert result.normalize_ms >= 0
    assert result.state_ms >= 0
    assert result.health_ms >= 0
    assert health_path.exists()
    health_payload = json.loads(health_path.read_text(encoding="utf-8"))
    assert health_payload["schema_version"] == "polymarket-normalized-health-v1"
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.price_ticks").fetchone() == (2,)
        assert conn.execute("select count(*) from core.orderbook_snapshots").fetchone() == (2,)
        assert conn.execute("select count(*) from features.asof_state_inputs").fetchone() == (2,)


def test_sidecar_cycle_writes_health_when_status_is_missing(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)

    result = run_rust_normalizer_cycle(
        raw_root=raw_root,
        db_path=db_path,
        status_path=tmp_path / "live" / "missing-status.json",
        normalized_health_path=health_path,
        include_next=False,
    )

    assert result.rows_read == 4
    assert result.contracts_upserted == 0
    assert result.states_written == 0
    assert result.unavailable == ()
    assert health_path.exists()


def test_sidecar_loop_reuses_process_and_sleeps_between_cycles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    sleeps: list[float] = []
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        sleeps.append,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=1.25,
        include_next=False,
        max_cycles=2,
    )

    lines = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("normalizer_cycle ")
    ]
    assert len(lines) == 2
    assert _log_values(lines[0])["rows_read"] == "4"
    assert _log_values(lines[1])["rows_read"] == "0"
    assert sleeps == [1.25]


def test_sidecar_loop_reuses_one_duckdb_connection_across_cycles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        lambda _: None,
    )
    duckdb_module = getattr(duckdb_store, "duckdb")
    real_connect = duckdb_module.connect
    connect_count = 0

    def counting_connect(*args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        nonlocal connect_count
        connect_count += 1
        return cast(duckdb.DuckDBPyConnection, real_connect(*args, **kwargs))

    monkeypatch.setattr(duckdb_module, "connect", counting_connect)

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert connect_count == 1


def _write_raw_tree(*, raw_root: Path, start_ts: datetime, asof_ts: datetime) -> None:
    raw_root.mkdir(parents=True, exist_ok=True)
    (raw_root / ".polymarket_archive_root").write_text("", encoding="utf-8")
    _write_jsonl(
        raw_root
        / "polymarket_rtds_chainlink"
        / "price_update"
        / "date=2026-06-02"
        / "hour=06"
        / "events.jsonl",
        _chainlink_row("BTC/USD", start_ts, start_ts, 70_000.0),
        _chainlink_row("BTC/USD", asof_ts - timedelta(seconds=2), asof_ts - timedelta(seconds=1), 70_125.0),
    )
    _write_jsonl(
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / "date=2026-06-02"
        / "hour=06"
        / "events.jsonl",
        _orderbook_row("up-token", asof_ts - timedelta(seconds=2), asof_ts - timedelta(seconds=1), 0.61, 0.64),
        _orderbook_row("down-token", asof_ts - timedelta(seconds=2), asof_ts - timedelta(seconds=1), 0.36, 0.39),
    )


def _write_status(path: Path, *, start_ts: datetime, asof_ts: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slug = f"btc-updown-5m-{int(start_ts.timestamp())}"
    payload = {
        "schema_version": "rust-live-probe-state-manager-v1",
        "mode": "state-manager",
        "generated_at": asof_ts.isoformat(),
        "current": [
            {
                "window": {
                    "asset": "BTC",
                    "interval": "5m",
                    "start_ts": start_ts.isoformat(),
                    "end_ts": (start_ts + timedelta(minutes=5)).isoformat(),
                },
                "up": {"asset": "BTC", "side": "Up", "token_id": "up-token"},
                "down": {"asset": "BTC", "side": "Down", "token_id": "down-token"},
            }
        ],
        "orderbooks": [
            {
                "market_slug": slug,
                "contract_id": "0xcondition",
                "token_id": "up-token",
            },
            {
                "market_slug": slug,
                "contract_id": "0xcondition",
                "token_id": "down-token",
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _chainlink_row(symbol: str, event_ts: datetime, observed_ts: datetime, value: float) -> dict[str, object]:
    return {
        "source_key": "polymarket_rtds_chainlink",
        "stream_key": "price_update",
        "symbol": symbol,
        "event_type": "chainlink_price",
        "event_ts": event_ts.isoformat(),
        "observed_ts": observed_ts.isoformat(),
        "payload": {"value": str(value)},
    }


def _orderbook_row(
    token_id: str,
    event_ts: datetime,
    observed_ts: datetime,
    best_bid: float,
    best_ask: float,
) -> dict[str, object]:
    return {
        "source_key": "polymarket_clob_market_ws",
        "stream_key": "best_bid_ask",
        "symbol": token_id,
        "event_type": "best_bid_ask",
        "event_ts": event_ts.isoformat(),
        "observed_ts": observed_ts.isoformat(),
        "payload": {
            "contract_id": "0xcondition",
            "token_id": token_id,
            "best_bid": str(best_bid),
            "best_ask": str(best_ask),
            "spread": str(best_ask - best_bid),
        },
    }


def _write_jsonl(path: Path, *rows: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _log_values(line: str) -> dict[str, str]:
    return dict(part.split("=", maxsplit=1) for part in line.split()[1:])
