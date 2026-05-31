from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class NormalizedPriceTick:
    source_key: str
    symbol: str
    event_ts: datetime
    price: float
    bid: float | None = None
    ask: float | None = None
    sequence: str | None = None


def parse_binance_trade(message: dict[str, object]) -> NormalizedPriceTick:
    return NormalizedPriceTick(
        source_key="binance_spot_ws",
        symbol=str(message["s"]),
        event_ts=datetime.fromtimestamp(int(str(message["T"])) / 1000, tz=timezone.utc),
        price=float(str(message["p"])),
        sequence=str(message.get("t", "")),
    )


def parse_binance_book_ticker(
    message: dict[str, object],
    observed_ts: datetime | None = None,
) -> NormalizedPriceTick:
    bid = float(str(message["b"]))
    ask = float(str(message["a"]))
    event_time = message.get("E")
    if event_time is None:
        if observed_ts is None:
            raise ValueError("observed_ts is required for Binance bookTicker messages without event time")
        event_ts = observed_ts
    else:
        event_ts = datetime.fromtimestamp(int(str(event_time)) / 1000, tz=timezone.utc)
    return NormalizedPriceTick(
        source_key="binance_spot_ws",
        symbol=str(message["s"]),
        event_ts=event_ts,
        price=(bid + ask) / 2,
        bid=bid,
        ask=ask,
        sequence=str(message.get("u", "")),
    )
