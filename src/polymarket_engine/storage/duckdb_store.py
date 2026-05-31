from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb


class DuckDbIngestStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def apply_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path = Path(__file__).with_name("schema.sql")
        with duckdb.connect(str(self.db_path)) as conn:
            conn.sql(schema_path.read_text())

    def register_ingest_file(
        self,
        file_id: str,
        source_key: str,
        stream_key: str,
        partition_date: str,
        partition_hour: int,
        path: str,
        sha256: str,
        row_count: int,
        first_event_ts: datetime,
        last_event_ts: datetime,
    ) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into ops.ingest_files
                (file_id, source_key, stream_key, partition_date, partition_hour, path, sha256,
                 row_count, first_event_ts, last_event_ts, written_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    file_id,
                    source_key,
                    stream_key,
                    partition_date,
                    partition_hour,
                    path,
                    sha256,
                    row_count,
                    first_event_ts,
                    last_event_ts,
                    datetime.now(timezone.utc),
                ],
            )
