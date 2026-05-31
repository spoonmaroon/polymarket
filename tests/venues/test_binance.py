from polymarket_engine.venues.binance import parse_binance_book_ticker, parse_binance_trade


def test_parse_binance_trade() -> None:
    tick = parse_binance_trade({"s": "BTCUSDT", "T": 1780257601000, "p": "104000.1", "t": 123})

    assert tick.source_key == "binance_spot_ws"
    assert tick.symbol == "BTCUSDT"
    assert tick.price == 104000.1
    assert tick.sequence == "123"


def test_parse_binance_book_ticker() -> None:
    tick = parse_binance_book_ticker(
        {"s": "ETHUSDT", "E": 1780257601000, "b": "3500.0", "a": "3501.0", "u": 9}
    )

    assert tick.price == 3500.5
    assert tick.bid == 3500.0
    assert tick.ask == 3501.0
