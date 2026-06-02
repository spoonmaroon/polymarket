from __future__ import annotations

import json
from pathlib import Path

import duckdb

from polymarket_engine.ingestion.rust_event_normalizer import (
    normalize_rust_event_file,
    normalize_rust_event_tree,
)
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def test_normalizes_chainlink_raw_event_into_price_ticks(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    raw_path = (
        tmp_path
        / "raw"
        / "polymarket_rtds_chainlink"
        / "price_update"
        / "date=2026-06-02"
        / "hour=05"
        / "events.jsonl"
    )
    _write_jsonl(
        raw_path,
        {
            "source_key": "polymarket_rtds_chainlink",
            "stream_key": "price_update",
            "symbol": "BTC/USD",
            "event_type": "chainlink_price",
            "event_ts": "2026-06-02T05:33:54Z",
            "observed_ts": "2026-06-02T05:33:55.239695967Z",
            "payload": {
                "topic": "crypto_prices_chainlink",
                "payload": {
                    "symbol": "btc/usd",
                    "timestamp": 1_780_378_434_000,
                    "value": "70600.137545",
                },
            },
        },
    )

    result = normalize_rust_event_file(path=raw_path, store=store)

    assert result.rows_read == 1
    assert result.price_ticks_written == 1
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.price_ticks").fetchone() == (1,)
        row = conn.execute(
            """
            select source_key, symbol, price, raw_file_id
            from core.price_ticks
            """
        ).fetchone()
    assert row is not None
    assert row[0] == "polymarket_rtds_chainlink"
    assert row[1] == "BTC/USD"
    assert row[2] == 70600.137545
    assert row[3] == result.file_id


def test_normalizes_clob_best_bid_ask_raw_event_into_orderbooks(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    raw_path = (
        tmp_path
        / "raw"
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / "date=2026-06-02"
        / "hour=05"
        / "events.jsonl"
    )
    _write_jsonl(
        raw_path,
        {
            "source_key": "polymarket_clob_market_ws",
            "stream_key": "best_bid_ask",
            "symbol": "token-1",
            "event_type": "best_bid_ask",
            "event_ts": "2026-06-02T05:33:54.100Z",
            "observed_ts": "2026-06-02T05:33:54.200Z",
            "payload": {
                "contract_id": "0xabc",
                "token_id": "token-1",
                "best_bid": "0",
                "best_ask": "0.01",
                "spread": "0.01",
            },
        },
    )

    result = normalize_rust_event_file(path=raw_path, store=store)

    assert result.rows_read == 1
    assert result.orderbooks_written == 1
    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute(
            """
            select venue, contract_id, token_id, best_bid, best_ask, spread, depth_json, raw_file_id
            from core.orderbook_snapshots
            """
        ).fetchone()
    assert row is not None
    assert row[0] == "polymarket"
    assert row[1] == "0xabc"
    assert row[2] == "token-1"
    assert row[3] == 0.0
    assert row[4] == 0.01
    assert row[5] == 0.01
    assert json.loads(row[6])["top_of_book"]["best_ask"] == "0.01"
    assert row[7] == result.file_id


def test_normalizes_state_manager_snapshot_into_prices_and_orderbooks(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    raw_path = (
        tmp_path
        / "raw"
        / "polymarket_state_manager"
        / "state_snapshot"
        / "date=2026-06-02"
        / "hour=05"
        / "state-manager.jsonl"
    )
    _write_jsonl(
        raw_path,
        {
            "schema_version": "rust-live-probe-state-manager-v1",
            "mode": "state-manager",
            "generated_at": "2026-06-02T05:33:56.111840329Z",
            "chainlink_prices": [
                {
                    "source_key": "polymarket_rtds_chainlink",
                    "symbol": "ETH/USD",
                    "event_ts": "2026-06-02T05:33:54Z",
                    "observed_ts": "2026-06-02T05:33:55.232426944Z",
                    "price": "1992.3355818126513",
                }
            ],
            "orderbooks": [
                {
                    "venue": "polymarket",
                    "source_key": "polymarket_rust_sdk",
                    "market_slug": "eth-updown-5m-1780378500",
                    "contract_id": "0xbook",
                    "token_id": "token-2",
                    "asset": "ETH",
                    "side": "DOWN",
                    "event_ts": "2026-06-02T05:33:54.100Z",
                    "observed_ts": "2026-06-02T05:33:54.200Z",
                    "best_bid": "0.48",
                    "best_ask": "0.50",
                    "spread": "0.02",
                    "bid_size_top": "71.25",
                    "ask_size_top": "759.5",
                    "bids": [{"price": "0.48", "size": "71.25"}],
                    "asks": [{"price": "0.50", "size": "759.5"}],
                }
            ],
        },
    )

    result = normalize_rust_event_file(path=raw_path, store=store)

    assert result.rows_read == 1
    assert result.price_ticks_written == 1
    assert result.orderbooks_written == 1
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.price_ticks").fetchone() == (1,)
        assert conn.execute("select count(*) from core.orderbook_snapshots").fetchone() == (1,)
        row = conn.execute(
            "select depth_json from core.orderbook_snapshots"
        ).fetchone()
    assert row is not None
    orderbook_depth = row[0]
    assert json.loads(orderbook_depth)["market_slug"] == "eth-updown-5m-1780378500"


def test_tree_normalizer_excludes_state_snapshots_by_default(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    raw_root = tmp_path / "raw"
    _write_jsonl(
        raw_root
        / "polymarket_rtds_chainlink"
        / "price_update"
        / "date=2026-06-02"
        / "hour=05"
        / "events.jsonl",
        {
            "source_key": "polymarket_rtds_chainlink",
            "stream_key": "price_update",
            "symbol": "BTC/USD",
            "event_type": "chainlink_price",
            "event_ts": "2026-06-02T05:33:54Z",
            "observed_ts": "2026-06-02T05:33:55Z",
            "payload": {
                "topic": "crypto_prices_chainlink",
                "payload": {"symbol": "btc/usd", "timestamp": 1, "value": 70600.0},
            },
        },
    )
    _write_jsonl(
        raw_root
        / "polymarket_state_manager"
        / "state_snapshot"
        / "date=2026-06-02"
        / "hour=05"
        / "state-manager.jsonl",
        {
            "schema_version": "rust-live-probe-state-manager-v1",
            "chainlink_prices": [
                {
                    "source_key": "polymarket_rtds_chainlink",
                    "symbol": "ETH/USD",
                    "event_ts": "2026-06-02T05:33:54Z",
                    "observed_ts": "2026-06-02T05:33:55Z",
                    "price": "1992.33",
                }
            ],
            "orderbooks": [],
        },
    )

    default_results = normalize_rust_event_tree(raw_root=raw_root, store=store)
    included_results = normalize_rust_event_tree(
        raw_root=raw_root,
        store=store,
        include_state_snapshots=True,
    )

    assert [result.path.name for result in default_results] == ["events.jsonl"]
    assert [result.path.name for result in included_results] == [
        "events.jsonl",
        "state-manager.jsonl",
    ]


def _write_jsonl(path: Path, *rows: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
