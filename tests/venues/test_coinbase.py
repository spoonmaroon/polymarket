from polymarket_engine.venues.coinbase import parse_coinbase_ticker, parse_coinbase_ticker_message


def test_parse_coinbase_ticker() -> None:
    tick = parse_coinbase_ticker(
        {"product_id": "BTC-USD", "time": "2026-05-31T20:00:01Z", "price": "104001.2"}
    )

    assert tick.source_key == "coinbase_advanced_ws"
    assert tick.symbol == "BTC-USD"
    assert tick.price == 104001.2


def test_parse_coinbase_ticker_message_uses_official_wrapper_shape() -> None:
    ticks = parse_coinbase_ticker_message(
        {
            "channel": "ticker",
            "timestamp": "2023-02-09T20:30:37.167359596Z",
            "sequence_num": 0,
            "events": [
                {
                    "type": "snapshot",
                    "tickers": [
                        {
                            "type": "ticker",
                            "product_id": "BTC-USD",
                            "price": "21932.98",
                            "best_bid": "21931.98",
                            "best_ask": "21933.98",
                        }
                    ],
                }
            ],
        }
    )

    assert len(ticks) == 1
    assert ticks[0].symbol == "BTC-USD"
    assert ticks[0].price == 21932.98
    assert ticks[0].bid == 21931.98
    assert ticks[0].ask == 21933.98
