from datetime import datetime, timezone

from polymarket_engine.ingestion.contract_discovery import MarketToken
from polymarket_engine.ingestion.polymarket_clob import (
    build_market_ws_subscription,
    clob_book_event,
    clob_book_top,
)


def test_clob_book_top_uses_highest_bid_and_lowest_ask() -> None:
    book = {
        "bids": [{"price": "0.01", "size": "10"}, {"price": "0.66", "size": "7"}],
        "asks": [{"price": "0.68", "size": "4"}, {"price": "0.99", "size": "1"}],
    }

    top = clob_book_top(book)

    assert top.best_bid == 0.66
    assert top.best_ask == 0.68
    assert top.bid_size_top == 7.0
    assert top.ask_size_top == 4.0


def test_clob_book_event_preserves_contract_slug_and_outcome() -> None:
    observed = datetime(2026, 5, 31, 21, 0, 2, tzinfo=timezone.utc)
    book = {
        "asset_id": "111",
        "market": "0xabc",
        "timestamp": "1780261201000",
        "bids": [{"price": "0.66", "size": "7"}],
        "asks": [{"price": "0.68", "size": "4"}],
    }
    token = MarketToken(slug="btc-updown-5m-1780261200", outcome="Up", token_id="111")

    event = clob_book_event(book, token, observed)

    assert event.source_key == "polymarket_clob"
    assert event.stream_key == "orderbook_snapshot"
    assert event.symbol == "btc-updown-5m-1780261200:Up"
    assert event.payload["contract_id"] == "0xabc"
    assert event.payload["best_bid"] == 0.66
    assert event.payload["best_ask"] == 0.68
    assert round(float(event.payload["spread"]), 2) == 0.02
    assert '"asks"' in event.payload["depth_json"]


def test_build_market_ws_subscription_uses_asset_ids() -> None:
    assert build_market_ws_subscription(("111", "222")) == {
        "assets_ids": ["111", "222"],
        "type": "market",
        "custom_feature_enabled": True,
    }
