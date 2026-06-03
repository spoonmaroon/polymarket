import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import pytest

from polymarket_engine.domain.market_state import PriceObservation
from polymarket_engine.monitor import _snapshot_from_status, fetch_monitor_snapshot, render_monitor
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


def test_monitor_snapshot_retries_transient_duckdb_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "monitor.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()

    original_connect = duckdb.connect
    calls = {"locked": 0}

    def flaky_connect(*args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        if kwargs.get("read_only") and calls["locked"] == 0:
            calls["locked"] += 1
            raise duckdb.IOException("Could not set lock on file")
        return original_connect(*args, **kwargs)

    monkeypatch.setattr("polymarket_engine.monitor.duckdb.connect", flaky_connect)

    snapshot = fetch_monitor_snapshot(db_path, limit=4, lock_retry_seconds=0.2)

    assert calls["locked"] == 1
    assert snapshot.generated_at.tzinfo is not None


def test_monitor_snapshot_prefers_atomic_status_file(tmp_path: Path) -> None:
    db_path = tmp_path / "missing.duckdb"
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-31T21:30:00+00:00",
                "prices": [
                    {
                        "source_key": "polymarket_rtds_chainlink",
                        "symbol": "BTC/USD",
                        "observed_ts": "2026-05-31T21:30:00+00:00",
                        "price": 73500.0,
                    }
                ],
                "contracts": [],
                "orderbooks": [],
                "ingest_counts": [],
                "normalized_health": [
                    {
                        "table": "core.price_ticks",
                        "rows": 12,
                        "latest_ts": "2026-05-31T21:30:00+00:00",
                    }
                ],
                "source_freshness": [
                    {
                        "source_key": "polymarket_rtds_chainlink",
                        "symbol": "ETH/USD",
                        "observed_ts": "2026-05-31T21:29:00+00:00",
                        "age_ms": 60000,
                        "stale_after_ms": 5000,
                        "stale": True,
                        "missing": False,
                    }
                ],
                "source_disagreements": [
                    {
                        "asset": "ETH",
                        "primary_source_key": "polymarket_rtds_chainlink",
                        "primary_symbol": "ETH/USD",
                        "primary_price": 1986.8168,
                        "proxy_source_key": "coinbase_advanced_ws",
                        "proxy_symbol": "ETH-USD",
                        "proxy_price": 1983.02,
                        "diff": None,
                        "diff_bps": None,
                        "usable": False,
                        "block_reason": "stale_reference_source",
                    }
                ],
                "orderbook_freshness": [],
            }
        ),
        encoding="utf-8",
    )

    snapshot = fetch_monitor_snapshot(db_path, limit=4, status_path=status_path)

    assert snapshot.prices[("polymarket_rtds_chainlink", "BTC/USD")] == 73500.0
    assert snapshot.normalized_health[0]["table"] == "core.price_ticks"
    assert snapshot.source_freshness[0]["symbol"] == "ETH/USD"
    assert snapshot.source_disagreements[0]["block_reason"] == "stale_reference_source"


def test_monitor_snapshot_reads_rust_state_manager_status(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "schema_version": "rust-live-probe-state-manager-v1",
                "mode": "state-manager",
                "generated_at": "2026-06-02T07:41:52.606221430Z",
                "current": [
                    {
                        "window": {
                            "asset": "BTC",
                            "interval": "5m",
                            "start_ts": "2026-06-02T07:40:00Z",
                            "end_ts": "2026-06-02T07:45:00Z",
                        },
                        "up": {"asset": "BTC", "side": "Up", "token_id": "btc-up"},
                        "down": {"asset": "BTC", "side": "Down", "token_id": "btc-down"},
                    }
                ],
                "next": [],
                "next_next": [],
                "chainlink_prices": [
                    {
                        "source_key": "polymarket_rtds_chainlink",
                        "symbol": "BTC/USD",
                        "event_ts": "2026-06-02T07:41:51Z",
                        "observed_ts": "2026-06-02T07:41:52.176844852Z",
                        "price": "70120.61797938941",
                    },
                    {
                        "source_key": "polymarket_rtds_chainlink",
                        "symbol": "ETH/USD",
                        "event_ts": "2026-06-02T07:41:51Z",
                        "observed_ts": "2026-06-02T07:41:52.079619611Z",
                        "price": "1984.4558728754",
                    },
                ],
                "proxy_prices": [],
                "orderbooks": [
                    {
                        "venue": "polymarket",
                        "source_key": "polymarket_rust_sdk",
                        "market_slug": "btc-updown-5m-1780386000",
                        "contract_id": "btc-updown-5m-1780386000",
                        "token_id": "btc-up",
                        "asset": "BTC",
                        "side": "UP",
                        "event_ts": "2026-06-02T07:41:50Z",
                        "observed_ts": "2026-06-02T07:41:52Z",
                        "best_bid": "0.49",
                        "best_ask": "0.51",
                        "spread": "0.02",
                        "bid_size_top": "10.0",
                        "ask_size_top": "12.0",
                        "bids": [],
                        "asks": [],
                    }
                ],
                "freshness": [
                    {
                        "source_key": "polymarket_rtds_chainlink",
                        "symbol": "BTC/USD",
                        "age_ms": 429,
                        "stale": False,
                    },
                    {
                        "source_key": "polymarket_rtds_chainlink",
                        "symbol": "ETH/USD",
                        "age_ms": 526,
                        "stale": False,
                    },
                    {
                        "source_key": "polymarket_rust_sdk",
                        "symbol": "btc-up",
                        "age_ms": 4271,
                        "stale": False,
                    },
                ],
                "latency_marks": [
                    {"name": "chainlink_observed_age_ms", "elapsed_ms": 526},
                    {"name": "orderbook_observed_age_ms", "elapsed_ms": 4271},
                ],
                "health_flags": [],
                "hot_decision_telemetry": {
                    "states_built": 4890,
                    "states_persist_queued": 4890,
                    "dropped_events": 0,
                    "last_state_age_ms": 3,
                    "last_observed_to_state_us": 105,
                },
                "subscriptions": [],
                "websocket_status": [],
            }
        ),
        encoding="utf-8",
    )

    snapshot = _snapshot_from_status(
        status_path,
        limit=12,
        now=datetime(2026, 6, 2, 7, 41, 52, tzinfo=timezone.utc),
    )
    output = render_monitor(snapshot)

    assert snapshot.prices[("polymarket_rtds_chainlink", "BTC/USD")] == pytest.approx(
        70120.61797938941
    )
    assert snapshot.prices[("polymarket_rtds_chainlink", "ETH/USD")] == pytest.approx(
        1984.4558728754
    )
    assert snapshot.source_freshness[0]["age_ms"] == 429
    assert snapshot.orderbook_freshness[0]["symbol"] == "btc-up"
    assert snapshot.contracts[0]["contract_id"] == "btc-updown-5m-1780386000"
    assert "BTC/USD" in output
    assert "ETH/USD" in output
    assert "70120.6180" in output
    assert "1984.4559" in output
    assert "polymarket_rust_sdk:btc-up" in output
    assert "Hot Decisions" in output
    assert "states_built=4890" in output


def test_monitor_snapshot_shapes_state_manager_books_to_active_current_and_next_rows(
    tmp_path: Path,
) -> None:
    status_path = tmp_path / "status.json"
    current_start = datetime(2026, 6, 3, 22, 0, tzinfo=timezone.utc)
    next_start = datetime(2026, 6, 3, 22, 5, tzinfo=timezone.utc)
    next_next_start = datetime(2026, 6, 3, 22, 10, tzinfo=timezone.utc)
    expired_start = datetime(2026, 6, 3, 21, 55, tzinfo=timezone.utc)
    observed_ts = "2026-06-03T22:04:30Z"
    status_path.write_text(
        json.dumps(
            {
                "schema_version": "rust-live-probe-state-manager-v1",
                "mode": "state-manager",
                "generated_at": observed_ts,
                "current": [
                    _state_manager_contract("BTC", current_start, "btc-current"),
                    _state_manager_contract("ETH", current_start, "eth-current"),
                ],
                "next": [
                    _state_manager_contract("BTC", next_start, "btc-next"),
                    _state_manager_contract("ETH", next_start, "eth-next"),
                ],
                "next_next": [
                    _state_manager_contract("BTC", next_next_start, "btc-next-next"),
                    _state_manager_contract("ETH", next_next_start, "eth-next-next"),
                ],
                "chainlink_prices": [],
                "orderbooks": [
                    _status_orderbook("ETH", "UP", "eth-current-up", observed_ts, bid=0.0, ask=0.75),
                    _status_orderbook("BTC", "DOWN", "btc-next-down", observed_ts, bid=0.49, ask=0.50),
                    _status_orderbook("BTC", "UP", "btc-expired-up", observed_ts, bid=0.91, ask=0.92),
                    _status_orderbook("BTC", "UP", "btc-current-up", observed_ts, bid=0.88, ask=0.89),
                    _status_orderbook("ETH", "DOWN", "eth-next-down", observed_ts, bid=0.24, ask=0.25),
                ],
                "freshness": [],
                "latency_marks": [],
                "health_flags": [],
                "websocket_status": [],
            }
        ),
        encoding="utf-8",
    )
    expired_contract_id = "btc-updown-5m-" + str(int(expired_start.timestamp()))

    snapshot = _snapshot_from_status(
        status_path,
        limit=12,
        now=datetime(2026, 6, 3, 22, 4, 30, tzinfo=timezone.utc),
    )

    assert [
        (row["asset"], row["window"], row["side"])
        for row in snapshot.orderbooks
    ] == [
        ("BTC", "current", "UP"),
        ("BTC", "current", "DOWN"),
        ("BTC", "next", "UP"),
        ("BTC", "next", "DOWN"),
        ("ETH", "current", "UP"),
        ("ETH", "current", "DOWN"),
        ("ETH", "next", "UP"),
        ("ETH", "next", "DOWN"),
    ]
    assert all(row["contract_id"] != expired_contract_id for row in snapshot.orderbooks)
    btc_current_down = next(
        row
        for row in snapshot.orderbooks
        if row["asset"] == "BTC" and row["window"] == "current" and row["side"] == "DOWN"
    )
    eth_current_up = next(
        row
        for row in snapshot.orderbooks
        if row["asset"] == "ETH" and row["window"] == "current" and row["side"] == "UP"
    )
    assert btc_current_down["best_bid"] is None
    assert btc_current_down["best_ask"] is None
    assert btc_current_down["book_state"] == "missing"
    assert eth_current_up["best_bid"] is None
    assert eth_current_up["best_ask"] == pytest.approx(0.75)
    assert eth_current_up["book_state"] == "no_bid"


def test_monitor_snapshot_recomputes_status_freshness_against_wall_time(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-31T21:30:00+00:00",
                "prices": [],
                "contracts": [],
                "orderbooks": [],
                "ingest_counts": [],
                "normalized_health": [],
                "source_freshness": [
                    {
                        "source_key": "polymarket_rtds_chainlink",
                        "symbol": "BTC/USD",
                        "observed_ts": "2026-05-31T21:30:00+00:00",
                        "age_ms": 0,
                        "stale_after_ms": 5000,
                        "stale": False,
                        "missing": False,
                    }
                ],
                "source_disagreements": [
                    {
                        "asset": "BTC",
                        "primary_source_key": "polymarket_rtds_chainlink",
                        "primary_symbol": "BTC/USD",
                        "primary_price": 73500.0,
                        "proxy_source_key": "coinbase_advanced_ws",
                        "proxy_symbol": "BTC-USD",
                        "proxy_price": 73501.0,
                        "diff": 1.0,
                        "diff_bps": 0.14,
                        "usable": True,
                        "block_reason": None,
                    }
                ],
                "orderbook_freshness": [
                    {
                        "asset": "BTC",
                        "side": "UP",
                        "contract_id": "btc-market:UP",
                        "observed_ts": "2026-05-31T21:30:00+00:00",
                        "age_ms": 0,
                        "stale_after_ms": 5000,
                        "stale": False,
                        "missing": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    snapshot = _snapshot_from_status(
        status_path,
        limit=4,
        now=datetime(2026, 5, 31, 21, 30, 10, tzinfo=timezone.utc),
    )

    assert snapshot.source_freshness[0]["stale"] is True
    assert snapshot.source_freshness[0]["age_ms"] == 10_000
    assert snapshot.source_disagreements[0]["usable"] is False
    assert snapshot.source_disagreements[0]["block_reason"] == "stale_reference_source"
    assert snapshot.orderbook_freshness[0]["stale"] is True
    assert snapshot.orderbook_freshness[0]["age_ms"] == 10_000


def _state_manager_contract(
    asset: str,
    start_ts: datetime,
    token_prefix: str,
) -> dict[str, Any]:
    end_ts = start_ts.replace(minute=start_ts.minute + 5)
    return {
        "window": {
            "asset": asset,
            "interval": "5m",
            "start_ts": start_ts.isoformat(),
            "end_ts": end_ts.isoformat(),
        },
        "up": {"asset": asset, "side": "Up", "token_id": f"{token_prefix}-up"},
        "down": {"asset": asset, "side": "Down", "token_id": f"{token_prefix}-down"},
    }


def _status_orderbook(
    asset: str,
    side: str,
    token_id: str,
    observed_ts: str,
    *,
    bid: float,
    ask: float,
) -> dict[str, Any]:
    return {
        "venue": "polymarket",
        "source_key": "polymarket_rust_sdk",
        "market_slug": f"{asset.lower()}-updown-5m-1780524000",
        "contract_id": f"{asset.lower()}-contract-{token_id}",
        "token_id": token_id,
        "asset": asset,
        "side": side,
        "event_ts": observed_ts,
        "observed_ts": observed_ts,
        "best_bid": bid,
        "best_ask": ask,
        "spread": ask - bid,
        "bid_size_top": 100.0,
        "ask_size_top": 120.0,
        "bids": [{"price": bid, "size": 100.0}],
        "asks": [{"price": ask, "size": 120.0}],
    }


def test_monitor_snapshot_rejects_naive_status_timestamp(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-31T21:30:00",
                "prices": [],
                "contracts": [],
                "orderbooks": [],
                "ingest_counts": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="status timestamps must be timezone-aware"):
        _snapshot_from_status(status_path, limit=4)


def test_render_monitor_outputs_normalized_health_and_stale_sources() -> None:
    snapshot = fetch_monitor_snapshot(
        Path("missing.duckdb"),
        limit=4,
        status_path=None,
    )
    enriched = snapshot.__class__(
        generated_at=datetime(2026, 5, 31, 21, 30, tzinfo=timezone.utc),
        prices=snapshot.prices,
        price_rows=snapshot.price_rows,
        orderbooks=snapshot.orderbooks,
        contracts=snapshot.contracts,
        ingest_counts=snapshot.ingest_counts,
        normalized_health=(
            {"table": "core.price_ticks", "rows": 12, "latest_ts": "2026-05-31T21:30:00+00:00"},
        ),
        source_freshness=(
            {
                "source_key": "polymarket_rtds_chainlink",
                "symbol": "ETH/USD",
                "observed_ts": "2026-05-31T21:29:00+00:00",
                "age_ms": 60000,
                "stale_after_ms": 5000,
                "stale": True,
                "missing": False,
            },
        ),
        source_disagreements=(
            {
                "asset": "ETH",
                "primary_source_key": "polymarket_rtds_chainlink",
                "primary_symbol": "ETH/USD",
                "primary_price": 1986.8168,
                "proxy_source_key": "coinbase_advanced_ws",
                "proxy_symbol": "ETH-USD",
                "proxy_price": 1983.02,
                "diff": None,
                "diff_bps": None,
                "usable": False,
                "block_reason": "stale_reference_source",
            },
        ),
        orderbook_freshness=(),
        source_errors={"polymarket_clob:111": "ConnectTimeout"},
    )

    output = render_monitor(enriched)

    assert "Normalized Health" in output
    assert "Source Freshness" in output
    assert "Source Disagreement" in output
    assert "Source Errors" in output
    assert "polymarket_clob:111" in output
    assert "STALE" in output
    assert "blocked=stale_reference_source" in output
