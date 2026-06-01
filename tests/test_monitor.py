import json
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from polymarket_engine.domain.market_state import PriceObservation
from polymarket_engine.monitor import fetch_monitor_snapshot, render_monitor
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def test_monitor_snapshot_reads_latest_prices(tmp_path: Path) -> None:
    db_path = tmp_path / "monitor.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    ts = datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc)
    store.insert_price_tick(PriceObservation("coinbase_advanced_ws", "BTC-USD", ts, ts, 73500.0))
    store.insert_price_tick(
        PriceObservation("polymarket_rtds_chainlink", "BTC/USD", ts, ts, 73501.0)
    )

    snapshot = fetch_monitor_snapshot(db_path, limit=4)

    assert ("coinbase_advanced_ws", "BTC-USD") in snapshot.prices
    assert ("polymarket_rtds_chainlink", "BTC/USD") in snapshot.prices


def test_render_monitor_outputs_read_only_status(tmp_path: Path) -> None:
    db_path = tmp_path / "monitor.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()

    output = render_monitor(fetch_monitor_snapshot(db_path, limit=4))

    assert "Polymarket Engine Monitor" in output
    assert "READ ONLY" in output


def test_monitor_snapshot_retries_transient_duckdb_lock(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "monitor.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()

    original_connect = duckdb.connect
    calls = {"locked": 0}

    def flaky_connect(*args, **kwargs):
        if kwargs.get("read_only") and calls["locked"] == 0:
            calls["locked"] += 1
            raise duckdb.IOException("Could not set lock on file")
        return original_connect(*args, **kwargs)

    monkeypatch.setattr("polymarket_engine.monitor.duckdb.connect", flaky_connect)

    snapshot = fetch_monitor_snapshot(db_path, limit=4, lock_retry_seconds=0.2)

    assert calls["locked"] == 1
    assert snapshot.generated_at.tzinfo is not None


def test_monitor_snapshot_prefers_atomic_status_file(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.duckdb"
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-31T21:30:00+00:00",
                "prices": [
                    {
                        "source_key": "polymarket_rtds_chainlink",
                        "symbol": "BTC/USD",
                        "observed_ts": "2026-05-31T21:30:00+00:00",
                        "price": 73500.0,
                    }
                ],
                "contracts": [],
                "orderbooks": [],
                "ingest_counts": [],
            }
        ),
        encoding="utf-8",
    )

    snapshot = fetch_monitor_snapshot(db_path, limit=4, status_path=status_path)

    assert snapshot.prices[("polymarket_rtds_chainlink", "BTC/USD")] == 73500.0
