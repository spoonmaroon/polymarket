from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class CoinbaseTick:
    source_key: str
    symbol: str
    event_ts: datetime
    price: float
    bid: float | None = None
    ask: float | None = None


def parse_coinbase_ticker(
    event: dict[str, object],
    event_ts: datetime | None = None,
) -> CoinbaseTick:
    if event_ts is None:
        event_ts = datetime.fromisoformat(str(event["time"]).replace("Z", "+00:00"))
    return CoinbaseTick(
        source_key="coinbase_advanced_ws",
        symbol=str(event["product_id"]),
        event_ts=event_ts,
        price=float(str(event["price"])),
        bid=_optional_float(event.get("best_bid")),
        ask=_optional_float(event.get("best_ask")),
    )


def parse_coinbase_ticker_message(message: dict[str, Any]) -> tuple[CoinbaseTick, ...]:
    if message.get("channel") != "ticker":
        return ()
    event_ts = datetime.fromisoformat(str(message["timestamp"]).replace("Z", "+00:00"))
    ticks: list[CoinbaseTick] = []
    for event in message.get("events", []):
        tickers = event.get("tickers", []) if isinstance(event, dict) else []
        for ticker in tickers:
            if isinstance(ticker, dict):
                ticks.append(parse_coinbase_ticker(ticker, event_ts=event_ts))
    return tuple(ticks)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(str(value))
