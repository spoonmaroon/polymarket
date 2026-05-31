from datetime import datetime, timezone

from polymarket_engine.ingestion.polymarket_rtds import (
    build_rtds_subscriptions,
    rtds_heartbeat_message,
    rtds_price_events,
)


def test_build_rtds_subscriptions_includes_chainlink_and_crypto_topics() -> None:
    subscriptions = build_rtds_subscriptions(("BTC", "ETH"))

    assert subscriptions["action"] == "subscribe"
    assert {item["topic"] for item in subscriptions["subscriptions"]} == {"crypto_prices_chainlink"}
    assert [item["filters"] for item in subscriptions["subscriptions"]] == [
        '{"symbol":"btc/usd"}',
        '{"symbol":"eth/usd"}',
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


def test_rtds_price_events_ignore_empty_ack() -> None:
    observed = datetime(2026, 5, 31, 21, 0, 3, tzinfo=timezone.utc)

    assert rtds_price_events({}, observed) == ()


def test_rtds_heartbeat_message_is_documented_ping() -> None:
    assert rtds_heartbeat_message() == "PING"
