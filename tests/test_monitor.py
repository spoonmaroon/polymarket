from datetime import datetime, timezone
from pathlib import Path

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
