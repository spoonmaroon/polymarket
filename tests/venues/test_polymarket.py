from polymarket_engine.venues.polymarket import (
    normalize_contract,
    normalize_orderbook_snapshot,
    normalize_price_changes,
    rule_hash,
)


def test_normalize_contract_hashes_rule_text() -> None:
    contract = normalize_contract(
        {
            "contract_id": "btc-up-5m-1",
            "asset": "btc",
            "side": "up",
            "threshold": 104000,
            "expiry_ts": "2026-05-31T20:05:00Z",
            "settlement_source": "chainlink_btc_usd",
            "token_id": "123",
            "rule_text": "BTC resolves up if final settlement price is above 104000.",
        }
    )

    assert contract.asset == "BTC"
    assert contract.side == "UP"
    assert contract.rule_hash == rule_hash(contract.rule_text)


def test_normalize_orderbook_snapshot_uses_market_channel_book_shape() -> None:
    snapshot = normalize_orderbook_snapshot(
        {
            "event_type": "book",
            "asset_id": "65818619657568813474341868652308942079804919287380422192892211131408793125422",
            "market": "0xbd31dc8a20211944f6b70f31557f1001557b59905b7738480ca09bd4532f84af",
            "bids": [
                {"price": ".48", "size": "30"},
                {"price": ".50", "size": "15"},
            ],
            "asks": [
                {"price": ".52", "size": "25"},
                {"price": ".54", "size": "10"},
            ],
            "timestamp": "123456789000",
            "hash": "0x0",
        }
    )

    assert snapshot.venue == "polymarket"
    assert snapshot.contract_id == "0xbd31dc8a20211944f6b70f31557f1001557b59905b7738480ca09bd4532f84af"
    assert snapshot.best_bid == 0.50
    assert snapshot.best_ask == 0.52
    assert snapshot.bid_size_top == 15.0
    assert snapshot.ask_size_top == 25.0
    assert snapshot.spread is not None
    assert round(snapshot.spread, 2) == 0.02
    assert '"bids"' in snapshot.depth_json


def test_normalize_price_changes_uses_market_channel_update_shape() -> None:
    changes = normalize_price_changes(
        {
            "market": "0x5f65177b394277fd294cd75650044e32ba009a95022d88a0c1d565897d72f8f1",
            "price_changes": [
                {
                    "asset_id": "71321045679252212594626385532706912750332728571942532289631379312455583992563",
                    "price": "0.5",
                    "size": "200",
                    "side": "BUY",
                    "hash": "56621a121a47ed9333273e21c83b660cff37ae50",
                    "best_bid": "0.5",
                    "best_ask": "1",
                }
            ],
            "timestamp": "1757908892351",
            "event_type": "price_change",
        }
    )

    assert len(changes) == 1
    assert changes[0].side == "BUY"
    assert changes[0].price == 0.5
    assert changes[0].size == 200.0
    assert changes[0].best_bid == 0.5
    assert changes[0].best_ask == 1.0
    assert changes[0].spread == 0.5
