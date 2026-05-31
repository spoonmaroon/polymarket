from polymarket_engine.venues.coinbase import parse_coinbase_ticker


def test_parse_coinbase_ticker() -> None:
    tick = parse_coinbase_ticker(
        {"product_id": "BTC-USD", "time": "2026-05-31T20:00:01Z", "price": "104001.2"}
    )

    assert tick.source_key == "coinbase_advanced_ws"
    assert tick.symbol == "BTC-USD"
    assert tick.price == 104001.2
