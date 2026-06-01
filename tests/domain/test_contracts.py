from datetime import datetime, timezone

import pytest

from polymarket_engine.domain.contract_rules import parse_polymarket_crypto_updown_rule
from polymarket_engine.domain.contracts import ContractSpec, contract_specs_from_rule


BTC_DESCRIPTION = """This market will resolve to "Up" if the Bitcoin price at the end of the time range specified in the title is greater than or equal to the price at the beginning of that range. Otherwise, it will resolve to "Down".
The resolution source for this market is information from Chainlink, specifically the BTC/USD data stream available at https://data.chain.link/streams/btc-usd.
Please note that this market is about the price according to Chainlink data stream BTC/USD, not according to other sources or spot exchanges."""


ETH_DESCRIPTION = BTC_DESCRIPTION.replace("Bitcoin", "Ethereum").replace(
    "BTC/USD", "ETH/USD"
).replace("btc-usd", "eth-usd")


def _market(asset: str, description: str) -> dict[str, object]:
    lower = asset.lower()
    return {
        "id": f"{asset}-market-1",
        "conditionId": f"0x{lower}",
        "slug": f"{lower}-updown-5m-1780264500",
        "question": f"{asset} Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": description,
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": f"https://data.chain.link/streams/{lower}-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }


def test_contract_specs_from_btc_start_price_rule() -> None:
    rule = parse_polymarket_crypto_updown_rule(_market("btc", BTC_DESCRIPTION))

    up, down = contract_specs_from_rule(rule)

    assert up.contract_id == "btc-market-1:UP"
    assert up.venue == "polymarket"
    assert up.asset == "BTC"
    assert up.side == "UP"
    assert up.token_id == "111"
    assert up.threshold_type == "start_price"
    assert up.threshold_price is None
    assert up.comparison_operator == ">="
    assert up.settlement_symbol == "BTC/USD"
    assert up.start_ts == datetime(2026, 5, 31, 21, 55, tzinfo=timezone.utc)
    assert up.expiry_ts == datetime(2026, 5, 31, 22, 0, tzinfo=timezone.utc)

    assert down.contract_id == "btc-market-1:DOWN"
    assert down.asset == "BTC"
    assert down.side == "DOWN"
    assert down.token_id == "222"
    assert down.comparison_operator == "<"
    assert down.rule_hash == up.rule_hash


def test_contract_specs_from_eth_start_price_rule() -> None:
    rule = parse_polymarket_crypto_updown_rule(_market("eth", ETH_DESCRIPTION))

    up, down = contract_specs_from_rule(rule)

    assert up.asset == "ETH"
    assert up.settlement_symbol == "ETH/USD"
    assert down.asset == "ETH"
    assert down.settlement_symbol == "ETH/USD"


def test_fixed_threshold_contract_spec_is_supported_at_object_level() -> None:
    spec = ContractSpec(
        contract_id="manual-btc-up",
        venue="polymarket",
        market_id="manual-market",
        condition_id="0xmanual",
        slug="manual-btc-fixed",
        asset="BTC",
        side="UP",
        token_id="token-up",
        threshold_type="fixed_price",
        threshold_price=105_000.0,
        comparison_operator=">",
        start_ts=datetime(2026, 5, 31, 21, 55, tzinfo=timezone.utc),
        expiry_ts=datetime(2026, 5, 31, 22, 0, tzinfo=timezone.utc),
        settlement_source_name="chainlink_data_streams",
        settlement_source_url="https://data.chain.link/streams/btc-usd",
        settlement_symbol="BTC/USD",
        rule_text="Manual fixed threshold fixture.",
        rule_hash="abc123",
        parser_version="manual_fixture",
    )

    assert spec.threshold_type == "fixed_price"
    assert spec.threshold_price == 105_000.0


def test_fixed_threshold_requires_threshold_price() -> None:
    with pytest.raises(ValueError, match="fixed_price requires threshold_price"):
        ContractSpec(
            contract_id="bad-fixed",
            venue="polymarket",
            market_id="manual-market",
            condition_id="0xmanual",
            slug="manual-btc-fixed",
            asset="BTC",
            side="UP",
            token_id="token-up",
            threshold_type="fixed_price",
            threshold_price=None,
            comparison_operator=">",
            start_ts=datetime(2026, 5, 31, 21, 55, tzinfo=timezone.utc),
            expiry_ts=datetime(2026, 5, 31, 22, 0, tzinfo=timezone.utc),
            settlement_source_name="chainlink_data_streams",
            settlement_source_url="https://data.chain.link/streams/btc-usd",
            settlement_symbol="BTC/USD",
            rule_text="Manual fixed threshold fixture.",
            rule_hash="abc123",
            parser_version="manual_fixture",
        )


def test_contract_spec_rejects_side_operator_mismatch() -> None:
    with pytest.raises(ValueError, match="UP side requires greater-than comparison operator"):
        ContractSpec(
            contract_id="bad-up",
            venue="polymarket",
            market_id="manual-market",
            condition_id="0xmanual",
            slug="manual-btc-fixed",
            asset="BTC",
            side="UP",
            token_id="token-up",
            threshold_type="fixed_price",
            threshold_price=105_000.0,
            comparison_operator="<",
            start_ts=datetime(2026, 5, 31, 21, 55, tzinfo=timezone.utc),
            expiry_ts=datetime(2026, 5, 31, 22, 0, tzinfo=timezone.utc),
            settlement_source_name="chainlink_data_streams",
            settlement_source_url="https://data.chain.link/streams/btc-usd",
            settlement_symbol="BTC/USD",
            rule_text="Manual fixed threshold fixture.",
            rule_hash="abc123",
            parser_version="manual_fixture",
        )
