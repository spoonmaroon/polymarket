from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class PolymarketContract:
    contract_id: str
    asset: str
    side: str
    threshold: float
    expiry_ts: datetime
    settlement_source: str
    token_id: str
    rule_text: str
    rule_hash: str


@dataclass(frozen=True)
class PolymarketOrderBookSnapshot:
    venue: str
    contract_id: str
    token_id: str
    event_ts: datetime
    best_bid: float | None
    best_ask: float | None
    bid_size_top: float | None
    ask_size_top: float | None
    spread: float | None
    depth_json: str


@dataclass(frozen=True)
class PolymarketPriceChange:
    contract_id: str
    token_id: str
    event_ts: datetime
    side: str
    price: float
    size: float
    best_bid: float | None
    best_ask: float | None
    spread: float | None


def rule_hash(rule_text: str) -> str:
    return hashlib.sha256(rule_text.strip().encode("utf-8")).hexdigest()


def normalize_contract(raw: dict[str, object]) -> PolymarketContract:
    text = str(raw["rule_text"])
    return PolymarketContract(
        contract_id=str(raw["contract_id"]),
        asset=str(raw["asset"]).upper(),
        side=str(raw["side"]).upper(),
        threshold=float(str(raw["threshold"])),
        expiry_ts=datetime.fromisoformat(str(raw["expiry_ts"]).replace("Z", "+00:00")),
        settlement_source=str(raw["settlement_source"]),
        token_id=str(raw["token_id"]),
        rule_text=text,
        rule_hash=rule_hash(text),
    )


def normalize_orderbook_snapshot(message: dict[str, Any]) -> PolymarketOrderBookSnapshot:
    bids = _levels(message.get("bids", []))
    asks = _levels(message.get("asks", []))
    best_bid = max(bids, key=lambda level: level[0], default=None)
    best_ask = min(asks, key=lambda level: level[0], default=None)
    best_bid_price = None if best_bid is None else best_bid[0]
    best_ask_price = None if best_ask is None else best_ask[0]
    return PolymarketOrderBookSnapshot(
        venue="polymarket",
        contract_id=str(message["market"]),
        token_id=str(message["asset_id"]),
        event_ts=_timestamp_ms(message["timestamp"]),
        best_bid=best_bid_price,
        best_ask=best_ask_price,
        bid_size_top=None if best_bid is None else best_bid[1],
        ask_size_top=None if best_ask is None else best_ask[1],
        spread=_spread(best_bid_price, best_ask_price),
        depth_json=json.dumps(
            {"bids": message.get("bids", []), "asks": message.get("asks", [])},
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def normalize_price_changes(message: dict[str, Any]) -> tuple[PolymarketPriceChange, ...]:
    event_ts = _timestamp_ms(message["timestamp"])
    changes: list[PolymarketPriceChange] = []
    for change in message.get("price_changes", []):
        if not isinstance(change, dict):
            continue
        best_bid = _optional_float(change.get("best_bid"))
        best_ask = _optional_float(change.get("best_ask"))
        changes.append(
            PolymarketPriceChange(
                contract_id=str(message["market"]),
                token_id=str(change["asset_id"]),
                event_ts=event_ts,
                side=str(change["side"]),
                price=float(str(change["price"])),
                size=float(str(change["size"])),
                best_bid=best_bid,
                best_ask=best_ask,
                spread=_spread(best_bid, best_ask),
            )
        )
    return tuple(changes)


def _levels(raw_levels: object) -> list[tuple[float, float]]:
    if not isinstance(raw_levels, list):
        return []
    levels: list[tuple[float, float]] = []
    for level in raw_levels:
        if isinstance(level, dict):
            levels.append((float(str(level["price"])), float(str(level["size"]))))
    return levels


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(str(value))


def _spread(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None:
        return None
    return ask - bid


def _timestamp_ms(value: object) -> datetime:
    return datetime.fromtimestamp(int(str(value)) / 1000, tz=timezone.utc)
