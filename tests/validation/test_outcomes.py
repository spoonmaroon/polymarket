from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import PriceObservation
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore
from polymarket_engine.validation.outcomes import official_resolution_from_polymarket_market
from polymarket_engine.validation.outcomes import latest_market_outcome_rows
from polymarket_engine.validation.outcomes import upsert_official_market_outcomes


UTC_START = datetime(2026, 6, 3, 20, 0, tzinfo=timezone.utc)
UTC_EXPIRY = UTC_START + timedelta(minutes=5)
UTC_EXPIRY_PLUS_ONE = UTC_EXPIRY + timedelta(seconds=1)
UTC_BEFORE_EXPIRY = UTC_EXPIRY - timedelta(seconds=1)


def test_official_resolution_maps_winning_up_token_from_polymarket_payload() -> None:
    resolution = official_resolution_from_polymarket_market(
        {
            "closed": True,
            "tokens": [
                {"token_id": "up-token", "outcome": "Up", "winner": True},
                {"token_id": "down-token", "outcome": "Down", "winner": False},
            ],
        },
        up_token_id="up-token",
        down_token_id="down-token",
        observed_at=UTC_EXPIRY_PLUS_ONE,
    )

    assert resolution.official_winner == "UP"
    assert resolution.winning_token_id == "up-token"
    assert resolution.official_resolution_status == "resolved"
    assert resolution.official_label_source == "polymarket_clob_market"
    assert resolution.official_resolved_at == UTC_EXPIRY_PLUS_ONE


def test_official_resolution_maps_winning_down_token_from_polymarket_payload() -> None:
    resolution = official_resolution_from_polymarket_market(
        {
            "closed": True,
            "tokens": [
                {"token_id": "up-token", "outcome": "Up", "winner": False},
                {"token_id": "down-token", "outcome": "Down", "winner": True},
            ],
        },
        up_token_id="up-token",
        down_token_id="down-token",
        observed_at=UTC_EXPIRY_PLUS_ONE,
    )

    assert resolution.official_winner == "DOWN"
    assert resolution.winning_token_id == "down-token"
    assert resolution.official_resolution_status == "resolved"


def test_official_resolution_stays_pending_for_ambiguous_polymarket_payload() -> None:
    resolution = official_resolution_from_polymarket_market(
        {
            "closed": True,
            "tokens": [
                {"token_id": "up-token", "outcome": "Up", "winner": True},
                {"token_id": "down-token", "outcome": "Down", "winner": True},
            ],
        },
        up_token_id="up-token",
        down_token_id="down-token",
        observed_at=UTC_EXPIRY_PLUS_ONE,
    )

    assert resolution.official_winner is None
    assert resolution.winning_token_id is None
    assert resolution.official_resolution_status == "pending"
    assert resolution.official_label_source is None


def test_official_outcome_uses_only_polymarket_source_payload(
    tmp_path: Path,
) -> None:
    store = seeded_store_with_btc_market(
        tmp_path,
        start_price=65_000.0,
        end_price=65_000.0,
    )

    written = upsert_official_market_outcomes(
        store=store,
        asof_ts=UTC_EXPIRY_PLUS_ONE,
        market_payload_source=lambda _condition_id: _polymarket_market_payload(
            winning_token_id="up-token"
        ),
    )

    assert written == 1
    row = fetch_outcome(store.db_path, "btc-updown-5m-1780502400")
    assert row["computed_winner"] is None
    assert row["official_winner"] == "UP"
    assert row["winning_token_id"] == "up-token"
    assert row["official_resolution_status"] == "resolved"
    assert row["official_label_source"] == "polymarket_clob_market"
    assert row["mismatch"] is None


def test_latest_market_outcome_rows_reads_history_read_only(tmp_path: Path) -> None:
    store = seeded_store_with_btc_market(
        tmp_path,
        start_price=65_000.0,
        end_price=65_000.0,
    )
    upsert_official_market_outcomes(
        store=store,
        asof_ts=UTC_EXPIRY_PLUS_ONE,
        market_payload_source=lambda _condition_id: _polymarket_market_payload(
            winning_token_id="down-token"
        ),
    )

    rows = latest_market_outcome_rows(duckdb_path=store.db_path, limit=4)

    assert rows[0]["market_id"] == "btc-updown-5m-1780502400"
    assert rows[0]["computed_winner"] is None
    assert rows[0]["official_winner"] == "DOWN"
    assert rows[0]["winning_token_id"] == "down-token"


def test_official_outcome_does_not_label_from_chainlink_prices(
    tmp_path: Path,
) -> None:
    store = seeded_store_with_btc_market(
        tmp_path,
        start_price=65_000.0,
        end_price=64_999.99,
    )

    written = upsert_official_market_outcomes(
        store=store,
        asof_ts=UTC_EXPIRY_PLUS_ONE,
        market_payload_source=lambda _condition_id: None,
    )

    row = fetch_outcome(store.db_path, "btc-updown-5m-1780502400")
    assert written == 1
    assert row["computed_winner"] is None
    assert row["official_winner"] is None
    assert row["official_resolution_status"] == "pending"


def test_official_outcome_waits_until_after_expiry(tmp_path: Path) -> None:
    store = seeded_store_with_btc_market(
        tmp_path,
        start_price=65_000.0,
        end_price=64_999.99,
    )

    written = upsert_official_market_outcomes(
        store=store,
        asof_ts=UTC_BEFORE_EXPIRY,
        market_payload_source=lambda _condition_id: _polymarket_market_payload(
            winning_token_id="up-token"
        ),
    )

    assert written == 0


def test_official_outcome_does_not_require_chainlink_end_tick(
    tmp_path: Path,
) -> None:
    store = seeded_store_with_btc_market(
        tmp_path,
        start_price=65_000.0,
        end_price=None,
    )

    written = upsert_official_market_outcomes(
        store=store,
        asof_ts=UTC_EXPIRY_PLUS_ONE,
        market_payload_source=lambda _condition_id: _polymarket_market_payload(
            winning_token_id="down-token"
        ),
    )

    row = fetch_outcome(store.db_path, "btc-updown-5m-1780502400")
    assert written == 1
    assert row["computed_winner"] is None
    assert row["official_winner"] == "DOWN"


def test_official_outcome_never_uses_coinbase_for_labels(tmp_path: Path) -> None:
    store = seeded_store_with_btc_market(
        tmp_path,
        start_price=None,
        end_price=None,
        coinbase_start_price=65_000.0,
        coinbase_end_price=65_001.0,
    )

    written = upsert_official_market_outcomes(
        store=store,
        asof_ts=UTC_EXPIRY_PLUS_ONE,
        market_payload_source=lambda _condition_id: None,
    )

    row = fetch_outcome(store.db_path, "btc-updown-5m-1780502400")
    assert written == 1
    assert row["computed_winner"] is None
    assert row["official_winner"] is None
    assert row["official_resolution_status"] == "pending"


def test_official_outcome_refresh_can_be_limited_to_newest_markets(
    tmp_path: Path,
) -> None:
    store = seeded_store_with_btc_market(
        tmp_path,
        start_price=None,
        end_price=None,
    )
    older = UTC_START - timedelta(minutes=5)
    newest = UTC_START + timedelta(minutes=5)
    store.upsert_contract_specs(
        (
            _contract_at("older", older, "UP", "older-up-token", ">="),
            _contract_at("older", older, "DOWN", "older-down-token", "<"),
            _contract_at("newest", newest, "UP", "newest-up-token", ">="),
            _contract_at("newest", newest, "DOWN", "newest-down-token", "<"),
        )
    )
    requested_condition_ids: list[str] = []

    def payload_source(condition_id: str) -> dict[str, object]:
        requested_condition_ids.append(condition_id)
        return {
            "closed": True,
            "tokens": [
                {
                    "token_id": "newest-up-token",
                    "outcome": "Up",
                    "winner": True,
                },
                {
                    "token_id": "newest-down-token",
                    "outcome": "Down",
                    "winner": False,
                },
            ],
        }

    written = upsert_official_market_outcomes(
        store=store,
        asof_ts=newest + timedelta(minutes=5, seconds=1),
        market_payload_source=payload_source,
        max_markets=1,
    )

    assert written == 1
    assert requested_condition_ids == ["0xnewest"]
    newest_market_id = f"newest-updown-5m-{int((newest + timedelta(minutes=5)).timestamp())}"
    assert fetch_outcome(store.db_path, newest_market_id)["official_winner"] == "UP"


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
            select market_id, computed_winner, official_winner, winning_token_id,
                   official_resolution_status, official_label_source, mismatch
            from validation.market_outcome_history
            where market_id = ?
            """,
            [market_id],
        ).fetchone()
    assert row is not None
    return {
        "market_id": row[0],
        "computed_winner": row[1],
        "official_winner": row[2],
        "winning_token_id": row[3],
        "official_resolution_status": row[4],
        "official_label_source": row[5],
        "mismatch": row[6],
    }


def _polymarket_market_payload(*, winning_token_id: str) -> dict[str, object]:
    return {
        "closed": True,
        "tokens": [
            {"token_id": "up-token", "outcome": "Up", "winner": winning_token_id == "up-token"},
            {
                "token_id": "down-token",
                "outcome": "Down",
                "winner": winning_token_id == "down-token",
            },
        ],
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


def _contract_at(
    suffix: str,
    start_ts: datetime,
    side: str,
    token_id: str,
    comparison_operator: str,
) -> ContractSpec:
    expiry_ts = start_ts + timedelta(minutes=5)
    expiry_epoch = int(expiry_ts.timestamp())
    return replace(
        _contract(side, token_id, comparison_operator),
        contract_id=f"btc-updown-5m-{expiry_epoch}:{side}",
        market_id=f"{suffix}-updown-5m-{expiry_epoch}",
        condition_id=f"0x{suffix}",
        slug=f"{suffix}-updown-5m-{expiry_epoch}",
        start_ts=start_ts,
        expiry_ts=expiry_ts,
        rule_hash=f"hash-{suffix}",
    )
