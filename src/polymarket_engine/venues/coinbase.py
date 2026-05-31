from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CoinbaseTick:
    source_key: str
    symbol: str
    event_ts: datetime
    price: float


def parse_coinbase_ticker(event: dict[str, object]) -> CoinbaseTick:
    return CoinbaseTick(
        source_key="coinbase_advanced_ws",
        symbol=str(event["product_id"]),
        event_ts=datetime.fromisoformat(str(event["time"]).replace("Z", "+00:00")),
        price=float(str(event["price"])),
    )
