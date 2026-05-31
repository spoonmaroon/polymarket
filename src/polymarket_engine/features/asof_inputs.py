from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class AsOfStateInput:
    contract_id: str
    asof_ts: datetime
    asset: str
    side: str
    threshold: float
    seconds_left: float
    settlement_price: float
    settlement_source_key: str
    binance_price: float | None
    coinbase_price: float | None
    source_disagreement_bps: float | None
    best_bid: float | None
    best_ask: float | None
    executable_price: float | None
    spread: float | None
    quote_age_ms: float | None


def calculate_source_disagreement_bps(
    primary_price: float,
    proxy_prices: list[float],
) -> float | None:
    if not proxy_prices:
        return None
    return max(abs(proxy - primary_price) / primary_price * 10_000 for proxy in proxy_prices)


def ensure_asof(timestamp: datetime, asof_ts: datetime, field_name: str) -> None:
    if timestamp > asof_ts:
        raise ValueError(f"{field_name} timestamp is after asof_ts")
