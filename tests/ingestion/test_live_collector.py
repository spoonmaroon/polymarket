from datetime import datetime, timezone
from pathlib import Path

import duckdb

import polymarket_engine.ingestion.live_collector as live_collector
from polymarket_engine.ingestion.live_collector import (
    LiveCollectorConfig,
    _price_freshness_rows,
    _source_disagreement_rows,
    register_market_rules,
)


BTC_DESCRIPTION = """This market will resolve to "Up" if the Bitcoin price at the end of the time range specified in the title is greater than or equal to the price at the beginning of that range. Otherwise, it will resolve to "Down".
The resolution source for this market is information from Chainlink, specifically the BTC/USD data stream available at https://data.chain.link/streams/btc-usd.
Please note that this market is about the price according to Chainlink data stream BTC/USD, not according to other sources or spot exchanges."""


def test_live_collector_does_not_expose_fake_collection_runtime() -> None:
    assert not hasattr(live_collector, "run_fake_collection")


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
