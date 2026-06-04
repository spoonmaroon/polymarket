from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import PriceObservation
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore
from polymarket_engine.validation.outcomes import computed_winner
from polymarket_engine.validation.outcomes import upsert_computed_market_outcomes


UTC_START = datetime(2026, 6, 3, 20, 0, tzinfo=timezone.utc)
UTC_EXPIRY = UTC_START + timedelta(minutes=5)
UTC_EXPIRY_PLUS_ONE = UTC_EXPIRY + timedelta(seconds=1)
UTC_BEFORE_EXPIRY = UTC_EXPIRY - timedelta(seconds=1)


def test_computed_winner_uses_exact_up_greater_than_or_equal_rule() -> None:
    assert computed_winner(threshold_price=65_000.0, end_price=65_000.0) == "UP"


def test_computed_outcome_uses_exact_up_greater_than_or_equal_rule(
    tmp_path: Path,
) -> None:
    store = seeded_store_with_btc_market(
        tmp_path,
        start_price=65_000.0,
        end_price=65_000.0,
    )

    written = upsert_computed_market_outcomes(store=store, asof_ts=UTC_EXPIRY_PLUS_ONE)

    assert written == 1
    row = fetch_outcome(store.db_path, "btc-updown-5m-1780502400")
    assert row["computed_winner"] == "UP"
    assert row["official_resolution_status"] == "pending"
    assert row["mismatch"] is None


def test_computed_outcome_marks_down_when_end_price_is_below_start(
    tmp_path: Path,
) -> None:
    store = seeded_store_with_btc_market(
        tmp_path,
        start_price=65_000.0,
        end_price=64_999.99,
    )

    upsert_computed_market_outcomes(store=store, asof_ts=UTC_EXPIRY_PLUS_ONE)

    assert fetch_outcome(store.db_path, "btc-updown-5m-1780502400")[
        "computed_winner"
    ] == "DOWN"


def test_computed_outcome_waits_until_after_expiry(tmp_path: Path) -> None:
    store = seeded_store_with_btc_market(
        tmp_path,
        start_price=65_000.0,
        end_price=64_999.99,
    )

    written = upsert_computed_market_outcomes(store=store, asof_ts=UTC_BEFORE_EXPIRY)

    assert written == 0


def test_computed_outcome_skips_when_chainlink_end_tick_is_missing(
    tmp_path: Path,
) -> None:
    store = seeded_store_with_btc_market(
        tmp_path,
        start_price=65_000.0,
        end_price=None,
    )

    written = upsert_computed_market_outcomes(store=store, asof_ts=UTC_EXPIRY_PLUS_ONE)

    assert written == 0


def test_computed_outcome_never_uses_coinbase_for_labels(tmp_path: Path) -> None:
    store = seeded_store_with_btc_market(
        tmp_path,
        start_price=None,
        end_price=None,
        coinbase_start_price=65_000.0,
        coinbase_end_price=65_001.0,
    )

    written = upsert_computed_market_outcomes(store=store, asof_ts=UTC_EXPIRY_PLUS_ONE)

    assert written == 0


def seeded_store_with_btc_market(
    tmp_path: Path,
    *,
    start_price: float | None,
    end_price: float | None,
    coinbase_start_price: float | None = None,
    coinbase_end_price: float | None = None,
) -> DuckDbIngestStore:
    store = DuckDbIngestStore(tmp_path / "outcomes.duckdb")
    store.apply_schema()
    store.upsert_contract_specs(
        (
            _contract("UP", "up-token", ">="),
            _contract("DOWN", "down-token", "<"),
        )
    )
    ticks: list[PriceObservation] = []
    if start_price is not None:
        ticks.append(
            PriceObservation(
                source_key="polymarket_rtds_chainlink",
                symbol="BTC/USD",
                event_ts=UTC_START,
                observed_ts=UTC_START,
                price=start_price,
            )
        )
    if end_price is not None:
        ticks.append(
            PriceObservation(
                source_key="polymarket_rtds_chainlink",
                symbol="BTC/USD",
                event_ts=UTC_EXPIRY,
                observed_ts=UTC_EXPIRY,
                price=end_price,
            )
        )
    if coinbase_start_price is not None:
        ticks.append(
            PriceObservation(
                source_key="coinbase_advanced_ws",
                symbol="BTC-USD",
                event_ts=UTC_START,
                observed_ts=UTC_START,
                price=coinbase_start_price,
            )
        )
    if coinbase_end_price is not None:
        ticks.append(
            PriceObservation(
                source_key="coinbase_advanced_ws",
                symbol="BTC-USD",
                event_ts=UTC_EXPIRY,
                observed_ts=UTC_EXPIRY,
                price=coinbase_end_price,
            )
        )
    store.insert_price_ticks(tuple(ticks))
    return store


def fetch_outcome(db_path: Path, market_id: str) -> dict[str, object]:
    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute(
            """
            select market_id, computed_winner, official_resolution_status, mismatch
            from validation.market_outcome_history
            where market_id = ?
            """,
            [market_id],
        ).fetchone()
    assert row is not None
    return {
        "market_id": row[0],
        "computed_winner": row[1],
        "official_resolution_status": row[2],
        "mismatch": row[3],
    }


def _contract(
    side: str,
    token_id: str,
    comparison_operator: str,
) -> ContractSpec:
    return ContractSpec(
        contract_id=f"btc-updown-5m-1780502400:{side}",
        venue="polymarket",
        market_id="btc-updown-5m-1780502400",
        condition_id="0xbtc",
        slug="btc-updown-5m-1780502400",
        asset="BTC",
        side=side,  # type: ignore[arg-type]
        token_id=token_id,
        threshold_type="start_price",
        threshold_price=None,
        comparison_operator=comparison_operator,  # type: ignore[arg-type]
        start_ts=UTC_START,
        expiry_ts=UTC_EXPIRY,
        settlement_source_name="chainlink_data_streams",
        settlement_source_url="https://data.chain.link/streams/btc-usd",
        settlement_symbol="BTC/USD",
        rule_text="fixture",
        rule_hash="hash",
        parser_version="test",
    )
