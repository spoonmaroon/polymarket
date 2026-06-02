from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from polymarket_engine.features.hot_decision_replay import (
    replay_ready_hot_decision_rows,
    verify_hot_decision_rows,
)
from polymarket_engine.ingestion.rust_event_normalizer import normalize_rust_event_tree
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def test_verifies_hot_decision_state_against_replayed_raw_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    raw_root = tmp_path / "raw"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    start_ts = datetime(2026, 6, 2, 8, 10, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(seconds=72, milliseconds=500)
    settlement_event_ts = asof_ts - timedelta(seconds=2)
    settlement_observed_ts = asof_ts - timedelta(seconds=1)
    book_event_ts = asof_ts - timedelta(seconds=3)
    book_observed_ts = asof_ts - timedelta(milliseconds=500)
    token_id = "up-token"

    _write_jsonl(
        raw_root
        / "polymarket_rtds_chainlink"
        / "price_update"
        / "date=2026-06-02"
        / "hour=08"
        / "events.jsonl",
        [
            {
                "source_key": "polymarket_rtds_chainlink",
                "stream_key": "price_update",
                "symbol": "BTC/USD",
                "event_ts": start_ts.isoformat(),
                "observed_ts": (start_ts + timedelta(milliseconds=100)).isoformat(),
                "payload": {"value": "70000.0"},
            },
            {
                "source_key": "polymarket_rtds_chainlink",
                "stream_key": "price_update",
                "symbol": "BTC/USD",
                "event_ts": settlement_event_ts.isoformat(),
                "observed_ts": settlement_observed_ts.isoformat(),
                "payload": {"value": "70050.0"},
            },
        ],
    )
    _write_jsonl(
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / "date=2026-06-02"
        / "hour=08"
        / "events.jsonl",
        [
            {
                "source_key": "polymarket_clob_market_ws",
                "stream_key": "best_bid_ask",
                "symbol": token_id,
                "event_ts": book_event_ts.isoformat(),
                "observed_ts": book_observed_ts.isoformat(),
                "payload": {
                    "contract_id": "0xcondition",
                    "token_id": token_id,
                    "best_bid": "0.61",
                    "best_ask": "0.64",
                    "spread": "0.03",
                },
            }
        ],
    )
    normalize_rust_event_tree(raw_root=raw_root, store=store)

    result = verify_hot_decision_rows(rows=[_hot_row(start_ts, asof_ts)], store=store)

    assert result.rows_checked == 1
    assert result.mismatches == ()


def test_replay_selection_reports_skip_reasons(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    raw_root = tmp_path / "raw"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    start_ts = datetime(2026, 6, 2, 8, 10, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(seconds=72, milliseconds=500)
    token_id = "up-token"

    _write_jsonl(
        raw_root
        / "polymarket_rtds_chainlink"
        / "price_update"
        / "date=2026-06-02"
        / "hour=08"
        / "events.jsonl",
        [
            {
                "source_key": "polymarket_rtds_chainlink",
                "stream_key": "price_update",
                "symbol": "BTC/USD",
                "event_ts": start_ts.isoformat(),
                "observed_ts": (asof_ts - timedelta(seconds=1)).isoformat(),
                "payload": {"value": "70000.0"},
            },
        ],
    )
    _write_jsonl(
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / "date=2026-06-02"
        / "hour=08"
        / "events.jsonl",
        [
            {
                "source_key": "polymarket_clob_market_ws",
                "stream_key": "best_bid_ask",
                "symbol": token_id,
                "event_ts": (asof_ts - timedelta(seconds=3)).isoformat(),
                "observed_ts": (asof_ts - timedelta(milliseconds=500)).isoformat(),
                "payload": {
                    "contract_id": "0xcondition",
                    "token_id": token_id,
                    "best_bid": "0.61",
                    "best_ask": "0.64",
                    "spread": "0.03",
                },
            }
        ],
    )
    normalize_rust_event_tree(raw_root=raw_root, store=store)

    selection = replay_ready_hot_decision_rows(
        rows=[
            {
                **_hot_row(start_ts, asof_ts),
                "threshold_price": None,
                "threshold_event_ts": None,
                "data_quality_flags": ["MissingThreshold"],
            },
            {
                **_hot_row(start_ts, asof_ts + timedelta(milliseconds=100)),
                "best_bid": None,
                "best_ask": None,
                "spread": None,
                "book_age_ms": None,
                "data_quality_flags": ["MissingOrderbook"],
            },
            {
                **_hot_row(start_ts, asof_ts),
                "source_age_ms": 0,
            },
            {
                **_hot_row(start_ts, asof_ts),
                "book_age_ms": 0,
            },
        ],
        store=store,
        limit=10,
    )

    assert selection.rows_skipped_quality_blocked == 2
    assert selection.rows_skipped_not_replay_ready == 2
    assert selection.rows_skipped_quality_blocked_by_reason == {
        "MissingThreshold": 1,
        "MissingOrderbook": 1,
    }
    assert selection.rows_skipped_not_replay_ready_by_reason == {
        "price_observed_after_watermark": 1,
        "orderbook_observed_after_watermark": 1,
    }


def test_replay_selection_reports_all_skip_reasons_for_one_row(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    raw_root = tmp_path / "raw"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    start_ts = datetime(2026, 6, 2, 8, 10, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(seconds=72, milliseconds=500)
    token_id = "up-token"

    _write_jsonl(
        raw_root
        / "polymarket_rtds_chainlink"
        / "price_update"
        / "date=2026-06-02"
        / "hour=08"
        / "events.jsonl",
        [
            {
                "source_key": "polymarket_rtds_chainlink",
                "stream_key": "price_update",
                "symbol": "BTC/USD",
                "event_ts": start_ts.isoformat(),
                "observed_ts": (asof_ts - timedelta(seconds=1)).isoformat(),
                "payload": {"value": "70000.0"},
            },
        ],
    )
    _write_jsonl(
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / "date=2026-06-02"
        / "hour=08"
        / "events.jsonl",
        [
            {
                "source_key": "polymarket_clob_market_ws",
                "stream_key": "best_bid_ask",
                "symbol": token_id,
                "event_ts": (asof_ts - timedelta(seconds=3)).isoformat(),
                "observed_ts": (asof_ts - timedelta(milliseconds=500)).isoformat(),
                "payload": {
                    "contract_id": "0xcondition",
                    "token_id": token_id,
                    "best_bid": "0.61",
                    "best_ask": "0.64",
                    "spread": "0.03",
                },
            }
        ],
    )
    normalize_rust_event_tree(raw_root=raw_root, store=store)

    selection = replay_ready_hot_decision_rows(
        rows=[
            {
                **_hot_row(start_ts, asof_ts),
                "source_age_ms": 0,
                "book_age_ms": 0,
            },
        ],
        store=store,
        limit=10,
    )

    assert selection.rows_skipped_not_replay_ready == 1
    assert selection.rows_skipped_not_replay_ready_by_reason == {
        "price_observed_after_watermark": 1,
        "orderbook_observed_after_watermark": 1,
    }


def _hot_row(start_ts: datetime, asof_ts: datetime) -> dict[str, object]:
    return {
        "schema_version": "rust-hot-decision-state-v1",
        "state_id": f"btc-updown-5m-{int(start_ts.timestamp())}:UP:{asof_ts.isoformat()}",
        "trigger_kind": "OrderBookTopOfBook",
        "trigger_symbol": None,
        "trigger_token_id": "up-token",
        "asof_ts": asof_ts.isoformat(),
        "contract": {
            "window": {
                "asset": "BTC",
                "interval": "5m",
                "start_ts": start_ts.isoformat(),
                "end_ts": (start_ts + timedelta(minutes=5)).isoformat(),
            },
            "up": {"asset": "BTC", "side": "Up", "token_id": "up-token"},
            "down": {"asset": "BTC", "side": "Down", "token_id": "down-token"},
        },
        "side": "Up",
        "token_id": "up-token",
        "threshold_price": "70000.0",
        "threshold_event_ts": start_ts.isoformat(),
        "settlement_price": "70050.0",
        "settlement_event_ts": (asof_ts - timedelta(seconds=2)).isoformat(),
        "best_bid": "0.61",
        "best_ask": "0.64",
        "executable_price": "0.64",
        "spread": "0.03",
        "source_age_ms": 1000,
        "book_age_ms": 500,
        "data_quality_flags": [],
        "latency": {
            "trigger_event_to_observed_ms": 2500,
            "observed_to_state_us": 100,
            "state_to_persist_us": None,
            "total_event_to_persist_ms": None,
        },
    }


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{json.dumps(row, sort_keys=True)}\n" for row in rows),
        encoding="utf-8",
    )
