from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from polymarket_engine.domain.market_state import OrderBookObservation, PriceObservation
from polymarket_engine.features.rust_decision_snapshots import (
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


def test_current_decision_states_reuse_asset_level_store_reads(tmp_path: Path) -> None:
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

    result = build_current_decision_state_snapshots(
        status_path=status_path,
        store=store,
        include_next=True,
    )

    assert result.contracts_upserted == 8
    assert result.states_written == 4
    assert len(result.unavailable) == 4
    assert store.latest_price_tick_before_calls <= 6
    assert store.latest_price_tick_calls <= 2
    assert store.price_ticks_before_calls <= 2
    assert store.latest_orderbook_snapshot_calls == 4


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


class _CountingIngestStore(DuckDbIngestStore):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self.latest_price_tick_before_calls = 0
        self.latest_price_tick_calls = 0
        self.price_ticks_before_calls = 0
        self.latest_orderbook_snapshot_calls = 0

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

    def latest_price_tick(
        self,
        *,
        source_key: str,
        symbol: str,
        asof_ts: datetime,
    ) -> PriceObservation | None:
        self.latest_price_tick_calls += 1
        return super().latest_price_tick(source_key=source_key, symbol=symbol, asof_ts=asof_ts)

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

    def latest_orderbook_snapshot(
        self,
        *,
        venue: str,
        token_id: str,
        asof_ts: datetime,
    ) -> OrderBookObservation | None:
        self.latest_orderbook_snapshot_calls += 1
        return super().latest_orderbook_snapshot(venue=venue, token_id=token_id, asof_ts=asof_ts)
