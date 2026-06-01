from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

import polymarket_engine.ingestion.live_collector as live_collector
from polymarket_engine.ingestion.live_collector import (
    LiveCollectorConfig,
    _is_rtds_socket_idle,
    _merge_status_from_markets,
    _orderbook_freshness_rows,
    _orderbook_observation_from_event,
    _price_freshness_rows,
    _prune_expired_contract_state,
    _require_discovered_markets,
    _rtds_activity_timestamp,
    _should_record_sampled_symbol,
    _source_disagreement_rows,
    register_market_rules,
)
from polymarket_engine.ingestion.collector_events import CollectorEvent


BTC_DESCRIPTION = """This market will resolve to "Up" if the Bitcoin price at the end of the time range specified in the title is greater than or equal to the price at the beginning of that range. Otherwise, it will resolve to "Down".
The resolution source for this market is information from Chainlink, specifically the BTC/USD data stream available at https://data.chain.link/streams/btc-usd.
Please note that this market is about the price according to Chainlink data stream BTC/USD, not according to other sources or spot exchanges."""


def test_live_collector_does_not_expose_fake_collection_runtime() -> None:
    assert not hasattr(live_collector, "run_fake_collection")


@pytest.mark.anyio
async def test_live_collector_runtime_is_retired(tmp_path: Path) -> None:
    config = LiveCollectorConfig(
        assets=("BTC",),
        duration_seconds=1,
        raw_root=tmp_path / "raw",
        duckdb_path=tmp_path / "collector.duckdb",
    )

    try:
        await live_collector.run_live_collection(config)
    except RuntimeError as exc:
        assert "Python live collection is retired" in str(exc)
    else:  # pragma: no cover - defensive assertion branch
        raise AssertionError("retired Python collector started")


def test_live_collector_config_rejects_invalid_market_fetch_timeout(tmp_path: Path) -> None:
    try:
        LiveCollectorConfig(
            assets=("BTC",),
            duration_seconds=1,
            raw_root=tmp_path / "raw",
            duckdb_path=tmp_path / "collector.duckdb",
            market_fetch_timeout_seconds=0,
        )
    except ValueError as exc:
        assert "market_fetch_timeout_seconds" in str(exc)
    else:  # pragma: no cover - defensive assertion branch
        raise AssertionError("invalid market_fetch_timeout_seconds was accepted")


def test_live_collector_rejects_empty_market_discovery_before_state_update() -> None:
    try:
        _require_discovered_markets(())
    except ValueError as exc:
        assert "no Polymarket markets returned" in str(exc)
    else:  # pragma: no cover - defensive assertion branch
        raise AssertionError("empty market discovery was accepted")


def test_live_collector_default_freshness_windows_tolerate_quiet_rtds_ticks(tmp_path: Path) -> None:
    config = LiveCollectorConfig(
        assets=("BTC", "ETH"),
        duration_seconds=1,
        raw_root=tmp_path / "raw",
        duckdb_path=tmp_path / "collector.duckdb",
    )

    assert config.rtds_stale_after_ms == 60_000
    assert config.coinbase_stale_after_ms == 30_000
    assert config.orderbook_stale_after_ms == 30_000


def test_register_market_rules_stores_accepted_contract_rule(tmp_path: Path) -> None:
    config = LiveCollectorConfig(
        assets=("BTC",),
        duration_seconds=1,
        raw_root=tmp_path / "raw",
        duckdb_path=tmp_path / "collector.duckdb",
        max_batch_size=2,
    )
    market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-updown-5m-1780264500",
        "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": BTC_DESCRIPTION,
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    source_errors = register_market_rules(config.duckdb_path, (market,))

    assert source_errors == {}
    with duckdb.connect(str(config.duckdb_path), read_only=True) as conn:
        row = conn.execute(
            "select asset, threshold_type, comparison_operator_up, comparison_operator_down, "
            "settlement_symbol, accepted from core.contract_rules where slug = ?",
            ["btc-updown-5m-1780264500"],
        ).fetchone()

    assert row == ("BTC", "start_price", ">=", "<", "BTC/USD", True)


def test_register_market_rules_also_writes_side_level_contracts(tmp_path: Path) -> None:
    db_path = tmp_path / "contracts.duckdb"
    markets = [
        {
            "id": "2397858",
            "conditionId": "0xabc",
            "slug": "btc-updown-5m-1780264500",
            "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
            "description": BTC_DESCRIPTION,
            "eventStartTime": "2026-05-31T21:55:00Z",
            "endDate": "2026-05-31T22:00:00Z",
            "resolutionSource": "https://data.chain.link/streams/btc-usd",
            "outcomes": '["Up", "Down"]',
            "clobTokenIds": '["111", "222"]',
        }
    ]

    errors = register_market_rules(db_path, tuple(markets))

    assert errors == {}
    with duckdb.connect(str(db_path), read_only=True) as conn:
        rows = conn.sql(
            "select contract_id, asset, side, token_id from core.contracts order by side"
        ).fetchall()

    assert rows == [
        ("2397858:DOWN", "BTC", "DOWN", "222"),
        ("2397858:UP", "BTC", "UP", "111"),
    ]


def test_partial_market_discovery_merges_without_deleting_existing_contracts() -> None:
    existing_eth_contract: dict[str, object] = {
        "contract_id": "eth-market:UP",
        "asset": "ETH",
        "side": "UP",
        "token_id": "333",
        "threshold_type": "start_price",
        "settlement_symbol": "ETH/USD",
        "start_ts": "2026-05-31T21:55:00+00:00",
        "expiry_ts": "2026-05-31T22:00:00+00:00",
    }
    latest_contracts: dict[str, dict[str, object]] = {
        "eth-market:UP": existing_eth_contract,
    }
    latest_orderbooks: dict[str, dict[str, object]] = {"333": {"token_id": "333"}}
    latest_orderbooks_by_source: dict[str, dict[str, object]] = {
        "polymarket_clob:333": {"token_id": "333"}
    }
    btc_market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-updown-5m-1780264500",
        "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": BTC_DESCRIPTION,
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    accepted_tokens = _merge_status_from_markets(
        markets=(btc_market,),
        latest_contracts=latest_contracts,
        latest_orderbooks=latest_orderbooks,
        latest_orderbooks_by_source=latest_orderbooks_by_source,
    )

    assert "eth-market:UP" in latest_contracts
    assert latest_contracts["eth-market:UP"] == existing_eth_contract
    assert "2397858:UP" in latest_contracts
    assert "2397858:DOWN" in latest_contracts
    assert latest_orderbooks == {"333": {"token_id": "333"}}
    assert latest_orderbooks_by_source == {"polymarket_clob:333": {"token_id": "333"}}
    assert {token.token_id for token in accepted_tokens} == {"111", "222"}


def test_status_source_disagreement_blocks_stale_chainlink_reference() -> None:
    generated_at = datetime(2026, 6, 1, 11, 20, tzinfo=timezone.utc)
    latest_prices = {
        "coinbase_advanced_ws:ETH-USD": {
            "source_key": "coinbase_advanced_ws",
            "symbol": "ETH-USD",
            "observed_ts": "2026-06-01T11:19:59+00:00",
            "price": 1983.02,
        },
        "polymarket_rtds_chainlink:ETH/USD": {
            "source_key": "polymarket_rtds_chainlink",
            "symbol": "ETH/USD",
            "observed_ts": "2026-06-01T09:48:01+00:00",
            "price": 1986.8168,
        },
    }
    freshness = _price_freshness_rows(
        latest_prices=latest_prices,
        assets=("ETH",),
        generated_at=generated_at,
        coinbase_stale_after_ms=2_000,
        rtds_stale_after_ms=5_000,
    )

    disagreements = _source_disagreement_rows(
        latest_prices=latest_prices,
        source_freshness=freshness,
        assets=("ETH",),
    )

    assert disagreements == (
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
    )


def test_status_source_disagreement_reports_fresh_basis_in_bps() -> None:
    generated_at = datetime(2026, 6, 1, 11, 20, tzinfo=timezone.utc)
    latest_prices = {
        "coinbase_advanced_ws:BTC-USD": {
            "source_key": "coinbase_advanced_ws",
            "symbol": "BTC-USD",
            "observed_ts": "2026-06-01T11:19:59+00:00",
            "price": 72_611.01,
        },
        "polymarket_rtds_chainlink:BTC/USD": {
            "source_key": "polymarket_rtds_chainlink",
            "symbol": "BTC/USD",
            "observed_ts": "2026-06-01T11:19:58+00:00",
            "price": 72_623.5125,
        },
    }
    freshness = _price_freshness_rows(
        latest_prices=latest_prices,
        assets=("BTC",),
        generated_at=generated_at,
        coinbase_stale_after_ms=2_000,
        rtds_stale_after_ms=5_000,
    )

    row = _source_disagreement_rows(
        latest_prices=latest_prices,
        source_freshness=freshness,
        assets=("BTC",),
    )[0]

    assert row["usable"] is True
    assert row["block_reason"] is None
    assert row["diff_bps"] is not None


def test_price_freshness_marks_coinbase_proxy_optional() -> None:
    generated_at = datetime(2026, 6, 1, 11, 20, tzinfo=timezone.utc)
    latest_prices = {
        "polymarket_rtds_chainlink:BTC/USD": {
            "source_key": "polymarket_rtds_chainlink",
            "symbol": "BTC/USD",
            "observed_ts": "2026-06-01T11:19:59+00:00",
            "price": 72_623.5125,
        },
    }

    freshness = _price_freshness_rows(
        latest_prices=latest_prices,
        assets=("BTC",),
        generated_at=generated_at,
        coinbase_stale_after_ms=2_000,
        rtds_stale_after_ms=5_000,
    )

    coinbase_row = next(row for row in freshness if row["source_key"] == "coinbase_advanced_ws")
    chainlink_row = next(
        row for row in freshness if row["source_key"] == "polymarket_rtds_chainlink"
    )
    assert coinbase_row["required"] is False
    assert chainlink_row.get("required", True) is True


def test_status_source_disagreement_includes_optional_rtds_crypto_proxy() -> None:
    generated_at = datetime(2026, 6, 1, 11, 20, tzinfo=timezone.utc)
    latest_prices = {
        "coinbase_advanced_ws:BTC-USD": {
            "source_key": "coinbase_advanced_ws",
            "symbol": "BTC-USD",
            "observed_ts": "2026-06-01T11:19:59+00:00",
            "price": 72_611.01,
        },
        "polymarket_rtds_crypto:BTC/USDT": {
            "source_key": "polymarket_rtds_crypto",
            "symbol": "BTC/USDT",
            "observed_ts": "2026-06-01T11:19:59+00:00",
            "price": 72_612.00,
        },
        "polymarket_rtds_chainlink:BTC/USD": {
            "source_key": "polymarket_rtds_chainlink",
            "symbol": "BTC/USD",
            "observed_ts": "2026-06-01T11:19:58+00:00",
            "price": 72_623.5125,
        },
    }
    freshness = _price_freshness_rows(
        latest_prices=latest_prices,
        assets=("BTC",),
        generated_at=generated_at,
        coinbase_stale_after_ms=2_000,
        rtds_stale_after_ms=5_000,
    )

    rows = _source_disagreement_rows(
        latest_prices=latest_prices,
        source_freshness=freshness,
        assets=("BTC",),
    )

    assert {row["proxy_source_key"] for row in rows} == {
        "coinbase_advanced_ws",
        "polymarket_rtds_crypto",
    }
    assert all(row["usable"] is True for row in rows)


def test_market_ws_top_of_book_normalizes_to_orderbook_observation() -> None:
    observed = datetime(2026, 6, 1, 11, 20, tzinfo=timezone.utc)
    event = CollectorEvent(
        source_key="polymarket_market_ws",
        stream_key="top_of_book",
        symbol="btc-updown-5m-1780301700:UP",
        event_ts=observed,
        observed_ts=observed,
        payload={
            "contract_id": "0xabc",
            "token_id": "111",
            "best_bid": 0.49,
            "best_ask": 0.50,
            "bid_size_top": None,
            "ask_size_top": None,
            "spread": 0.01,
            "depth_json": '{"source":"best_bid_ask"}',
        },
    )

    observation = _orderbook_observation_from_event(event)

    assert observation is not None
    assert observation.venue == "polymarket"
    assert observation.contract_id == "0xabc"
    assert observation.token_id == "111"
    assert observation.best_bid == 0.49
    assert observation.best_ask == 0.50


def test_orderbook_freshness_accepts_rest_backup_when_market_ws_is_quiet() -> None:
    generated_at = datetime(2026, 6, 1, 11, 20, tzinfo=timezone.utc)
    latest_contracts: dict[str, dict[str, object]] = {
        "btc:UP": {
            "contract_id": "btc:UP",
            "asset": "BTC",
            "side": "UP",
            "token_id": "111",
        }
    }
    latest_by_source: dict[str, dict[str, object]] = {
        "polymarket_clob:111": {
            "source_key": "polymarket_clob",
            "token_id": "111",
            "observed_ts": "2026-06-01T11:19:59+00:00",
        }
    }

    rows = _orderbook_freshness_rows(
        latest_contracts=latest_contracts,
        latest_orderbooks_by_source=latest_by_source,
        generated_at=generated_at,
        stale_after_ms=5_000,
        acceptable_source_keys=("polymarket_market_ws", "polymarket_clob"),
    )

    assert rows == (
        {
            "source_key": "polymarket_clob",
            "symbol": "111",
            "observed_ts": "2026-06-01T11:19:59+00:00",
            "age_ms": 1000,
            "stale_after_ms": 5_000,
            "stale": False,
            "missing": False,
            "contract_id": "btc:UP",
            "token_id": "111",
            "asset": "BTC",
            "side": "UP",
        },
    )


def test_orderbook_freshness_reports_missing_when_no_source_has_book() -> None:
    generated_at = datetime(2026, 6, 1, 11, 20, tzinfo=timezone.utc)
    latest_contracts: dict[str, dict[str, object]] = {
        "btc:UP": {
            "contract_id": "btc:UP",
            "asset": "BTC",
            "side": "UP",
            "token_id": "111",
        }
    }

    rows = _orderbook_freshness_rows(
        latest_contracts=latest_contracts,
        latest_orderbooks_by_source={},
        generated_at=generated_at,
        stale_after_ms=5_000,
        acceptable_source_keys=("polymarket_market_ws", "polymarket_clob"),
    )

    assert rows[0]["source_key"] == "polymarket_market_ws|polymarket_clob"
    assert rows[0]["missing"] is True


def test_prune_expired_contract_state_removes_dead_tokens() -> None:
    now = datetime(2026, 6, 1, 13, 40, tzinfo=timezone.utc)
    latest_contracts: dict[str, dict[str, object]] = {
        "old:UP": {
            "contract_id": "old:UP",
            "expiry_ts": "2026-06-01T13:35:00+00:00",
            "token_id": "old-token",
        },
        "live:UP": {
            "contract_id": "live:UP",
            "expiry_ts": "2026-06-01T13:45:00+00:00",
            "token_id": "live-token",
        },
    }
    market_tokens = {
        "old-token": object(),
        "live-token": object(),
    }
    latest_orderbooks: dict[str, dict[str, object]] = {
        "old-token": {"token_id": "old-token"},
        "live-token": {"token_id": "live-token"},
    }
    latest_orderbooks_by_source: dict[str, dict[str, object]] = {
        "polymarket_clob:old-token": {"token_id": "old-token"},
        "polymarket_clob:live-token": {"token_id": "live-token"},
    }
    source_errors = {
        "polymarket_clob:old-token": "ConnectTimeout: ",
        "polymarket_clob:live-token": "ConnectTimeout: ",
        "polymarket_markets": "TimeoutError: ",
    }

    _prune_expired_contract_state(
        now=now,
        latest_contracts=latest_contracts,
        market_tokens=market_tokens,
        latest_orderbooks=latest_orderbooks,
        latest_orderbooks_by_source=latest_orderbooks_by_source,
        source_errors=source_errors,
    )

    assert set(latest_contracts) == {"live:UP"}
    assert set(market_tokens) == {"live-token"}
    assert set(latest_orderbooks) == {"live-token"}
    assert set(latest_orderbooks_by_source) == {"polymarket_clob:live-token"}
    assert source_errors == {
        "polymarket_clob:live-token": "ConnectTimeout: ",
        "polymarket_markets": "TimeoutError: ",
    }


def test_rtds_idle_socket_reconnect_threshold() -> None:
    assert _is_rtds_socket_idle(
        last_message_monotonic=10.0,
        now_monotonic=25.1,
        idle_reconnect_seconds=15.0,
    )
    assert not _is_rtds_socket_idle(
        last_message_monotonic=10.0,
        now_monotonic=24.9,
        idle_reconnect_seconds=15.0,
    )


def test_rtds_activity_timestamp_only_resets_on_chainlink_price_events() -> None:
    assert _rtds_activity_timestamp(
        previous_monotonic=10.0,
        chainlink_event_count=0,
        now_monotonic=20.0,
    ) == 10.0
    assert _rtds_activity_timestamp(
        previous_monotonic=10.0,
        chainlink_event_count=2,
        now_monotonic=20.0,
    ) == 20.0


def test_should_record_sampled_symbol_limits_proxy_write_rate() -> None:
    observed = datetime(2026, 6, 1, 14, 20, tzinfo=timezone.utc)
    last_seen: dict[str, datetime] = {}

    assert _should_record_sampled_symbol(
        last_seen=last_seen,
        symbol="BTC-USD",
        observed_ts=observed,
        min_interval_seconds=1.0,
    )
    assert not _should_record_sampled_symbol(
        last_seen=last_seen,
        symbol="BTC-USD",
        observed_ts=observed,
        min_interval_seconds=1.0,
    )
    assert _should_record_sampled_symbol(
        last_seen=last_seen,
        symbol="BTC-USD",
        observed_ts=observed.replace(second=21),
        min_interval_seconds=1.0,
    )
