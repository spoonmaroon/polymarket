import json
from datetime import datetime, timezone

from polymarket_engine.ingestion.contract_discovery import MarketToken
from polymarket_engine.ingestion.polymarket_clob_ws import (
    CLOB_MARKET_WS_URL,
    build_market_ws_subscribe_message,
    clob_market_ws_events,
)


def test_build_market_ws_subscribe_message_requests_best_bid_ask() -> None:
    message = build_market_ws_subscribe_message(("111", "222"))

    assert CLOB_MARKET_WS_URL == "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    assert message == {
        "assets_ids": ["111", "222"],
        "type": "market",
        "custom_feature_enabled": True,
    }


def test_market_ws_book_becomes_orderbook_snapshot_event() -> None:
    observed = datetime(2026, 6, 1, 10, 55, 1, tzinfo=timezone.utc)
    token = MarketToken(slug="btc-updown-5m-1780301700", outcome="UP", token_id="111")
    events = clob_market_ws_events(
        {
            "event_type": "book",
            "asset_id": "111",
            "market": "0xabc",
            "timestamp": "1780301701000",
            "bids": [{"price": "0.48", "size": "20"}, {"price": "0.50", "size": "8"}],
            "asks": [{"price": "0.52", "size": "9"}, {"price": "0.54", "size": "30"}],
            "hash": "0xbookhash",
        },
        {"111": token},
        observed,
    )

    assert len(events) == 1
    event = events[0]
    assert event.source_key == "polymarket_market_ws"
    assert event.stream_key == "orderbook_snapshot"
    assert event.symbol == "btc-updown-5m-1780301700:UP"
    assert event.event_ts.isoformat() == "2026-06-01T10:55:01+00:00"
    assert event.observed_ts == observed
    assert event.payload["token_id"] == "111"
    assert event.payload["contract_id"] == "0xabc"
    assert event.payload["best_bid"] == 0.50
    assert event.payload["best_ask"] == 0.52
    assert round(float(event.payload["spread"]), 2) == 0.02
    assert event.payload["event_type"] == "book"
    assert '"bids"' in str(event.payload["depth_json"])


def test_market_ws_best_bid_ask_becomes_top_of_book_event() -> None:
    observed = datetime(2026, 6, 1, 10, 55, 2, tzinfo=timezone.utc)
    token = MarketToken(slug="eth-updown-5m-1780301700", outcome="DOWN", token_id="222")
    events = clob_market_ws_events(
        {
            "event_type": "best_bid_ask",
            "asset_id": "222",
            "market": "0xdef",
            "timestamp": "1780301702000",
            "best_bid": "0.41",
            "best_ask": "0.42",
        },
        {"222": token},
        observed,
    )

    assert len(events) == 1
    event = events[0]
    assert event.source_key == "polymarket_market_ws"
    assert event.stream_key == "top_of_book"
    assert event.payload["best_bid"] == 0.41
    assert event.payload["best_ask"] == 0.42
    assert round(float(event.payload["spread"]), 2) == 0.01
    assert event.payload["depth_json"] == '{"source":"best_bid_ask"}'


def test_market_ws_accepts_json_string_messages() -> None:
    observed = datetime(2026, 6, 1, 10, 55, 2, tzinfo=timezone.utc)
    token = MarketToken(slug="eth-updown-5m-1780301700", outcome="DOWN", token_id="222")
    events = clob_market_ws_events(
        json.dumps(
            {
                "event_type": "best_bid_ask",
                "asset_id": "222",
                "market": "0xdef",
                "timestamp": "1780301702000",
                "best_bid": "0.41",
                "best_ask": "0.42",
            }
        ),
        {"222": token},
        observed,
    )

    assert len(events) == 1
    assert events[0].payload["best_bid"] == 0.41


def test_market_ws_price_change_with_best_prices_updates_top_of_book() -> None:
    observed = datetime(2026, 6, 1, 10, 55, 3, tzinfo=timezone.utc)
    token = MarketToken(slug="btc-updown-5m-1780301700", outcome="DOWN", token_id="333")
    events = clob_market_ws_events(
        {
            "event_type": "price_change",
            "market": "0xghi",
            "timestamp": "1780301703000",
            "price_changes": [
                {
                    "asset_id": "333",
                    "price": "0.39",
                    "size": "200",
                    "side": "BUY",
                    "best_bid": "0.39",
                    "best_ask": "0.40",
                }
            ],
        },
        {"333": token},
        observed,
    )

    assert len(events) == 1
    event = events[0]
    assert event.source_key == "polymarket_market_ws"
    assert event.stream_key == "top_of_book"
    assert event.payload["price"] == 0.39
    assert event.payload["size"] == 200.0
    assert event.payload["side"] == "BUY"
    assert event.payload["best_bid"] == 0.39
    assert event.payload["best_ask"] == 0.40


def test_market_ws_ignores_unknown_asset_and_pong() -> None:
    observed = datetime(2026, 6, 1, 10, 55, 4, tzinfo=timezone.utc)

    assert clob_market_ws_events("PONG", {}, observed) == ()
    assert (
        clob_market_ws_events(
            {
                "event_type": "best_bid_ask",
                "asset_id": "missing",
                "market": "0x0",
                "timestamp": "1780301704000",
                "best_bid": "0.50",
                "best_ask": "0.51",
            },
            {},
            observed,
        )
        == ()
    )
