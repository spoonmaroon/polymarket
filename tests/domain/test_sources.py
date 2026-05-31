from polymarket_engine.domain.sources import LOCKED_SOURCES, SourceRole, SourceStatus, part_one_sources


def test_part_one_sources_are_locked() -> None:
    keys = {source.key for source in part_one_sources()}

    assert keys == {
        "polymarket_markets",
        "polymarket_clob",
        "polymarket_market_ws",
        "polymarket_rtds_chainlink",
        "binance_spot_ws",
        "coinbase_advanced_ws",
    }


def test_etf_and_jupiter_are_not_part_one() -> None:
    assert LOCKED_SOURCES["etf_gex_context"].status == SourceStatus.DEFERRED
    assert LOCKED_SOURCES["jupiter_prediction_markets"].status == SourceStatus.DEFERRED


def test_settlement_source_is_separate_from_proxy_sources() -> None:
    assert LOCKED_SOURCES["polymarket_rtds_chainlink"].role == SourceRole.SETTLEMENT_REFERENCE
    assert LOCKED_SOURCES["binance_spot_ws"].role == SourceRole.PRICE_PROXY
    assert LOCKED_SOURCES["coinbase_advanced_ws"].role == SourceRole.PRICE_PROXY
