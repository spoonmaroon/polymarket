from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import pytest

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import DecisionState
from polymarket_engine.domain.market_state import OrderBookObservation, PriceObservation
from polymarket_engine.features import volatility as volatility_module
from polymarket_engine.features.rust_decision_snapshots import (
    CurrentDecisionStateReadCache,
    build_current_decision_state_snapshots,
)
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def test_builds_current_decision_states_from_rust_status_and_normalized_rows(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    status_path = tmp_path / "status.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = datetime(2026, 6, 2, 6, 2, tzinfo=timezone.utc)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            start_ts,
            start_ts,
            70_000.0,
        )
    )
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            asof_ts,
            asof_ts,
            70_125.0,
        )
    )
    for token_id, bid, ask in (("up-token", 0.61, 0.64), ("down-token", 0.36, 0.39)):
        store.insert_orderbook_snapshot(
            OrderBookObservation(
                venue="polymarket",
                contract_id="0xcondition",
                token_id=token_id,
                event_ts=asof_ts,
                observed_ts=asof_ts,
                best_bid=bid,
                best_ask=ask,
                bid_size_top=50.0,
                ask_size_top=40.0,
                spread=ask - bid,
                depth_json="{}",
            )
        )

    result = build_current_decision_state_snapshots(status_path=status_path, store=store)

    assert result.contracts_upserted == 2
    assert result.states_written == 2
    assert result.unavailable == ()
    with duckdb.connect(str(db_path), read_only=True) as conn:
        contract_rows = conn.execute(
            "select slug, side, token_id, threshold_type from core.contracts order by side"
        ).fetchall()
        state_rows = conn.execute(
            """
            select asset, side, threshold, settlement_price, best_ask
            from features.asof_state_inputs
            order by side
            """
        ).fetchall()
    assert contract_rows == [
        ("btc-updown-5m-1780380000", "DOWN", "down-token", "start_price"),
        ("btc-updown-5m-1780380000", "UP", "up-token", "start_price"),
    ]
    assert state_rows == [
        ("BTC", "DOWN", 70_000.0, 70_125.0, 0.39),
        ("BTC", "UP", 70_000.0, 70_125.0, 0.64),
    ]


def test_reports_unavailable_current_state_when_threshold_tick_is_missing(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    status_path = tmp_path / "status.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = datetime(2026, 6, 2, 6, 2, tzinfo=timezone.utc)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            asof_ts,
            asof_ts,
            70_125.0,
        )
    )

    result = build_current_decision_state_snapshots(status_path=status_path, store=store)

    assert result.contracts_upserted == 2
    assert result.states_written == 0
    assert len(result.unavailable) == 2
    assert all("start-price contract requires" in row.reason for row in result.unavailable)


def test_current_decision_states_use_live_health_freshness_thresholds(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    status_path = tmp_path / "status.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = datetime(2026, 6, 2, 6, 2, tzinfo=timezone.utc)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            start_ts,
            start_ts,
            70_000.0,
        )
    )
    store.insert_price_tick(
        PriceObservation(
            "polymarket_rtds_chainlink",
            "BTC/USD",
            asof_ts - timedelta(seconds=5),
            asof_ts - timedelta(seconds=4),
            70_125.0,
        )
    )
    for token_id, bid, ask in (("up-token", 0.61, 0.64), ("down-token", 0.36, 0.39)):
        store.insert_orderbook_snapshot(
            OrderBookObservation(
                venue="polymarket",
                contract_id="0xcondition",
                token_id=token_id,
                event_ts=asof_ts - timedelta(seconds=3),
                observed_ts=asof_ts - timedelta(seconds=2),
                best_bid=bid,
                best_ask=ask,
                bid_size_top=50.0,
                ask_size_top=40.0,
                spread=ask - bid,
                depth_json="{}",
            )
        )

    result = build_current_decision_state_snapshots(status_path=status_path, store=store)

    assert result.states_written == 2
    with duckdb.connect(str(db_path), read_only=True) as conn:
        rows = conn.execute(
            "select data_quality_flags_json from features.asof_state_inputs"
        ).fetchall()
    flags = [set(json.loads(row[0])) for row in rows]
    assert all("stale_source" not in row for row in flags)
    assert all("stale_orderbook" not in row for row in flags)


def test_current_decision_states_reuse_asset_level_store_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "state.duckdb"
    store = _CountingIngestStore(db_path)
    store.apply_schema()
    status_path = tmp_path / "status.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_multi_asset_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    for asset, base_price in (("BTC", 70_000.0), ("ETH", 2_000.0)):
        symbol = f"{asset}/USD"
        store.insert_price_tick(
            PriceObservation(
                "polymarket_rtds_chainlink",
                symbol,
                start_ts,
                start_ts,
                base_price,
            )
        )
        store.insert_price_tick(
            PriceObservation(
                "polymarket_rtds_chainlink",
                symbol,
                asof_ts,
                asof_ts,
                base_price + 10.0,
            )
        )
    for asset in ("BTC", "ETH"):
        for window in ("current", "next"):
            for side, bid, ask in (("up", 0.61, 0.64), ("down", 0.36, 0.39)):
                token_id = f"{asset.lower()}-{window}-{side}-token"
                store.insert_orderbook_snapshot(
                    OrderBookObservation(
                        venue="polymarket",
                        contract_id=f"0x{asset.lower()}{window}",
                        token_id=token_id,
                        event_ts=asof_ts,
                        observed_ts=asof_ts,
                        best_bid=bid,
                        best_ask=ask,
                        bid_size_top=50.0,
                        ask_size_top=40.0,
                        spread=ask - bid,
                        depth_json="{}",
                    )
                )
    real_build_volatility_snapshot = volatility_module.build_volatility_snapshot
    volatility_snapshot_calls = 0

    def counting_build_volatility_snapshot(*args: Any, **kwargs: Any) -> Any:
        nonlocal volatility_snapshot_calls
        volatility_snapshot_calls += 1
        return real_build_volatility_snapshot(*args, **kwargs)

    monkeypatch.setattr(
        volatility_module,
        "build_volatility_snapshot",
        counting_build_volatility_snapshot,
    )

    result = build_current_decision_state_snapshots(
        status_path=status_path,
        store=store,
        include_next=True,
    )

    assert result.contracts_upserted == 8
    assert result.states_written == 4
    assert result.unavailable == ()
    assert store.latest_price_ticks_before_calls == 1
    assert store.latest_price_tick_before_calls == 0
    assert store.latest_price_ticks_calls == 1
    assert store.latest_price_tick_calls == 0
    assert store.price_ticks_before_by_symbol_calls == 1
    assert store.price_ticks_before_calls == 0
    assert store.latest_orderbook_snapshots_calls == 1
    assert store.latest_orderbook_snapshot_calls == 0
    assert store.upsert_contract_specs_calls == 1
    assert store.upsert_contract_spec_calls == 0
    assert store.upsert_asof_state_inputs_calls == 1
    assert store.upsert_asof_state_input_calls == 0
    assert volatility_snapshot_calls == 2


def test_include_next_skips_prestart_decision_state_builds(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = _CountingIngestStore(db_path)
    store.apply_schema()
    status_path = tmp_path / "status.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_multi_asset_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    for asset, base_price in (("BTC", 70_000.0), ("ETH", 2_000.0)):
        symbol = f"{asset}/USD"
        store.insert_price_tick(
            PriceObservation(
                "polymarket_rtds_chainlink",
                symbol,
                start_ts,
                start_ts,
                base_price,
            )
        )
        store.insert_price_tick(
            PriceObservation(
                "polymarket_rtds_chainlink",
                symbol,
                asof_ts,
                asof_ts,
                base_price + 10.0,
            )
        )
    for asset in ("BTC", "ETH"):
        for side, bid, ask in (("up", 0.61, 0.64), ("down", 0.36, 0.39)):
            token_id = f"{asset.lower()}-current-{side}-token"
            store.insert_orderbook_snapshot(
                OrderBookObservation(
                    venue="polymarket",
                    contract_id=f"0x{asset.lower()}current",
                    token_id=token_id,
                    event_ts=asof_ts,
                    observed_ts=asof_ts,
                    best_bid=bid,
                    best_ask=ask,
                    bid_size_top=50.0,
                    ask_size_top=40.0,
                    spread=ask - bid,
                    depth_json="{}",
                )
            )

    result = build_current_decision_state_snapshots(
        status_path=status_path,
        store=store,
        include_next=True,
    )

    assert result.contracts_upserted == 8
    assert result.states_written == 4
    assert result.unavailable == ()
    assert store.latest_orderbook_token_ids_requests == [
        (
            "btc-current-up-token",
            "btc-current-down-token",
            "eth-current-up-token",
            "eth-current-down-token",
        )
    ]


def test_current_decision_states_use_status_orderbooks_without_duckdb_lookup(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.duckdb"
    store = _CountingIngestStore(db_path)
    store.apply_schema()
    status_path = tmp_path / "status.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_multi_asset_status_with_orderbooks(
        status_path,
        start_ts=start_ts,
        asof_ts=asof_ts,
    )
    for asset, base_price in (("BTC", 70_000.0), ("ETH", 2_000.0)):
        symbol = f"{asset}/USD"
        store.insert_price_tick(
            PriceObservation(
                "polymarket_rtds_chainlink",
                symbol,
                start_ts,
                start_ts,
                base_price,
            )
        )
        store.insert_price_tick(
            PriceObservation(
                "polymarket_rtds_chainlink",
                symbol,
                asof_ts,
                asof_ts,
                base_price + 10.0,
            )
        )

    result = build_current_decision_state_snapshots(
        status_path=status_path,
        store=store,
        include_next=True,
    )

    assert result.contracts_upserted == 8
    assert result.states_written == 4
    assert result.unavailable == ()
    assert store.latest_orderbook_snapshots_calls == 0
    with duckdb.connect(str(db_path), read_only=True) as conn:
        rows = conn.execute(
            """
            select asset, side, best_bid, best_ask, spread
            from features.asof_state_inputs
            order by asset, side
            """
        ).fetchall()
    assert rows == [
        ("BTC", "DOWN", 0.36, 0.39, 0.03),
        ("BTC", "UP", 0.61, 0.64, 0.03),
        ("ETH", "DOWN", 0.36, 0.39, 0.03),
        ("ETH", "UP", 0.61, 0.64, 0.03),
    ]


def test_current_decision_states_use_status_chainlink_prices_without_duckdb_lookup(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.duckdb"
    store = _CountingIngestStore(db_path)
    store.apply_schema()
    status_path = tmp_path / "status.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_multi_asset_status_with_prices_and_orderbooks(
        status_path,
        start_ts=start_ts,
        asof_ts=asof_ts,
    )
    for asset, base_price in (("BTC", 70_000.0), ("ETH", 2_000.0)):
        store.insert_price_tick(
            PriceObservation(
                "polymarket_rtds_chainlink",
                f"{asset}/USD",
                start_ts,
                start_ts,
                base_price,
            )
        )

    result = build_current_decision_state_snapshots(
        status_path=status_path,
        store=store,
        include_next=True,
    )

    assert result.contracts_upserted == 8
    assert result.states_written == 4
    assert result.unavailable == ()
    assert store.latest_price_ticks_calls == 0
    assert store.latest_price_tick_calls == 0
    with duckdb.connect(str(db_path), read_only=True) as conn:
        rows = conn.execute(
            """
            select asset, side, threshold, settlement_price
            from features.asof_state_inputs
            order by asset, side
            """
        ).fetchall()
    assert rows == [
        ("BTC", "DOWN", 70_000.0, 70_123.0),
        ("BTC", "UP", 70_000.0, 70_123.0),
        ("ETH", "DOWN", 2_000.0, 2_123.0),
        ("ETH", "UP", 2_000.0, 2_123.0),
    ]


def test_current_decision_states_reuse_threshold_and_history_cache_across_status_only_builds(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.duckdb"
    store = _CountingIngestStore(db_path)
    store.apply_schema()
    first_status_path = tmp_path / "status-first.json"
    second_status_path = tmp_path / "status-second.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    first_asof_ts = start_ts + timedelta(minutes=2)
    second_asof_ts = first_asof_ts + timedelta(seconds=1)
    _write_multi_asset_status_with_prices_and_orderbooks(
        first_status_path,
        start_ts=start_ts,
        asof_ts=first_asof_ts,
    )
    _write_multi_asset_status_with_prices_and_orderbooks(
        second_status_path,
        start_ts=start_ts,
        asof_ts=second_asof_ts,
    )
    for asset, base_price in (("BTC", 70_000.0), ("ETH", 2_000.0)):
        store.insert_price_tick(
            PriceObservation(
                "polymarket_rtds_chainlink",
                f"{asset}/USD",
                start_ts,
                start_ts,
                base_price,
            )
        )
    read_cache = CurrentDecisionStateReadCache()

    first = build_current_decision_state_snapshots(
        status_path=first_status_path,
        store=store,
        include_next=True,
        read_cache=read_cache,
    )
    second = build_current_decision_state_snapshots(
        status_path=second_status_path,
        store=store,
        include_next=True,
        read_cache=read_cache,
    )

    assert first.states_written == 4
    assert second.states_written == 4
    assert store.latest_price_ticks_before_calls == 1
    assert store.price_ticks_before_by_symbol_calls == 1


def _write_status(path: Path, *, start_ts: datetime, asof_ts: datetime) -> None:
    end_ts = start_ts.replace(minute=start_ts.minute + 5)
    slug = f"btc-updown-5m-{int(start_ts.timestamp())}"
    payload = {
        "schema_version": "rust-live-probe-state-manager-v1",
        "mode": "state-manager",
        "generated_at": asof_ts.isoformat(),
        "current": [
            {
                "window": {
                    "asset": "BTC",
                    "interval": "5m",
                    "start_ts": start_ts.isoformat(),
                    "end_ts": end_ts.isoformat(),
                },
                "up": {"asset": "BTC", "side": "Up", "token_id": "up-token"},
                "down": {"asset": "BTC", "side": "Down", "token_id": "down-token"},
            }
        ],
        "orderbooks": [
            {
                "venue": "polymarket",
                "source_key": "polymarket_rust_sdk",
                "market_slug": slug,
                "contract_id": "0xcondition",
                "token_id": "up-token",
                "asset": "BTC",
                "side": "UP",
            },
            {
                "venue": "polymarket",
                "source_key": "polymarket_rust_sdk",
                "market_slug": slug,
                "contract_id": "0xcondition",
                "token_id": "down-token",
                "asset": "BTC",
                "side": "DOWN",
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_multi_asset_status(path: Path, *, start_ts: datetime, asof_ts: datetime) -> None:
    payload = {
        "schema_version": "rust-live-probe-state-manager-v1",
        "mode": "state-manager",
        "generated_at": asof_ts.isoformat(),
        "current": _status_windows(start_ts=start_ts, window_offset_minutes=0),
        "next": _status_windows(start_ts=start_ts, window_offset_minutes=5),
        "orderbooks": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_multi_asset_status_with_orderbooks(
    path: Path,
    *,
    start_ts: datetime,
    asof_ts: datetime,
) -> None:
    payload = {
        "schema_version": "rust-live-probe-state-manager-v1",
        "mode": "state-manager",
        "generated_at": asof_ts.isoformat(),
        "current": _status_windows(start_ts=start_ts, window_offset_minutes=0),
        "next": _status_windows(start_ts=start_ts, window_offset_minutes=5),
        "orderbooks": [
            _status_orderbook_row(
                asset=asset,
                side=side,
                token_id=f"{asset.lower()}-current-{side.lower()}-token",
                contract_id=f"0x{asset.lower()}current",
                asof_ts=asof_ts,
                best_bid=bid,
                best_ask=ask,
            )
            for asset in ("BTC", "ETH")
            for side, bid, ask in (("UP", 0.61, 0.64), ("DOWN", 0.36, 0.39))
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_multi_asset_status_with_prices_and_orderbooks(
    path: Path,
    *,
    start_ts: datetime,
    asof_ts: datetime,
) -> None:
    payload = {
        "schema_version": "rust-live-probe-state-manager-v1",
        "mode": "state-manager",
        "generated_at": asof_ts.isoformat(),
        "current": _status_windows(start_ts=start_ts, window_offset_minutes=0),
        "next": _status_windows(start_ts=start_ts, window_offset_minutes=5),
        "chainlink_prices": [
            _status_price_row(asset=asset, asof_ts=asof_ts, price=base_price + 123.0)
            for asset, base_price in (("BTC", 70_000.0), ("ETH", 2_000.0))
        ],
        "orderbooks": [
            _status_orderbook_row(
                asset=asset,
                side=side,
                token_id=f"{asset.lower()}-current-{side.lower()}-token",
                contract_id=f"0x{asset.lower()}current",
                asof_ts=asof_ts,
                best_bid=bid,
                best_ask=ask,
            )
            for asset in ("BTC", "ETH")
            for side, bid, ask in (("UP", 0.61, 0.64), ("DOWN", 0.36, 0.39))
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _status_windows(*, start_ts: datetime, window_offset_minutes: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    window_start = start_ts + timedelta(minutes=window_offset_minutes)
    window_end = window_start + timedelta(minutes=5)
    window_name = "current" if window_offset_minutes == 0 else "next"
    for asset in ("BTC", "ETH"):
        asset_lower = asset.lower()
        rows.append(
            {
                "window": {
                    "asset": asset,
                    "interval": "5m",
                    "start_ts": window_start.isoformat(),
                    "end_ts": window_end.isoformat(),
                },
                "up": {
                    "asset": asset,
                    "side": "Up",
                    "token_id": f"{asset_lower}-{window_name}-up-token",
                },
                "down": {
                    "asset": asset,
                    "side": "Down",
                    "token_id": f"{asset_lower}-{window_name}-down-token",
                },
            }
        )
    return rows


def _status_orderbook_row(
    *,
    asset: str,
    side: str,
    token_id: str,
    contract_id: str,
    asof_ts: datetime,
    best_bid: float,
    best_ask: float,
) -> dict[str, object]:
    return {
        "venue": "polymarket",
        "source_key": "polymarket_rust_sdk",
        "market_slug": f"{asset.lower()}-updown-5m-1780380000",
        "contract_id": contract_id,
        "token_id": token_id,
        "asset": asset,
        "side": side,
        "event_ts": asof_ts.isoformat(),
        "observed_ts": asof_ts.isoformat(),
        "best_bid": str(best_bid),
        "best_ask": str(best_ask),
        "bid_size_top": "50",
        "ask_size_top": "40",
        "spread": str(round(best_ask - best_bid, 10)),
        "bids": [{"price": str(best_bid), "size": "50"}],
        "asks": [{"price": str(best_ask), "size": "40"}],
    }


def _status_price_row(*, asset: str, asof_ts: datetime, price: float) -> dict[str, object]:
    return {
        "source_key": "polymarket_rtds_chainlink",
        "symbol": f"{asset}/USD",
        "event_ts": asof_ts.isoformat(),
        "observed_ts": asof_ts.isoformat(),
        "price": str(price),
    }


class _CountingIngestStore(DuckDbIngestStore):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self.latest_price_tick_before_calls = 0
        self.latest_price_ticks_before_calls = 0
        self.latest_price_tick_calls = 0
        self.latest_price_ticks_calls = 0
        self.price_ticks_before_calls = 0
        self.price_ticks_before_by_symbol_calls = 0
        self.latest_orderbook_snapshots_calls = 0
        self.latest_orderbook_token_ids_requests: list[tuple[str, ...]] = []
        self.latest_orderbook_snapshot_calls = 0
        self.upsert_contract_specs_calls = 0
        self.upsert_contract_spec_calls = 0
        self.upsert_asof_state_inputs_calls = 0
        self.upsert_asof_state_input_calls = 0

    def upsert_contract_specs(self, contracts: Sequence[ContractSpec]) -> None:
        self.upsert_contract_specs_calls += 1
        super().upsert_contract_specs(contracts)

    def upsert_contract_spec(self, contract: ContractSpec) -> None:
        self.upsert_contract_spec_calls += 1
        super().upsert_contract_spec(contract)

    def upsert_asof_state_inputs(self, states: Sequence[DecisionState]) -> None:
        self.upsert_asof_state_inputs_calls += 1
        super().upsert_asof_state_inputs(states)

    def upsert_asof_state_input(self, state: DecisionState) -> None:
        self.upsert_asof_state_input_calls += 1
        super().upsert_asof_state_input(state)

    def latest_price_tick_before(
        self,
        *,
        source_key: str,
        symbol: str,
        event_ts_lte: datetime,
        observed_ts_lte: datetime,
    ) -> PriceObservation | None:
        self.latest_price_tick_before_calls += 1
        return super().latest_price_tick_before(
            source_key=source_key,
            symbol=symbol,
            event_ts_lte=event_ts_lte,
            observed_ts_lte=observed_ts_lte,
        )

    def latest_price_ticks_before(
        self,
        *,
        source_key: str,
        symbols: Sequence[str],
        event_ts_lte: datetime,
        observed_ts_lte: datetime,
    ) -> dict[str, PriceObservation]:
        self.latest_price_ticks_before_calls += 1
        return super().latest_price_ticks_before(
            source_key=source_key,
            symbols=symbols,
            event_ts_lte=event_ts_lte,
            observed_ts_lte=observed_ts_lte,
        )

    def latest_price_tick(
        self,
        *,
        source_key: str,
        symbol: str,
        asof_ts: datetime,
    ) -> PriceObservation | None:
        self.latest_price_tick_calls += 1
        return super().latest_price_tick(source_key=source_key, symbol=symbol, asof_ts=asof_ts)

    def latest_price_ticks(
        self,
        *,
        source_key: str,
        symbols: Sequence[str],
        asof_ts: datetime,
    ) -> dict[str, PriceObservation]:
        self.latest_price_ticks_calls += 1
        return super().latest_price_ticks(
            source_key=source_key,
            symbols=symbols,
            asof_ts=asof_ts,
        )

    def price_ticks_before(
        self,
        *,
        source_key: str,
        symbol: str,
        asof_ts: datetime,
        limit: int,
    ) -> tuple[PriceObservation, ...]:
        self.price_ticks_before_calls += 1
        return super().price_ticks_before(
            source_key=source_key,
            symbol=symbol,
            asof_ts=asof_ts,
            limit=limit,
        )

    def price_ticks_before_by_symbol(
        self,
        *,
        source_key: str,
        symbols: Sequence[str],
        asof_ts: datetime,
        limit: int,
    ) -> dict[str, tuple[PriceObservation, ...]]:
        self.price_ticks_before_by_symbol_calls += 1
        return super().price_ticks_before_by_symbol(
            source_key=source_key,
            symbols=symbols,
            asof_ts=asof_ts,
            limit=limit,
        )

    def latest_orderbook_snapshot(
        self,
        *,
        venue: str,
        token_id: str,
        asof_ts: datetime,
    ) -> OrderBookObservation | None:
        self.latest_orderbook_snapshot_calls += 1
        return super().latest_orderbook_snapshot(venue=venue, token_id=token_id, asof_ts=asof_ts)

    def latest_orderbook_snapshots(
        self,
        *,
        venue: str,
        token_ids: Sequence[str],
        asof_ts: datetime,
    ) -> dict[str, OrderBookObservation]:
        self.latest_orderbook_snapshots_calls += 1
        self.latest_orderbook_token_ids_requests.append(tuple(token_ids))
        return super().latest_orderbook_snapshots(
            venue=venue,
            token_ids=token_ids,
            asof_ts=asof_ts,
        )
