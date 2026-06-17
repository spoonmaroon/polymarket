from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from polymarket_engine.calibration.export import (
    CalibrationExportConfig,
    export_calibration_dataset,
)
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


ASOF = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
START = ASOF - timedelta(minutes=1)
EXPIRY = ASOF + timedelta(minutes=4)


def _seed_export_db(path: Path) -> None:
    store = DuckDbIngestStore(path)
    store.apply_schema()
    with duckdb.connect(str(path)) as conn:
        conn.execute(
            """
            insert into core.contracts (
              contract_id, venue, market_id, condition_id, slug, asset, side, token_id,
              threshold_type, threshold_price, comparison_operator, start_ts, expiry_ts,
              settlement_source_name, settlement_source_url, settlement_symbol, rule_text,
              rule_hash, parser_version, first_seen_ts, last_seen_ts
            ) values (
              'contract-up','polymarket','market-1','condition-1',
              'btc-updown-5m-1781102700','BTC','UP','token-up',
              'price',65000.0,'>',?,?,?,?,?,'rule text','rule-hash-1',
              'parser-v1',?,?
            )
            """,
            [
                START,
                EXPIRY,
                "Chainlink",
                "chainlink-url",
                "BTC/USD",
                ASOF - timedelta(minutes=2),
                ASOF - timedelta(minutes=2),
            ],
        )
        conn.execute(
            """
            insert into features.asof_state_inputs (
              state_id, contract_id, asof_ts, asset, side, threshold, threshold_source_key,
              threshold_event_ts, threshold_observed_ts, seconds_left, settlement_price,
              settlement_source_key, settlement_event_ts, settlement_observed_ts,
              proxy_prices_json, source_disagreement_bps, best_bid, best_ask,
              executable_price, spread, book_event_ts, book_observed_ts, quote_age_ms,
              source_age_ms, source_observed_lag_ms, book_age_ms, book_observed_lag_ms,
              realized_returns_json, short_realized_vol, medium_realized_vol,
              long_realized_vol, sigma_tau, volatility_regime, data_quality_flags_json,
              created_at
            ) values (
              'state-1','contract-up',?,'BTC','UP',65000.0,
              'polymarket_rtds_chainlink',?,?,300.0,65123.45,
              'polymarket_rtds_chainlink',?,?,'{}',1.4,0.68,0.71,0.71,0.03,
              ?,?,250.0,1000.0,1000.0,250.0,250.0,'[]',
              0.01,0.012,0.014,0.015,'normal','[]',?
            )
            """,
            [
                ASOF,
                START,
                START,
                ASOF - timedelta(seconds=1),
                ASOF - timedelta(seconds=1),
                ASOF - timedelta(milliseconds=250),
                ASOF - timedelta(milliseconds=250),
                ASOF,
            ],
        )
        conn.execute(
            """
            insert into features.ensemble_decisions (
              decision_id, state_id, contract_id, asof_ts, execution_mode, decision_hint,
              p_finish, p_no_touch, z_path, edge_after_costs, required_edge,
              skip_reasons_json, edge_components_json, generator_summary_json,
              execution_summary_json, supervised_live_json, created_at
            ) values (
              'decision-1','state-1','contract-up',?,'read_only','TRADE_CANDIDATE',
              0.71,0.64,0.42,0.02,0.03,'[]',
              '{"uncertainty_buffer":0.01}',
              '{"mc_dispersion":0.04}',
              '{"entry_vwap":0.715,"exit_vwap":0.675,"visible_depth":1234.5,"orderbook_imbalance":-0.12}',
              '{"supervised_live_action":"DISABLED"}',?
            )
            """,
            [ASOF, ASOF],
        )
        conn.execute(
            """
            insert into validation.market_outcome_history (
              market_id, condition_id, market_slug, asset, interval, start_ts, expiry_ts,
              up_token_id, down_token_id, threshold_price, threshold_event_ts,
              threshold_observed_ts, end_price, end_event_ts, end_observed_ts,
              computed_winner, computed_label_source, computed_at, official_winner,
              winning_token_id, official_resolution_status, official_label_source,
              official_resolved_at, rule_hash, mismatch, updated_at
            ) values (
              'market-1','condition-1','btc-updown-5m-1781102700','BTC','5m',
              ?,?,'token-up','token-down',65000.0,?,?,65100.0,?,?,
              'UP','computed',?,'UP','token-up','resolved','clob',?,
              'rule-hash-1',false,?
            )
            """,
            [
                START,
                EXPIRY,
                START,
                START,
                EXPIRY,
                EXPIRY,
                EXPIRY,
                EXPIRY,
                EXPIRY,
            ],
        )
        for offset, price in ((-20, 64990.0), (-10, 65010.0), (-5, 64998.0), (-1, 65123.45)):
            ts = ASOF + timedelta(seconds=offset)
            conn.execute(
                "insert into core.price_ticks values ('polymarket_rtds_chainlink','BTC/USD',?,?,?,null,null,null,null)",
                [ts, ts, price],
            )


def test_export_calibration_dataset_writes_labeled_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "poly.duckdb"
    out_path = tmp_path / "calibration.jsonl"
    _seed_export_db(db_path)

    result = export_calibration_dataset(
        CalibrationExportConfig(
            duckdb_path=db_path,
            out_path=out_path,
            start_ts=ASOF - timedelta(minutes=5),
            end_ts=ASOF + timedelta(minutes=1),
            include_unlabeled=False,
            limit=100,
        )
    )

    assert result.rows_written == 1
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["state_id"] == "state-1"
    assert payload["final_label"] == 1
    assert payload["resolved_outcome"] == "UP"
    assert payload["settlement_price_at_expiry"] == 65100.0
    assert payload["p_finish_mc"] == 0.71
    assert payload["mc_generator_dispersion"] == 0.04
    assert payload["target_size_ask_vwap"] == 0.715
    assert payload["target_size_bid_vwap"] == 0.675
    assert payload["threshold_cross_count"] == 3
    assert payload["near_threshold_congestion"] >= 1
    assert payload["event_window_flag"] == "regular"
    assert payload["feature_version"] == "calibration-features-v2"


def test_export_skips_unlabeled_rows_by_default(tmp_path: Path) -> None:
    db_path = tmp_path / "poly.duckdb"
    out_path = tmp_path / "calibration.jsonl"
    _seed_export_db(db_path)
    with duckdb.connect(str(db_path)) as conn:
        conn.execute("delete from validation.market_outcome_history")

    result = export_calibration_dataset(
        CalibrationExportConfig(
            duckdb_path=db_path,
            out_path=out_path,
            start_ts=None,
            end_ts=None,
            include_unlabeled=False,
            limit=100,
        )
    )

    assert result.rows_written == 0
    assert out_path.read_text(encoding="utf-8") == ""


def test_export_includes_unlabeled_rows_without_forcing_zero_end_price(tmp_path: Path) -> None:
    db_path = tmp_path / "poly.duckdb"
    out_path = tmp_path / "calibration.jsonl"
    _seed_export_db(db_path)
    with duckdb.connect(str(db_path)) as conn:
        conn.execute(
            """
            update validation.market_outcome_history
            set official_winner = null,
                computed_winner = null,
                end_price = null,
                official_resolution_status = 'pending'
            """
        )

    result = export_calibration_dataset(
        CalibrationExportConfig(
            duckdb_path=db_path,
            out_path=out_path,
            start_ts=None,
            end_ts=None,
            include_unlabeled=True,
            limit=100,
        )
    )

    assert result.rows_written == 1
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["final_label"] is None
    assert payload["resolved_outcome"] is None
    assert payload["settlement_price_at_expiry"] is None


def test_export_scopes_tick_history_features_to_settlement_source_key(tmp_path: Path) -> None:
    db_path = tmp_path / "poly.duckdb"
    out_path = tmp_path / "calibration.jsonl"
    _seed_export_db(db_path)
    with duckdb.connect(str(db_path)) as conn:
        unrelated_ticks = (
            (-45, 64999.5),
            (-30, 65000.1),
            (-15, 64999.9),
            (-8, 65000.2),
            (-3, 64999.8),
        )
        for offset, price in unrelated_ticks:
            ts = ASOF + timedelta(seconds=offset)
            conn.execute(
                "insert into core.price_ticks values ('unrelated_btc_feed','BTC/USD',?,?,?,null,null,null,null)",
                [ts, ts, price],
            )

    result = export_calibration_dataset(
        CalibrationExportConfig(
            duckdb_path=db_path,
            out_path=out_path,
            start_ts=ASOF - timedelta(minutes=5),
            end_ts=ASOF + timedelta(minutes=1),
            include_unlabeled=False,
            limit=100,
        )
    )

    assert result.rows_written == 1
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["threshold_cross_count"] == 3
    assert payload["near_threshold_congestion"] == 1
    assert payload["recent_wick_size"] == (65123.45 - 64990.0) / 65123.45
