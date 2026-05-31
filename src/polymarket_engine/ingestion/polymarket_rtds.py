from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from polymarket_engine.ingestion.collector_events import CollectorEvent


def build_rtds_subscriptions(assets: tuple[str, ...]) -> dict[str, object]:
    chainlink_filters = [
        {"topic": "crypto_prices_chainlink", "type": "*", "filters": f'{{"symbol":"{asset.lower()}/usd"}}'}
        for asset in assets
    ]
    crypto_symbols = ",".join(f"{asset.lower()}usdt" for asset in assets)
    return {
        "action": "subscribe",
        "subscriptions": [
            *chainlink_filters,
            {"topic": "crypto_prices", "type": "update", "filters": crypto_symbols},
        ],
    }


def _source_key(topic: str) -> str:
    if topic == "crypto_prices_chainlink":
        return "polymarket_rtds_chainlink"
    return "polymarket_rtds_crypto"


def _symbol(raw_symbol: str) -> str:
    normalized = raw_symbol.upper()
    if "/" in normalized:
        return normalized
    if normalized.endswith("USDT"):
        return f"{normalized[:-4]}/USDT"
    return normalized


def rtds_price_events(
    message: dict[str, Any],
    observed_ts: datetime,
) -> tuple[CollectorEvent, ...]:
    topic = str(message.get("topic", ""))
    if topic not in {"crypto_prices_chainlink", "crypto_prices"}:
        return ()
    payload = message.get("payload", {})
    if not isinstance(payload, dict) or "symbol" not in payload:
        return ()
    source_timestamp = int(str(payload.get("timestamp", message.get("timestamp"))))
    event_ts = datetime.fromtimestamp(source_timestamp / 1000, tz=timezone.utc)
    return (
        CollectorEvent(
            source_key=_source_key(topic),
            stream_key="price_update",
            symbol=_symbol(str(payload["symbol"])),
            event_ts=event_ts,
            observed_ts=observed_ts,
            payload=dict(payload),
        ),
    )
