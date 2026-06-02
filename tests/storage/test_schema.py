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
        "ops.raw_file_checkpoints",
        "ops.retention_manifests",
        "core.contracts",
        "core.contract_rules",
        "core.price_ticks",
        "core.orderbook_snapshots",
        "features.asof_state_inputs",
        "features.decision_snapshots",
        "features.probability_outputs",
        "validation.contract_labels",
        "validation.decision_labels",
    }.issubset(tables)


def test_apply_schema_rebuilds_incompatible_normalized_tables(tmp_path: Path) -> None:
    from polymarket_engine.storage.duckdb_store import DuckDbIngestStore

    db_path = tmp_path / "old.duckdb"
    with duckdb.connect(str(db_path)) as conn:
        conn.sql("create schema core")
        conn.sql(
            """
            create table core.contracts (
                contract_id varchar primary key,
                venue varchar not null,
                asset varchar not null,
                side varchar not null,
                threshold double not null,
                expiry_ts timestamptz not null,
                settlement_source varchar not null,
                token_id varchar not null,
                rule_text varchar not null,
                rule_hash varchar not null,
                first_seen_ts timestamptz not null,
                last_seen_ts timestamptz not null
            )
            """
        )

    DuckDbIngestStore(db_path).apply_schema()

    with duckdb.connect(str(db_path), read_only=True) as conn:
        columns = {
            row[0]
            for row in conn.sql(
                """
                select column_name
                from information_schema.columns
                where table_schema = 'core' and table_name = 'contracts'
                """
            ).fetchall()
        }

    assert "market_id" in columns
    assert "threshold" not in columns
