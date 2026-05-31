from datetime import datetime, timezone

from polymarket_engine.ingestion.polymarket_rtds import (
    build_rtds_subscriptions,
    rtds_price_events,
)


def test_build_rtds_subscriptions_includes_chainlink_and_crypto_topics() -> None:
    subscriptions = build_rtds_subscriptions(("BTC", "ETH"))

    assert subscriptions["action"] == "subscribe"
    assert {
        item["topic"] for item in subscriptions["subscriptions"]
    } == {"crypto_prices_chainlink", "crypto_prices"}


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
