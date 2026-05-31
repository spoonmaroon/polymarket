from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from polymarket_engine.ingestion.collector_events import CollectorEvent
from polymarket_engine.ingestion.contract_discovery import MarketToken
from polymarket_engine.venues.polymarket import normalize_orderbook_snapshot


@dataclass(frozen=True)
class BookTop:
    best_bid: float | None
    best_ask: float | None
    bid_size_top: float | None
    ask_size_top: float | None


def _best_bid(levels: list[dict[str, object]]) -> dict[str, object] | None:
    if not levels:
        return None
    return max(levels, key=lambda level: float(str(level["price"])))


def _best_ask(levels: list[dict[str, object]]) -> dict[str, object] | None:
    if not levels:
        return None
    return min(levels, key=lambda level: float(str(level["price"])))


def clob_book_top(book: dict[str, Any]) -> BookTop:
    bid = _best_bid(list(book.get("bids", [])))
    ask = _best_ask(list(book.get("asks", [])))
    return BookTop(
        best_bid=None if bid is None else float(str(bid["price"])),
        best_ask=None if ask is None else float(str(ask["price"])),
        bid_size_top=None if bid is None else float(str(bid["size"])),
        ask_size_top=None if ask is None else float(str(ask["size"])),
    )


def clob_book_event(
    book: dict[str, Any],
    token: MarketToken,
    observed_ts: datetime,
) -> CollectorEvent:
    timestamp_ms = int(str(book["timestamp"]))
    event_ts = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
    snapshot = normalize_orderbook_snapshot(book)
    return CollectorEvent(
        source_key="polymarket_clob",
        stream_key="orderbook_snapshot",
        symbol=f"{token.slug}:{token.outcome}",
        event_ts=event_ts,
        observed_ts=observed_ts,
        payload={
            **book,
            "contract_slug": token.slug,
            "outcome": token.outcome,
            "token_id": token.token_id,
            "contract_id": snapshot.contract_id,
            "best_bid": snapshot.best_bid,
            "best_ask": snapshot.best_ask,
            "bid_size_top": snapshot.bid_size_top,
            "ask_size_top": snapshot.ask_size_top,
            "spread": snapshot.spread,
            "depth_json": snapshot.depth_json,
        },
    )


def build_market_ws_subscription(asset_ids: tuple[str, ...]) -> dict[str, object]:
    return {
        "assets_ids": list(asset_ids),
        "type": "market",
        "custom_feature_enabled": True,
    }
