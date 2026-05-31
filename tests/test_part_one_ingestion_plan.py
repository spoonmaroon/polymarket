from polymarket_engine.ingestion.runner import build_part_one_ingestion_plan


def test_part_one_ingestion_plan_is_paper_only() -> None:
    plan = build_part_one_ingestion_plan()

    assert plan.paper_only is True
    assert {source.key for source in plan.sources} == {
        "polymarket_markets",
        "polymarket_clob",
        "polymarket_market_ws",
        "polymarket_rtds_chainlink",
        "binance_spot_ws",
        "coinbase_advanced_ws",
    }
