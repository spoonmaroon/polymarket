from polymarket_engine.venues.polymarket import normalize_contract, rule_hash


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
