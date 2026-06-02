from __future__ import annotations

import builtins
import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Sequence, cast

import duckdb
import msgspec
import pytest

from polymarket_engine.domain.market_state import OrderBookObservation, PriceObservation
from polymarket_engine.ingestion import rust_event_normalizer
from polymarket_engine.ingestion.rust_event_normalizer import (
    _complete_jsonl_byte_limit,
    _file_id,
    _iter_jsonl,
    _optional_probability_float,
    _parse_ts,
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


def test_top_of_book_depth_json_materializes_without_json_dumps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        _orderbook_row(
            "token-1",
            "2026-06-02T05:33:54Z",
            "2026-06-02T05:33:55Z",
            0.61,
            0.64,
        ),
    )
    json_dumps_calls = 0
    real_json_dumps = json.dumps

    def counting_json_dumps(*args: Any, **kwargs: Any) -> str:
        nonlocal json_dumps_calls
        json_dumps_calls += 1
        return real_json_dumps(*args, **kwargs)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_event_normalizer.json.dumps",
        counting_json_dumps,
    )

    result = normalize_rust_event_file(path=raw_path, store=store)

    assert result.orderbooks_written == 1
    assert json_dumps_calls == 0
    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute("select depth_json from core.orderbook_snapshots").fetchone()
    assert row is not None
    assert json.loads(row[0]) == {
        "source_key": "polymarket_clob_market_ws",
        "stream_key": "best_bid_ask",
        "top_of_book": {
            "best_ask": "0.64",
            "best_bid": "0.61",
            "spread": "0.03",
        },
    }


def test_top_of_book_rows_skip_intermediate_row_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        _orderbook_row(
            "token-1",
            "2026-06-02T05:33:54Z",
            "2026-06-02T05:33:55Z",
            0.61,
            0.64,
        ),
    )
    top_of_book_row_calls = 0
    real_top_of_book_row = rust_event_normalizer._TopOfBookRow

    def counting_top_of_book_row(*args: Any, **kwargs: Any) -> object:
        nonlocal top_of_book_row_calls
        top_of_book_row_calls += 1
        return real_top_of_book_row(*args, **kwargs)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_event_normalizer._TopOfBookRow",
        counting_top_of_book_row,
    )

    result = normalize_rust_event_file(path=raw_path, store=store)

    assert result.orderbooks_written == 1
    assert top_of_book_row_calls == 0


def test_top_of_book_rows_skip_orderbook_observation_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        _orderbook_row(
            "token-1",
            "2026-06-02T05:33:54Z",
            "2026-06-02T05:33:55Z",
            0.61,
            0.64,
        ),
    )
    orderbook_observation_calls = 0
    real_orderbook_observation = OrderBookObservation

    def counting_orderbook_observation(
        *args: Any,
        **kwargs: Any,
    ) -> OrderBookObservation:
        nonlocal orderbook_observation_calls
        orderbook_observation_calls += 1
        return real_orderbook_observation(*args, **kwargs)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_event_normalizer.OrderBookObservation",
        counting_orderbook_observation,
    )

    result = normalize_rust_event_file(path=raw_path, store=store)

    assert result.orderbooks_written == 1
    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute(
            """
            select contract_id, token_id, best_bid, best_ask, spread
            from core.orderbook_snapshots
            """
        ).fetchone()
    assert row is not None
    assert row[:4] == ("0xabc", "token-1", 0.61, 0.64)
    assert round(float(row[4]), 2) == 0.03
    assert orderbook_observation_calls == 0


def test_top_of_book_file_uses_typed_json_decoder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        _orderbook_row(
            "token-1",
            "2026-06-02T05:33:54Z",
            "2026-06-02T05:33:55Z",
            0.61,
            0.64,
        ),
    )
    decode_types: list[object] = []
    real_decode = msgspec.json.decode

    def tracking_decode(value: Any, *args: Any, **kwargs: Any) -> Any:
        decode_types.append(kwargs.get("type"))
        return real_decode(value, *args, **kwargs)

    monkeypatch.setattr("msgspec.json.decode", tracking_decode)

    result = normalize_rust_event_file(path=raw_path, store=store)

    assert result.orderbooks_written == 1
    assert decode_types
    assert all(decoded_type is not None for decoded_type in decode_types)


def test_top_of_book_rows_defer_observed_timestamp_parsing_to_duckdb(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        _orderbook_row(
            "token-1",
            "2026-06-02T05:33:54.100Z",
            "2026-06-02T05:33:55.239695967Z",
            0.61,
            0.64,
        ),
    )
    parse_ts_calls = 0
    real_parse_ts = rust_event_normalizer._parse_ts

    def counting_parse_ts(value: object) -> datetime:
        nonlocal parse_ts_calls
        parse_ts_calls += 1
        return real_parse_ts(value)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_event_normalizer._parse_ts",
        counting_parse_ts,
    )

    result = normalize_rust_event_file(path=raw_path, store=store)

    assert result.orderbooks_written == 1
    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute(
            """
            select observed_ts
            from core.orderbook_snapshots
            """
        ).fetchone()
    assert row == (datetime(2026, 6, 2, 5, 33, 55, 239695, tzinfo=timezone.utc),)
    assert parse_ts_calls == 1


def test_top_of_book_rows_parse_only_event_time_bounds_in_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        _orderbook_row(
            "token-1",
            "2026-06-02T05:33:54.100Z",
            "2026-06-02T05:33:54.200000000Z",
            0.61,
            0.64,
        ),
        _orderbook_row(
            "token-1",
            "2026-06-02T05:33:55.100Z",
            "2026-06-02T05:33:55.200000000Z",
            0.62,
            0.65,
        ),
        _orderbook_row(
            "token-1",
            "2026-06-02T05:33:56.100Z",
            "2026-06-02T05:33:56.200000000Z",
            0.63,
            0.66,
        ),
    )
    parse_ts_calls = 0
    real_parse_ts = rust_event_normalizer._parse_ts

    def counting_parse_ts(value: object) -> datetime:
        nonlocal parse_ts_calls
        parse_ts_calls += 1
        return real_parse_ts(value)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_event_normalizer._parse_ts",
        counting_parse_ts,
    )

    result = normalize_rust_event_file(path=raw_path, store=store)

    assert result.orderbooks_written == 3
    with duckdb.connect(str(db_path), read_only=True) as conn:
        rows = conn.execute(
            """
            select event_ts
            from core.orderbook_snapshots
            order by event_ts
            """
        ).fetchall()
    assert rows == [
        (datetime(2026, 6, 2, 5, 33, 54, 100000, tzinfo=timezone.utc),),
        (datetime(2026, 6, 2, 5, 33, 55, 100000, tzinfo=timezone.utc),),
        (datetime(2026, 6, 2, 5, 33, 56, 100000, tzinfo=timezone.utc),),
    ]
    assert parse_ts_calls == 2


def test_normalizes_clob_spread_from_bid_ask_when_payload_spread_is_stale(tmp_path: Path) -> None:
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
                "best_bid": "0.61",
                "best_ask": "0.64",
                "spread": "0.02",
            },
        },
    )

    normalize_rust_event_file(path=raw_path, store=store)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute(
            """
            select spread, depth_json
            from core.orderbook_snapshots
            """
        ).fetchone()
    assert row is not None
    assert round(float(row[0]), 2) == 0.03
    assert json.loads(row[1])["top_of_book"]["spread"] == "0.02"


def test_normalizes_clob_without_parsing_unused_payload_spread(tmp_path: Path) -> None:
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
    row = _orderbook_row(
        "token-1",
        "2026-06-02T05:33:54.100Z",
        "2026-06-02T05:33:54.200Z",
        0.61,
        0.64,
    )
    cast(dict[str, object], row["payload"])["spread"] = "stale-unparseable"
    _write_jsonl(raw_path, row)

    result = normalize_rust_event_file(path=raw_path, store=store)

    assert result.orderbooks_written == 1
    with duckdb.connect(str(db_path), read_only=True) as conn:
        stored = conn.execute(
            """
            select spread, depth_json
            from core.orderbook_snapshots
            """
        ).fetchone()
    assert stored is not None
    assert round(float(stored[0]), 2) == 0.03
    assert json.loads(stored[1])["top_of_book"]["spread"] == "stale-unparseable"


def test_normalizer_collapses_consecutive_duplicate_chainlink_state(tmp_path: Path) -> None:
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
        _chainlink_row("BTC/USD", "2026-06-02T05:33:54Z", "2026-06-02T05:33:55Z", 70600.0),
        _chainlink_row("BTC/USD", "2026-06-02T05:33:54Z", "2026-06-02T05:33:56Z", 70600.0),
    )

    result = normalize_rust_event_file(path=raw_path, store=store)

    assert result.rows_read == 2
    assert result.price_ticks_written == 1
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.price_ticks").fetchone() == (1,)


def test_normalizer_collapses_consecutive_duplicate_top_of_book_state(tmp_path: Path) -> None:
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
        _orderbook_row("token-1", "2026-06-02T05:33:54.100Z", "2026-06-02T05:33:54.200Z", 0.61, 0.64),
        _orderbook_row("token-1", "2026-06-02T05:33:54.100Z", "2026-06-02T05:33:54.300Z", 0.61, 0.64),
    )

    result = normalize_rust_event_file(path=raw_path, store=store)

    assert result.rows_read == 2
    assert result.orderbooks_written == 1
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.orderbook_snapshots").fetchone() == (1,)


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


def test_tree_normalizer_batches_raw_checkpoint_reads(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = _CountingCheckpointStore(db_path)
    store.apply_schema()
    raw_root = tmp_path / "raw"
    _write_jsonl(
        raw_root
        / "polymarket_rtds_chainlink"
        / "price_update"
        / "date=2026-06-02"
        / "hour=05"
        / "events.jsonl",
        _chainlink_row("BTC/USD", "2026-06-02T05:33:54Z", "2026-06-02T05:33:55Z", 70600.0),
    )
    _write_jsonl(
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / "date=2026-06-02"
        / "hour=05"
        / "events.jsonl",
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
                "best_bid": "0.61",
                "best_ask": "0.64",
                "spread": "0.03",
            },
        },
    )

    normalize_rust_event_tree(raw_root=raw_root, store=store)

    assert store.raw_file_checkpoints_calls == 1
    assert store.raw_file_checkpoint_calls == 0


def test_normalizer_only_reads_new_jsonl_bytes_on_second_pass(tmp_path: Path) -> None:
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
        _chainlink_row("BTC/USD", "2026-06-02T05:33:54Z", "2026-06-02T05:33:55Z", 70600.0),
    )
    first = normalize_rust_event_file(path=raw_path, store=store)

    _append_jsonl(
        raw_path,
        _chainlink_row("BTC/USD", "2026-06-02T05:33:56Z", "2026-06-02T05:33:57Z", 70601.0),
    )
    second = normalize_rust_event_file(path=raw_path, store=store)
    third = normalize_rust_event_file(path=raw_path, store=store)

    assert first.rows_read == 1
    assert second.rows_read == 1
    assert second.price_ticks_written == 1
    assert third.rows_read == 0
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.price_ticks").fetchone() == (2,)


def test_normalizer_tracks_event_time_bounds_without_post_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        _chainlink_row("BTC/USD", "2026-06-02T05:33:56Z", "2026-06-02T05:33:57Z", 70601.0),
        _chainlink_row("BTC/USD", "2026-06-02T05:33:54Z", "2026-06-02T05:33:55Z", 70600.0),
    )
    min_calls = 0
    max_calls = 0

    def counting_min(*args: Any, **kwargs: Any) -> Any:
        nonlocal min_calls
        min_calls += 1
        return builtins.min(*args, **kwargs)

    def counting_max(*args: Any, **kwargs: Any) -> Any:
        nonlocal max_calls
        max_calls += 1
        return builtins.max(*args, **kwargs)

    monkeypatch.setattr(rust_event_normalizer, "min", counting_min, raising=False)
    monkeypatch.setattr(rust_event_normalizer, "max", counting_max, raising=False)

    result = normalize_rust_event_file(path=raw_path, store=store)

    assert result.price_ticks_written == 2
    assert min_calls == 0
    assert max_calls == 0
    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute(
            """
            select first_event_ts, last_event_ts
            from ops.ingest_files
            where path = ?
            """,
            [str(raw_path)],
        ).fetchone()
    assert row is not None
    assert row[0] == datetime.fromisoformat("2026-06-02 05:33:54+00:00")
    assert row[1] == datetime.fromisoformat("2026-06-02 05:33:56+00:00")


def test_normalizer_skips_empty_storage_writes_when_checkpoint_is_current(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.duckdb"
    store = _CountingCheckpointStore(db_path)
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
        _chainlink_row("BTC/USD", "2026-06-02T05:33:54Z", "2026-06-02T05:33:55Z", 70600.0),
    )
    normalize_rust_event_file(path=raw_path, store=store)
    store.insert_price_ticks_calls = 0
    store.insert_orderbook_snapshots_calls = 0

    result = normalize_rust_event_file(path=raw_path, store=store)

    assert result.rows_read == 0
    assert store.insert_price_ticks_calls == 0
    assert store.insert_orderbook_snapshots_calls == 0


def test_normalizer_skips_ingest_file_registration_for_duplicate_only_chunk(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.duckdb"
    store = _CountingCheckpointStore(db_path)
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
    row = {
        "source_key": "polymarket_clob_market_ws",
        "stream_key": "best_bid_ask",
        "symbol": "token-1",
        "event_type": "best_bid_ask",
        "event_ts": "2026-06-02T05:33:54.100Z",
        "observed_ts": "2026-06-02T05:33:54.200Z",
        "payload": {
            "contract_id": "0xabc",
            "token_id": "token-1",
            "best_bid": "0.61",
            "best_ask": "0.64",
            "spread": "0.03",
        },
    }
    orderbook_state_cache: dict[tuple[str, str], tuple[object, ...]] = {}
    _write_jsonl(raw_path, row)
    first = normalize_rust_event_file(
        path=raw_path,
        store=store,
        last_orderbook_state_by_token=orderbook_state_cache,
    )
    store.insert_price_ticks_calls = 0
    store.insert_orderbook_snapshots_calls = 0
    store.register_ingest_file_calls = 0
    _append_jsonl(raw_path, row)

    second = normalize_rust_event_file(
        path=raw_path,
        store=store,
        last_orderbook_state_by_token=orderbook_state_cache,
    )

    assert first.orderbooks_written == 1
    assert second.rows_read == 1
    assert second.orderbooks_written == 0
    assert store.insert_price_ticks_calls == 0
    assert store.insert_orderbook_snapshots_calls == 0
    assert store.register_ingest_file_calls == 0


def test_duplicate_chainlink_rows_skip_price_observation_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "state.duckdb"
    store = _CountingCheckpointStore(db_path)
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
    row = _chainlink_row(
        "BTC/USD",
        "2026-06-02T05:33:54Z",
        "2026-06-02T05:33:55Z",
        70600.0,
    )
    price_state_cache: dict[tuple[str, str], tuple[object, ...]] = {}
    _write_jsonl(raw_path, row)
    first = normalize_rust_event_file(
        path=raw_path,
        store=store,
        last_price_state_by_symbol=price_state_cache,
    )
    store.insert_price_ticks_calls = 0
    _append_jsonl(raw_path, row)
    price_observation_calls = 0
    parse_ts_calls = 0
    real_price_observation = PriceObservation
    real_parse_ts = rust_event_normalizer._parse_ts

    def counting_price_observation(*args: Any, **kwargs: Any) -> PriceObservation:
        nonlocal price_observation_calls
        price_observation_calls += 1
        return real_price_observation(*args, **kwargs)

    def counting_parse_ts(value: object) -> datetime:
        nonlocal parse_ts_calls
        parse_ts_calls += 1
        return real_parse_ts(value)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_event_normalizer.PriceObservation",
        counting_price_observation,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_event_normalizer._parse_ts",
        counting_parse_ts,
    )

    duplicate = normalize_rust_event_file(
        path=raw_path,
        store=store,
        last_price_state_by_symbol=price_state_cache,
    )

    assert first.price_ticks_written == 1
    assert duplicate.rows_read == 1
    assert duplicate.price_ticks_written == 0
    assert price_observation_calls == 0
    assert parse_ts_calls == 1
    assert store.insert_price_ticks_calls == 0


def test_duplicate_top_of_book_rows_skip_depth_json_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "state.duckdb"
    store = _CountingCheckpointStore(db_path)
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
    row = _orderbook_row(
        "token-1",
        "2026-06-02T05:33:54Z",
        "2026-06-02T05:33:55Z",
        0.61,
        0.64,
    )
    orderbook_state_cache: dict[tuple[str, str], tuple[object, ...]] = {}
    _write_jsonl(raw_path, row)
    first = normalize_rust_event_file(
        path=raw_path,
        store=store,
        last_orderbook_state_by_token=orderbook_state_cache,
    )
    store.insert_orderbook_snapshots_calls = 0
    _append_jsonl(raw_path, row)
    json_dumps_calls = 0
    parse_ts_calls = 0
    real_json_dumps = json.dumps
    real_parse_ts = rust_event_normalizer._parse_ts

    def counting_json_dumps(*args: Any, **kwargs: Any) -> str:
        nonlocal json_dumps_calls
        json_dumps_calls += 1
        return real_json_dumps(*args, **kwargs)

    def counting_parse_ts(value: object) -> datetime:
        nonlocal parse_ts_calls
        parse_ts_calls += 1
        return real_parse_ts(value)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_event_normalizer.json.dumps",
        counting_json_dumps,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_event_normalizer._parse_ts",
        counting_parse_ts,
    )

    duplicate = normalize_rust_event_file(
        path=raw_path,
        store=store,
        last_orderbook_state_by_token=orderbook_state_cache,
    )

    assert first.orderbooks_written == 1
    assert duplicate.rows_read == 1
    assert duplicate.orderbooks_written == 0
    assert json_dumps_calls == 0
    assert parse_ts_calls == 0
    assert store.insert_orderbook_snapshots_calls == 0


def test_clob_rows_skip_price_tick_parser_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        _orderbook_row(
            "token-1",
            "2026-06-02T05:33:54Z",
            "2026-06-02T05:33:55Z",
            0.61,
            0.64,
        ),
    )
    real_price_tick_row_from_raw = getattr(
        __import__(
            "polymarket_engine.ingestion.rust_event_normalizer",
            fromlist=["_price_tick_row_from_raw"],
        ),
        "_price_tick_row_from_raw",
    )
    price_probe_calls = 0

    def counting_price_tick_row_from_raw(*args: Any, **kwargs: Any) -> Any:
        nonlocal price_probe_calls
        price_probe_calls += 1
        return real_price_tick_row_from_raw(*args, **kwargs)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_event_normalizer._price_tick_row_from_raw",
        counting_price_tick_row_from_raw,
    )

    result = normalize_rust_event_file(path=raw_path, store=store)

    assert result.orderbooks_written == 1
    assert price_probe_calls == 0


def test_normalizer_waits_for_complete_appended_jsonl_line(tmp_path: Path) -> None:
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
        _chainlink_row("BTC/USD", "2026-06-02T05:33:54Z", "2026-06-02T05:33:55Z", 70600.0),
    )
    normalize_rust_event_file(path=raw_path, store=store)

    partial_row = json.dumps(
        _chainlink_row("BTC/USD", "2026-06-02T05:33:56Z", "2026-06-02T05:33:57Z", 70601.0),
        separators=(",", ":"),
    )
    raw_path.write_text(raw_path.read_text(encoding="utf-8") + partial_row, encoding="utf-8")

    partial = normalize_rust_event_file(path=raw_path, store=store)
    raw_path.write_text(raw_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    completed = normalize_rust_event_file(path=raw_path, store=store)

    assert partial.rows_read == 0
    assert completed.rows_read == 1
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.price_ticks").fetchone() == (2,)


def test_normalizer_reads_complete_chunk_once_after_byte_limit(tmp_path: Path) -> None:
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
        _chainlink_row("BTC/USD", "2026-06-02T05:33:54Z", "2026-06-02T05:33:55Z", 70600.0),
        _chainlink_row("BTC/USD", "2026-06-02T05:33:56Z", "2026-06-02T05:33:57Z", 70601.0),
    )
    raw_size = raw_path.stat().st_size
    counted_path = _CountingBinaryPath(raw_path)
    expected_file_id = _file_id(raw_path, start_byte_offset=0, byte_limit=raw_size)

    result = normalize_rust_event_file(path=cast(Path, counted_path), store=store)

    assert result.rows_read == 2
    assert result.file_id == expected_file_id
    assert counted_path.bytes_read <= raw_size + 1


def test_jsonl_iterator_stops_at_initial_byte_limit(tmp_path: Path) -> None:
    raw_path = tmp_path / "events.jsonl"
    first_line = json.dumps({"source_key": "one"}, separators=(",", ":")) + "\n"
    second_line = json.dumps({"source_key": "two"}, separators=(",", ":")) + "\n"
    raw_path.write_text(first_line + second_line, encoding="utf-8")

    rows = tuple(_iter_jsonl(raw_path, byte_limit=len(first_line.encode("utf-8"))))

    assert rows == ({"source_key": "one"},)


def test_jsonl_iterator_loads_raw_bytes_without_text_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = _FakeBinaryPath(b'{"source_key":"one"}\n')
    seen_input_types: list[type[object]] = []
    real_decode = msgspec.json.decode

    def tracking_decode(value: Any, *args: Any, **kwargs: Any) -> Any:
        seen_input_types.append(type(value))
        return real_decode(value, *args, **kwargs)

    monkeypatch.setattr(
        "msgspec.json.decode",
        tracking_decode,
    )

    rows = tuple(_iter_jsonl(cast(Path, raw_path)))

    assert rows == ({"source_key": "one"},)
    assert seen_input_types == [bytes]


def test_jsonl_iterator_skips_stdlib_json_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = _FakeBinaryPath(b'{"source_key":"one"}\n')
    json_loads_calls = 0
    real_json_loads = json.loads

    def tracking_json_loads(value: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal json_loads_calls
        json_loads_calls += 1
        return real_json_loads(value, *args, **kwargs)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_event_normalizer.json.loads",
        tracking_json_loads,
    )

    rows = tuple(_iter_jsonl(cast(Path, raw_path)))

    assert rows == ({"source_key": "one"},)
    assert json_loads_calls == 0


def test_jsonl_iterator_skips_whitespace_only_lines() -> None:
    raw_path = _FakeBinaryPath(b'{"source_key":"one"}\n  \t\r\n{"source_key":"two"}\n')

    rows = tuple(_iter_jsonl(cast(Path, raw_path)))

    assert rows == ({"source_key": "one"}, {"source_key": "two"})


def test_complete_jsonl_byte_limit_does_not_scan_complete_tail() -> None:
    raw_bytes = (b'{"source_key":"one"}\n' * 10_000)
    path = _FakeBinaryPath(raw_bytes)

    limit = _complete_jsonl_byte_limit(cast(Path, path), start_byte_offset=0, file_size=len(raw_bytes))

    assert limit == len(raw_bytes)
    assert path.last_reader is not None
    assert path.last_reader.bytes_read <= 4096


def test_parse_ts_fast_paths_utc_z_without_offset_rewrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_inputs: list[str] = []
    real_datetime = datetime

    class TrackingDatetime:
        @staticmethod
        def fromisoformat(value: str) -> datetime:
            seen_inputs.append(value)
            if value.endswith("+00:00"):
                return real_datetime.fromisoformat(value)
            return real_datetime.fromisoformat(f"{value}+00:00")

    monkeypatch.setattr(rust_event_normalizer, "datetime", TrackingDatetime)

    parsed = _parse_ts("2026-06-02T05:33:54.123456789Z")

    assert parsed == datetime.fromisoformat("2026-06-02T05:33:54.123456+00:00")
    assert seen_inputs == ["2026-06-02T05:33:54.123456"]


def test_optional_probability_float_parses_without_nested_probability_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probability_helper_calls = 0
    real_probability_float = rust_event_normalizer._probability_float

    def counting_probability_float(value: object, field_name: str) -> float:
        nonlocal probability_helper_calls
        probability_helper_calls += 1
        return real_probability_float(value, field_name)

    monkeypatch.setattr(
        rust_event_normalizer,
        "_probability_float",
        counting_probability_float,
    )

    assert _optional_probability_float("0.64", "best_ask") == 0.64
    assert probability_helper_calls == 0


def _write_jsonl(path: Path, *rows: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _append_jsonl(path: Path, *rows: object) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def _chainlink_row(symbol: str, event_ts: str, observed_ts: str, value: float) -> dict[str, object]:
    return {
        "source_key": "polymarket_rtds_chainlink",
        "stream_key": "price_update",
        "symbol": symbol,
        "event_type": "chainlink_price",
        "event_ts": event_ts,
        "observed_ts": observed_ts,
        "payload": {
            "topic": "crypto_prices_chainlink",
            "payload": {
                "symbol": symbol.lower(),
                "timestamp": 1,
                "value": value,
            },
        },
    }


def _orderbook_row(
    token_id: str,
    event_ts: str,
    observed_ts: str,
    best_bid: float,
    best_ask: float,
) -> dict[str, object]:
    return {
        "source_key": "polymarket_clob_market_ws",
        "stream_key": "best_bid_ask",
        "symbol": token_id,
        "event_type": "best_bid_ask",
        "event_ts": event_ts,
        "observed_ts": observed_ts,
        "payload": {
            "contract_id": "0xabc",
            "token_id": token_id,
            "best_bid": str(best_bid),
            "best_ask": str(best_ask),
            "spread": str(round(best_ask - best_bid, 10)),
        },
    }


class _TrackingReader(BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.bytes_read = 0

    def read(self, size: int | None = -1) -> bytes:
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk

    def readline(self, size: int | None = -1) -> bytes:
        chunk = super().readline(size)
        self.bytes_read += len(chunk)
        return chunk


class _FakeBinaryPath:
    def __init__(self, value: bytes) -> None:
        self.value = value
        self.last_reader: _TrackingReader | None = None

    def open(self, mode: str = "r", *args: object, **kwargs: object) -> _TrackingReader:
        if mode != "rb":
            raise AssertionError(f"expected binary read mode, got {mode!r}")
        self.last_reader = _TrackingReader(self.value)
        return self.last_reader


class _CountingBinaryPath:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.bytes_read = 0
        self.readers: list[_CountingReader] = []

    @property
    def parts(self) -> tuple[str, ...]:
        return self.path.parts

    def __str__(self) -> str:
        return str(self.path)

    def stat(self) -> object:
        return self.path.stat()

    def open(self, mode: str = "r", *args: object, **kwargs: object) -> _TrackingReader:
        if mode != "rb":
            raise AssertionError(f"expected binary read mode, got {mode!r}")
        reader = _CountingReader(self.path.read_bytes(), self)
        self.readers.append(reader)
        return reader


class _CountingReader(_TrackingReader):
    def __init__(self, value: bytes, path: _CountingBinaryPath) -> None:
        super().__init__(value)
        self.path = path

    def __iter__(self) -> _CountingReader:
        return self

    def __next__(self) -> bytes:
        line = self.readline()
        if line:
            return line
        raise StopIteration

    def read(self, size: int | None = -1) -> bytes:
        chunk = super().read(size)
        self.path.bytes_read += len(chunk)
        return chunk

    def readline(self, size: int | None = -1) -> bytes:
        chunk = super().readline(size)
        self.path.bytes_read += len(chunk)
        return chunk


class _CountingCheckpointStore(DuckDbIngestStore):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self.raw_file_checkpoint_calls = 0
        self.raw_file_checkpoints_calls = 0
        self.insert_price_ticks_calls = 0
        self.insert_orderbook_snapshots_calls = 0
        self.register_ingest_file_calls = 0

    def raw_file_checkpoint(self, path: Path) -> int | None:
        self.raw_file_checkpoint_calls += 1
        return super().raw_file_checkpoint(path)

    def raw_file_checkpoints(self, paths: Sequence[Path]) -> dict[Path, int]:
        self.raw_file_checkpoints_calls += 1
        return super().raw_file_checkpoints(paths)

    def insert_price_ticks(
        self,
        ticks: Sequence[PriceObservation],
        raw_file_id: str | None = None,
    ) -> None:
        self.insert_price_ticks_calls += 1
        super().insert_price_ticks(ticks, raw_file_id=raw_file_id)

    def insert_orderbook_snapshots(
        self,
        snapshots: Sequence[OrderBookObservation],
        raw_file_id: str | None = None,
    ) -> None:
        self.insert_orderbook_snapshots_calls += 1
        super().insert_orderbook_snapshots(snapshots, raw_file_id=raw_file_id)

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
        self.register_ingest_file_calls += 1
        super().register_ingest_file(
            file_id=file_id,
            source_key=source_key,
            stream_key=stream_key,
            partition_date=partition_date,
            partition_hour=partition_hour,
            path=path,
            sha256=sha256,
            row_count=row_count,
            first_event_ts=first_event_ts,
            last_event_ts=last_event_ts,
        )
