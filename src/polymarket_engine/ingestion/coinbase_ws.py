from __future__ import annotations

from datetime import datetime
from typing import Any

from polymarket_engine.ingestion.collector_events import CollectorEvent
from polymarket_engine.venues.coinbase import parse_coinbase_ticker


def build_coinbase_ticker_subscription(product_ids: tuple[str, ...]) -> dict[str, object]:
    return {
        "type": "subscribe",
        "product_ids": list(product_ids),
        "channel": "ticker",
    }


def coinbase_ticker_events(
    message: dict[str, Any],
    observed_ts: datetime,
) -> tuple[CollectorEvent, ...]:
    if message.get("channel") != "ticker":
        return ()
    event_ts = datetime.fromisoformat(str(message["timestamp"]).replace("Z", "+00:00"))
    events: list[CollectorEvent] = []
    for event in message.get("events", []):
        tickers = event.get("tickers", []) if isinstance(event, dict) else []
        for ticker in tickers:
            tick = parse_coinbase_ticker(ticker, event_ts=event_ts)
            events.append(
                CollectorEvent(
                    source_key="coinbase_advanced_ws",
                    stream_key="ticker",
                    symbol=tick.symbol,
                    event_ts=tick.event_ts,
                    observed_ts=observed_ts,
                    payload=dict(ticker),
                )
            )
    return tuple(events)
