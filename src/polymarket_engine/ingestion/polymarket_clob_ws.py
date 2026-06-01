from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from polymarket_engine.ingestion.collector_events import CollectorEvent
from polymarket_engine.ingestion.contract_discovery import MarketToken
from polymarket_engine.venues.polymarket import (
    normalize_orderbook_snapshot,
    normalize_price_changes,
)

CLOB_MARKET_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"

_SOURCE_KEY = "polymarket_market_ws"


def build_market_ws_subscribe_message(asset_ids: tuple[str, ...]) -> dict[str, object]:
    return {
        "assets_ids": list(asset_ids),
        "type": "market",
        "custom_feature_enabled": True,
    }


def build_market_ws_assets_update_message(
    asset_ids: tuple[str, ...],
    *,
    operation: str,
) -> dict[str, object]:
    return {
        "operation": operation,
        "assets_ids": list(asset_ids),
        "type": "market",
        "custom_feature_enabled": True,
    }


def clob_market_ws_events(
    message: object,
    tokens_by_id: dict[str, MarketToken],
    observed_ts: datetime,
) -> tuple[CollectorEvent, ...]:
    if isinstance(message, str) and message.upper() in {"PING", "PONG"}:
        return ()
    if isinstance(message, str):
        message = json.loads(message)
    if not isinstance(message, dict):
        return ()

    event_type = str(message.get("event_type", ""))
    if event_type == "book":
        event = _book_event(message, tokens_by_id, observed_ts)
        return () if event is None else (event,)
    if event_type == "best_bid_ask":
        event = _best_bid_ask_event(message, tokens_by_id, observed_ts)
        return () if event is None else (event,)
    if event_type == "price_change":
        return _price_change_events(message, tokens_by_id, observed_ts)
    return ()


def _book_event(
    message: dict[str, Any],
    tokens_by_id: dict[str, MarketToken],
    observed_ts: datetime,
) -> CollectorEvent | None:
    token_id = str(message.get("asset_id", ""))
    token = tokens_by_id.get(token_id)
    if token is None:
        return None

    snapshot = normalize_orderbook_snapshot(message)
    return CollectorEvent(
        source_key=_SOURCE_KEY,
        stream_key="orderbook_snapshot",
        symbol=_symbol(token),
        event_ts=snapshot.event_ts,
        observed_ts=observed_ts,
        payload={
            **message,
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


def _best_bid_ask_event(
    message: dict[str, Any],
    tokens_by_id: dict[str, MarketToken],
    observed_ts: datetime,
) -> CollectorEvent | None:
    token_id = str(message.get("asset_id", ""))
    token = tokens_by_id.get(token_id)
    if token is None:
        return None

    best_bid = _optional_float(message.get("best_bid"))
    best_ask = _optional_float(message.get("best_ask"))
    return CollectorEvent(
        source_key=_SOURCE_KEY,
        stream_key="top_of_book",
        symbol=_symbol(token),
        event_ts=_timestamp_ms(message["timestamp"]),
        observed_ts=observed_ts,
        payload={
            "event_type": "best_bid_ask",
            "contract_slug": token.slug,
            "outcome": token.outcome,
            "token_id": token.token_id,
            "contract_id": str(message["market"]),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": _spread(best_bid, best_ask),
            "depth_json": '{"source":"best_bid_ask"}',
        },
    )


def _price_change_events(
    message: dict[str, Any],
    tokens_by_id: dict[str, MarketToken],
    observed_ts: datetime,
) -> tuple[CollectorEvent, ...]:
    events: list[CollectorEvent] = []
    for change in normalize_price_changes(message):
        token = tokens_by_id.get(change.token_id)
        if token is None:
            continue
        events.append(
            CollectorEvent(
                source_key=_SOURCE_KEY,
                stream_key="top_of_book",
                symbol=_symbol(token),
                event_ts=change.event_ts,
                observed_ts=observed_ts,
                payload={
                    "event_type": "price_change",
                    "contract_slug": token.slug,
                    "outcome": token.outcome,
                    "token_id": token.token_id,
                    "contract_id": change.contract_id,
                    "side": change.side,
                    "price": change.price,
                    "size": change.size,
                    "best_bid": change.best_bid,
                    "best_ask": change.best_ask,
                    "spread": change.spread,
                    "depth_json": '{"source":"price_change"}',
                },
            )
        )
    return tuple(events)


def _symbol(token: MarketToken) -> str:
    return f"{token.slug}:{token.outcome}"


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
