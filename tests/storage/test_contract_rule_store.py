from datetime import datetime, timezone
from pathlib import Path

import duckdb

from polymarket_engine.domain.contract_rules import NormalizedContractRule
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def test_store_upserts_contract_rule(tmp_path: Path) -> None:
    db_path = tmp_path / "test.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    rule = NormalizedContractRule(
        market_id="2397858",
        condition_id="0xabc",
        slug="btc-updown-5m-1780264500",
        asset="BTC",
        contract_type="crypto_up_down_start_price",
        start_ts=datetime(2026, 5, 31, 21, 55, tzinfo=timezone.utc),
        end_ts=datetime(2026, 5, 31, 22, 0, tzinfo=timezone.utc),
        expiry_ts=datetime(2026, 5, 31, 22, 0, tzinfo=timezone.utc),
        threshold_type="start_price",
        threshold_price=None,
        comparison_operator_up=">=",
        comparison_operator_down="<",
        settlement_source_name="chainlink_data_streams",
        settlement_source_url="https://data.chain.link/streams/btc-usd",
        settlement_symbol="BTC/USD",
        outcome_token_ids={"Up": "111", "Down": "222"},
        rule_text="rule",
        rule_hash="hash",
        parser_version="polymarket_crypto_updown_v1",
        accepted=True,
        reject_reason=None,
    )

    store.upsert_contract_rule(rule)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute(
            "select asset, threshold_type, comparison_operator_up, settlement_symbol, accepted "
            "from core.contract_rules where market_id = ?",
            ["2397858"],
        ).fetchone()

    assert row == ("BTC", "start_price", ">=", "BTC/USD", True)
