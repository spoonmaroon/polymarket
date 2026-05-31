from datetime import datetime, timezone

from polymarket_engine.ingestion.coinbase_ws import (
    build_coinbase_ticker_subscription,
    coinbase_ticker_events,
)


def test_build_coinbase_ticker_subscription() -> None:
    assert build_coinbase_ticker_subscription(("BTC-USD", "ETH-USD")) == {
        "type": "subscribe",
        "product_ids": ["BTC-USD", "ETH-USD"],
        "channel": "ticker",
    }


def test_coinbase_ticker_events_parse_real_channel_shape() -> None:
    observed = datetime(2026, 5, 31, 21, 0, 1, tzinfo=timezone.utc)
    message = {
        "channel": "ticker",
        "timestamp": "2026-05-31T21:00:00.500000Z",
        "events": [
            {
                "type": "update",
                "tickers": [
                    {"product_id": "BTC-USD", "price": "104000.10"},
                    {"product_id": "ETH-USD", "price": "3900.20"},
                ],
            }
        ],
    }

    events = coinbase_ticker_events(message, observed)

    assert [event.symbol for event in events] == ["BTC-USD", "ETH-USD"]
    assert events[0].source_key == "coinbase_advanced_ws"
    assert events[0].stream_key == "ticker"
    assert events[0].payload["price"] == "104000.10"
    assert events[0].lag_ms == 500
