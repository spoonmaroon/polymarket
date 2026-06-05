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
                WHERE table_schema IN ('ops', 'core', 'features', 'validation', 'research')
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
        "features.probability_grid_cache",
        "features.probability_outputs",
        "validation.contract_labels",
        "validation.decision_labels",
        "validation.market_outcome_history",
        "research.generator_weight_snapshots",
    }.issubset(tables)


def test_generator_weight_snapshots_schema_has_expected_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    schema_path = Path("src/polymarket_engine/storage/schema.sql")

    with duckdb.connect(str(db_path)) as conn:
        conn.sql(schema_path.read_text())
        columns = [
            row[0]
            for row in conn.sql(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'research'
                  AND table_name = 'generator_weight_snapshots'
                ORDER BY ordinal_position
                """
            ).fetchall()
        ]

    assert columns == [
        "snapshot_id",
        "runtime_asof_ts",
        "evaluated_through_ts",
        "label_window_seconds",
        "source",
        "scope_json",
        "weights_json",
        "scores_json",
        "label_counts_json",
        "created_at",
    ]


def test_probability_grid_cache_schema_has_expected_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    schema_path = Path("src/polymarket_engine/storage/schema.sql")

    with duckdb.connect(str(db_path)) as conn:
        conn.sql(schema_path.read_text())
        columns = [
            row[0]
            for row in conn.sql(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'features'
                  AND table_name = 'probability_grid_cache'
                ORDER BY ordinal_position
                """
            ).fetchall()
        ]

    assert columns == [
        "cache_key",
        "asset",
        "side",
        "market_slug",
        "start_ts",
        "expiry_ts",
        "asof_ts",
        "horizon_seconds",
        "seconds_left_bucket",
        "z_path_bucket",
        "sigma_bucket",
        "volatility_regime",
        "event_flag",
        "source_risk_flag",
        "generator_version",
        "model_version",
        "p_finish",
        "p_no_touch",
        "u_gen",
        "path_count",
        "seed",
        "training_cutoff_ts",
        "max_event_ts",
        "max_observed_ts",
        "generated_at",
        "valid_from",
        "valid_until",
        "diagnostics_json",
    ]


def test_market_outcome_history_schema_has_expected_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    schema_path = Path("src/polymarket_engine/storage/schema.sql")

    with duckdb.connect(str(db_path)) as conn:
        conn.sql(schema_path.read_text())
        columns = [
            row[0]
            for row in conn.sql(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'validation'
                  AND table_name = 'market_outcome_history'
                ORDER BY ordinal_position
                """
            ).fetchall()
        ]

    assert columns == [
        "market_id",
        "condition_id",
        "market_slug",
        "asset",
        "interval",
        "start_ts",
        "expiry_ts",
        "up_token_id",
        "down_token_id",
        "threshold_price",
        "threshold_event_ts",
        "threshold_observed_ts",
        "end_price",
        "end_event_ts",
        "end_observed_ts",
        "computed_winner",
        "computed_label_source",
        "computed_at",
        "official_winner",
        "winning_token_id",
        "official_resolution_status",
        "official_label_source",
        "official_resolved_at",
        "rule_hash",
        "mismatch",
        "updated_at",
    ]


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
