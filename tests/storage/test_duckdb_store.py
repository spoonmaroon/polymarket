from datetime import datetime, timezone
from pathlib import Path

import duckdb

from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def test_duckdb_ingest_store_registers_written_file(tmp_path: Path) -> None:
    db_path = tmp_path / "collector.duckdb"
    raw_path = tmp_path / "file.parquet"
    raw_path.write_bytes(b"abc")
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    store.register_ingest_file(
        file_id="file-1",
        source_key="coinbase_advanced_ws",
        stream_key="ticker",
        partition_date="2026-05-31",
        partition_hour=21,
        path=str(raw_path),
        sha256="abc123",
        row_count=2,
        first_event_ts=datetime(2026, 5, 31, 21, 0, 0, tzinfo=timezone.utc),
        last_event_ts=datetime(2026, 5, 31, 21, 0, 1, tzinfo=timezone.utc),
    )

    with duckdb.connect(str(db_path)) as conn:
        rows = conn.sql("select source_key, stream_key, row_count from ops.ingest_files").fetchall()

    assert rows == [("coinbase_advanced_ws", "ticker", 2)]
