from datetime import datetime, timezone

from polymarket_engine.ingestion.polymarket_rtds import (
    build_rtds_subscriptions,
    rtds_heartbeat_message,
    rtds_price_events,
)


def test_build_rtds_subscriptions_uses_chainlink_and_binance_proxy_topics() -> None:
    subscriptions = build_rtds_subscriptions(("BTC", "ETH"))

    assert subscriptions["action"] == "subscribe"
    assert subscriptions["subscriptions"] == [
        {"topic": "crypto_prices_chainlink", "type": "*", "filters": ""},
        {"topic": "crypto_prices", "type": "update", "filters": '{"symbol":"btcusdt"}'},
        {"topic": "crypto_prices", "type": "update", "filters": '{"symbol":"ethusdt"}'},
    ]


def test_rtds_price_events_parse_chainlink_payload() -> None:
    observed = datetime(2026, 5, 31, 21, 0, 1, tzinfo=timezone.utc)
    message = {
        "topic": "crypto_prices_chainlink",
        "type": "update",
        "timestamp": 1780261201000,
        "payload": {
            "symbol": "btc/usd",
            "value": "104000.12",
            "timestamp": 1780261200500,
        },
    }

    events = rtds_price_events(message, observed)

    assert len(events) == 1
    assert events[0].source_key == "polymarket_rtds_chainlink"
    assert events[0].symbol == "BTC/USD"
    assert events[0].payload["value"] == "104000.12"
    assert events[0].lag_ms == 500


def test_rtds_price_events_parse_chainlink_snapshot_payload() -> None:
    observed = datetime(2026, 5, 31, 21, 0, 3, tzinfo=timezone.utc)
    message = {
        "topic": "crypto_prices",
        "type": "subscribe",
        "timestamp": 1780261203000,
        "payload": {
            "symbol": "btc/usd",
            "data": [
                {"timestamp": 1780261201000, "value": 104000.12},
                {"timestamp": 1780261202000, "value": 104001.34},
            ],
        },
    }

    events = rtds_price_events(message, observed)

    assert len(events) == 2
    assert {event.source_key for event in events} == {"polymarket_rtds_chainlink"}
    assert [event.symbol for event in events] == ["BTC/USD", "BTC/USD"]
    assert [event.payload["value"] for event in events] == [104000.12, 104001.34]


def test_rtds_price_events_parse_binance_proxy_payload() -> None:
    observed = datetime(2026, 5, 31, 21, 0, 1, tzinfo=timezone.utc)
    message = {
        "topic": "crypto_prices",
        "type": "update",
        "timestamp": 1780261201000,
        "payload": {
            "full_accuracy_value": "104001.12000000",
            "symbol": "btcusdt",
            "timestamp": 1780261200500,
            "value": 104001.12,
        },
    }

    events = rtds_price_events(message, observed, assets=("BTC", "ETH"))

    assert len(events) == 1
    assert events[0].source_key == "polymarket_rtds_crypto"
    assert events[0].symbol == "BTC/USDT"
    assert events[0].payload["value"] == 104001.12


def test_rtds_price_events_filter_to_configured_assets_after_all_symbol_subscription() -> None:
    observed = datetime(2026, 5, 31, 21, 0, 1, tzinfo=timezone.utc)
    eth_message = {
        "topic": "crypto_prices_chainlink",
        "type": "update",
        "timestamp": 1780261201000,
        "payload": {
            "symbol": "eth/usd",
            "value": "1982.79",
            "timestamp": 1780261200000,
        },
    }
    sol_message = {
        "topic": "crypto_prices_chainlink",
        "type": "update",
        "timestamp": 1780261201000,
        "payload": {
            "symbol": "sol/usd",
            "value": "81.27",
            "timestamp": 1780261200000,
        },
    }

    eth_events = rtds_price_events(eth_message, observed, assets=("BTC", "ETH"))
    sol_events = rtds_price_events(sol_message, observed, assets=("BTC", "ETH"))

    assert len(eth_events) == 1
    assert eth_events[0].symbol == "ETH/USD"
    assert sol_events == ()


def test_rtds_price_events_ignore_empty_ack() -> None:
    observed = datetime(2026, 5, 31, 21, 0, 3, tzinfo=timezone.utc)

    assert rtds_price_events({}, observed) == ()


def test_rtds_heartbeat_message_is_documented_ping() -> None:
    assert rtds_heartbeat_message() == "PING"
