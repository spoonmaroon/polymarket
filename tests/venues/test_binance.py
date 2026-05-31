from datetime import datetime, timezone

from polymarket_engine.venues.binance import parse_binance_book_ticker, parse_binance_trade


def test_parse_binance_trade() -> None:
    tick = parse_binance_trade({"s": "BTCUSDT", "T": 1780257601000, "p": "104000.1", "t": 123})

    assert tick.source_key == "binance_spot_ws"
    assert tick.symbol == "BTCUSDT"
    assert tick.price == 104000.1
    assert tick.sequence == "123"


def test_parse_binance_book_ticker() -> None:
    observed = datetime(2026, 5, 31, 20, 0, 1, tzinfo=timezone.utc)
    tick = parse_binance_book_ticker(
        {"u": 400900217, "s": "ETHUSDT", "b": "3500.0", "B": "31.2", "a": "3501.0", "A": "40.6"},
        observed_ts=observed,
    )

    assert tick.event_ts == observed
    assert tick.price == 3500.5
    assert tick.bid == 3500.0
    assert tick.ask == 3501.0
    assert tick.sequence == "400900217"
