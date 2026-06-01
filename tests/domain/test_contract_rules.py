from datetime import datetime, timezone

import pytest

from polymarket_engine.domain.contract_rules import (
    ContractRuleRejected,
    parse_polymarket_crypto_updown_rule,
    rule_text_hash,
)


BTC_DESCRIPTION = """This market will resolve to "Up" if the Bitcoin price at the end of the time range specified in the title is greater than or equal to the price at the beginning of that range. Otherwise, it will resolve to "Down".
The resolution source for this market is information from Chainlink, specifically the BTC/USD data stream available at https://data.chain.link/streams/btc-usd.
Please note that this market is about the price according to Chainlink data stream BTC/USD, not according to other sources or spot exchanges."""


ETH_DESCRIPTION = BTC_DESCRIPTION.replace("Bitcoin", "Ethereum").replace(
    "BTC/USD", "ETH/USD"
).replace("btc-usd", "eth-usd")


def test_parse_btc_updown_start_price_rule() -> None:
    market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-updown-5m-1780264500",
        "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": BTC_DESCRIPTION,
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    rule = parse_polymarket_crypto_updown_rule(market)

    assert rule.accepted is True
    assert rule.reject_reason is None
    assert rule.market_id == "2397858"
    assert rule.condition_id == "0xabc"
    assert rule.slug == "btc-updown-5m-1780264500"
    assert rule.asset == "BTC"
    assert rule.contract_type == "crypto_up_down_start_price"
    assert rule.start_ts == datetime(2026, 5, 31, 21, 55, tzinfo=timezone.utc)
    assert rule.end_ts == datetime(2026, 5, 31, 22, 0, tzinfo=timezone.utc)
    assert rule.expiry_ts == rule.end_ts
    assert rule.threshold_type == "start_price"
    assert rule.threshold_price is None
    assert rule.comparison_operator_up == ">="
    assert rule.comparison_operator_down == "<"
    assert rule.settlement_source_name == "chainlink_data_streams"
    assert rule.settlement_source_url == "https://data.chain.link/streams/btc-usd"
    assert rule.settlement_symbol == "BTC/USD"
    assert rule.outcome_token_ids == {"Up": "111", "Down": "222"}
    assert rule.rule_hash == rule_text_hash(BTC_DESCRIPTION)
    assert rule.parser_version == "polymarket_crypto_updown_v1"


@pytest.mark.parametrize(
    "phrase",
    ["greater than or equal to", "at or above", "not below"],
)
def test_parse_accepts_supported_tie_phrases(phrase: str) -> None:
    description = BTC_DESCRIPTION.replace("greater than or equal to", phrase)
    market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-updown-5m-1780264500",
        "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": description,
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    assert parse_polymarket_crypto_updown_rule(market).comparison_operator_up == ">="


def test_parse_rejects_ambiguous_tie_rule() -> None:
    market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-updown-5m-1780264500",
        "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": BTC_DESCRIPTION.replace("greater than or equal to", "higher than"),
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    with pytest.raises(ContractRuleRejected, match="ambiguous tie rule"):
        parse_polymarket_crypto_updown_rule(market)


def test_parse_rejects_wrong_settlement_source() -> None:
    market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-updown-5m-1780264500",
        "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": BTC_DESCRIPTION,
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://example.com/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    with pytest.raises(ContractRuleRejected, match="unsupported settlement source"):
        parse_polymarket_crypto_updown_rule(market)


def test_parse_eth_updown_start_price_rule() -> None:
    market = {
        "id": "2397999",
        "conditionId": "0xeth",
        "slug": "eth-updown-5m-1780264500",
        "question": "Ethereum Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": ETH_DESCRIPTION,
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/eth-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["333", "444"]',
    }

    rule = parse_polymarket_crypto_updown_rule(market)

    assert rule.asset == "ETH"
    assert rule.settlement_symbol == "ETH/USD"
    assert rule.settlement_source_url == "https://data.chain.link/streams/eth-usd"
    assert rule.outcome_token_ids == {"Up": "333", "Down": "444"}


def test_parse_rejects_unsupported_slug_before_state_building() -> None:
    market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-above-100000",
        "question": "Bitcoin above 100000",
        "description": BTC_DESCRIPTION,
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    with pytest.raises(ContractRuleRejected, match="unsupported slug"):
        parse_polymarket_crypto_updown_rule(market)


def test_parse_rejects_missing_end_price_rule() -> None:
    market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-updown-5m-1780264500",
        "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": BTC_DESCRIPTION.replace("price at the end", "final quote"),
        "eventStartTime": "2026-05-31T21:55:00Z",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    with pytest.raises(ContractRuleRejected, match="missing end-price comparison rule"):
        parse_polymarket_crypto_updown_rule(market)


def test_parse_rejects_naive_timestamps() -> None:
    market = {
        "id": "2397858",
        "conditionId": "0xabc",
        "slug": "btc-updown-5m-1780264500",
        "question": "Bitcoin Up or Down - May 31, 5:55PM-6:00PM ET",
        "description": BTC_DESCRIPTION,
        "eventStartTime": "2026-05-31T21:55:00",
        "endDate": "2026-05-31T22:00:00Z",
        "resolutionSource": "https://data.chain.link/streams/btc-usd",
        "outcomes": '["Up", "Down"]',
        "clobTokenIds": '["111", "222"]',
    }

    with pytest.raises(ContractRuleRejected, match="timestamp must be timezone-aware"):
        parse_polymarket_crypto_updown_rule(market)
