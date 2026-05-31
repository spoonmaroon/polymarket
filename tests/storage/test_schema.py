from pathlib import Path

import duckdb


def test_schema_applies_to_empty_database(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    schema_path = Path("src/polymarket_engine/storage/schema.sql")

    with duckdb.connect(str(db_path)) as conn:
        conn.sql(schema_path.read_text())
        tables = {
            row[0]
            for row in conn.sql(
                """
                SELECT table_schema || '.' || table_name
                FROM information_schema.tables
                WHERE table_schema IN ('ops', 'core', 'features', 'validation')
                """
            ).fetchall()
        }

    assert {
        "ops.ingest_files",
        "ops.ingest_checkpoints",
        "core.contracts",
        "core.price_ticks",
        "core.orderbook_snapshots",
        "features.asof_state_inputs",
        "validation.contract_labels",
    }.issubset(tables)
