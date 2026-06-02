from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from polymarket_engine.domain.contracts import Asset, ContractSide, ContractSpec
from polymarket_engine.domain.market_state import DecisionState, OrderBookObservation, PriceObservation
from polymarket_engine.features.state_builder import DecisionStateUnavailable
from polymarket_engine.features.state_replay import build_decision_state_from_store
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


SETTLEMENT_SOURCE_KEY = "polymarket_rtds_chainlink"
LIVE_HEALTH_FRESHNESS_MS = 30_000


@dataclass(frozen=True)
class UnavailableDecisionState:
    contract_id: str
    token_id: str
    reason: str


@dataclass(frozen=True)
class CurrentDecisionStateSnapshotResult:
    asof_ts: datetime
    contracts_upserted: int
    states_written: int
    unavailable: tuple[UnavailableDecisionState, ...]


def build_current_decision_state_snapshots(
    *,
    status_path: Path,
    store: DuckDbIngestStore,
    include_next: bool = False,
) -> CurrentDecisionStateSnapshotResult:
    payload = _read_status(status_path)
    asof_ts = _parse_ts(payload["generated_at"])
    token_metadata = _token_metadata(payload.get("orderbooks", []))
    contracts = _contracts_from_status(
        payload,
        token_metadata=token_metadata,
        include_next=include_next,
    )
    read_store = _CachedStateReadStore(store)
    read_store.prime_threshold_prices(
        contracts,
        source_key=SETTLEMENT_SOURCE_KEY,
        asof_ts=asof_ts,
    )
    read_store.prime_latest_prices(
        contracts,
        source_key=SETTLEMENT_SOURCE_KEY,
        asof_ts=asof_ts,
    )
    read_store.prime_price_histories(
        contracts,
        source_key=SETTLEMENT_SOURCE_KEY,
        asof_ts=asof_ts,
        limit=180,
    )
    read_store.prime_latest_orderbooks(contracts, asof_ts=asof_ts)
    store.upsert_contract_specs(contracts)
    states: list[DecisionState] = []
    unavailable: list[UnavailableDecisionState] = []
    for contract in contracts:
        try:
            state = build_decision_state_from_store(
                store=cast(DuckDbIngestStore, read_store),
                contract=contract,
                asof_ts=asof_ts,
                resolved_threshold_price=None,
                settlement_source_key=SETTLEMENT_SOURCE_KEY,
                proxy_source_keys=(),
                volatility=None,
                volatility_source_key=SETTLEMENT_SOURCE_KEY,
                volatility_lookback_limit=180,
                stale_source_after_ms=LIVE_HEALTH_FRESHNESS_MS,
                stale_book_after_ms=LIVE_HEALTH_FRESHNESS_MS,
            )
        except DecisionStateUnavailable as exc:
            unavailable.append(
                UnavailableDecisionState(
                    contract_id=contract.contract_id,
                    token_id=contract.token_id,
                    reason=str(exc),
                )
            )
            continue
        states.append(state)
    store.upsert_asof_state_inputs(states)
    return CurrentDecisionStateSnapshotResult(
        asof_ts=asof_ts,
        contracts_upserted=len(contracts),
        states_written=len(states),
        unavailable=tuple(unavailable),
    )


def _read_status(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("status payload must be an object")
    if payload.get("schema_version") != "rust-live-probe-state-manager-v1":
        raise ValueError("status payload is not a Rust state-manager report")
    return payload


def _contracts_from_status(
    payload: dict[str, Any],
    *,
    token_metadata: dict[str, dict[str, str]],
    include_next: bool,
) -> tuple[ContractSpec, ...]:
    groups = ["current"]
    if include_next:
        groups.append("next")
    contracts: list[ContractSpec] = []
    for group in groups:
        rows = payload.get(group, [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            window = _mapping(row.get("window"), f"{group}.window")
            asset = _asset(window["asset"])
            interval = str(window["interval"])
            start_ts = _parse_ts(window["start_ts"])
            expiry_ts = _parse_ts(window["end_ts"])
            for side_key, side in (("up", "UP"), ("down", "DOWN")):
                token = _mapping(row.get(side_key), f"{group}.{side_key}")
                token_id = str(token["token_id"])
                metadata = token_metadata.get(token_id, {})
                slug = metadata.get("market_slug") or _slug(asset, interval, start_ts)
                condition_id = metadata.get("contract_id") or f"unknown:{slug}"
                contract_id = f"{slug}:{side}"
                contracts.append(
                    ContractSpec(
                        contract_id=contract_id,
                        venue="polymarket",
                        market_id=slug,
                        condition_id=condition_id,
                        slug=slug,
                        asset=asset,
                        side=cast(ContractSide, side),
                        token_id=token_id,
                        threshold_type="start_price",
                        threshold_price=None,
                        comparison_operator=">=" if side == "UP" else "<",
                        start_ts=start_ts,
                        expiry_ts=expiry_ts,
                        settlement_source_name=SETTLEMENT_SOURCE_KEY,
                        settlement_source_url=_settlement_source_url(asset),
                        settlement_symbol=f"{asset}/USD",
                        rule_text=_rule_text(asset=asset, interval=interval, slug=slug),
                        rule_hash=_rule_hash(asset=asset, interval=interval, slug=slug),
                        parser_version="rust-state-manager-v1",
                    )
                )
    return tuple(contracts)


def _token_metadata(rows: object) -> dict[str, dict[str, str]]:
    if not isinstance(rows, list):
        return {}
    metadata: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        token_id = row.get("token_id")
        if not isinstance(token_id, str) or not token_id:
            continue
        values: dict[str, str] = {}
        for key in ("market_slug", "contract_id"):
            value = row.get(key)
            if isinstance(value, str) and value:
                values[key] = value
        metadata[token_id] = values
    return metadata


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _asset(value: object) -> Asset:
    normalized = str(value).upper()
    if normalized not in {"BTC", "ETH", "SOL"}:
        raise ValueError(f"unsupported asset: {value}")
    return cast(Asset, normalized)


def _parse_ts(value: object) -> datetime:
    raw = str(value)
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _slug(asset: Asset, interval: str, start_ts: datetime) -> str:
    return f"{asset.lower()}-updown-{interval}-{int(start_ts.timestamp())}"


def _settlement_source_url(asset: Asset) -> str:
    return f"https://data.chain.link/streams/{asset.lower()}-usd"


def _rule_text(*, asset: Asset, interval: str, slug: str) -> str:
    return (
        f"{asset} {interval} Up/Down start-price contract discovered by Rust "
        f"state-manager status: {slug}"
    )


def _rule_hash(*, asset: Asset, interval: str, slug: str) -> str:
    return hashlib.sha256(_rule_text(asset=asset, interval=interval, slug=slug).encode()).hexdigest()


class _CachedStateReadStore:
    def __init__(self, store: DuckDbIngestStore) -> None:
        self._store = store
        self._latest_price_before: dict[
            tuple[str, str, datetime, datetime],
            PriceObservation | None,
        ] = {}
        self._latest_price: dict[tuple[str, str, datetime], PriceObservation | None] = {}
        self._price_history: dict[
            tuple[str, str, datetime, int],
            tuple[PriceObservation, ...],
        ] = {}
        self._latest_orderbook: dict[
            tuple[str, str, datetime],
            OrderBookObservation | None,
        ] = {}

    def latest_price_tick_before(
        self,
        *,
        source_key: str,
        symbol: str,
        event_ts_lte: datetime,
        observed_ts_lte: datetime,
    ) -> PriceObservation | None:
        key = (source_key, symbol, event_ts_lte, observed_ts_lte)
        if key not in self._latest_price_before:
            self._latest_price_before[key] = self._store.latest_price_tick_before(
                source_key=source_key,
                symbol=symbol,
                event_ts_lte=event_ts_lte,
                observed_ts_lte=observed_ts_lte,
            )
        return self._latest_price_before[key]

    def latest_price_ticks_before(
        self,
        *,
        source_key: str,
        symbols: Sequence[str],
        event_ts_lte: datetime,
        observed_ts_lte: datetime,
    ) -> dict[str, PriceObservation]:
        unique_symbols = tuple(dict.fromkeys(symbols))
        missing_symbols = [
            symbol
            for symbol in unique_symbols
            if (source_key, symbol, event_ts_lte, observed_ts_lte)
            not in self._latest_price_before
        ]
        if missing_symbols:
            ticks = self._store.latest_price_ticks_before(
                source_key=source_key,
                symbols=missing_symbols,
                event_ts_lte=event_ts_lte,
                observed_ts_lte=observed_ts_lte,
            )
            for symbol in missing_symbols:
                self._latest_price_before[
                    (source_key, symbol, event_ts_lte, observed_ts_lte)
                ] = ticks.get(symbol)
        return {
            symbol: tick
            for symbol in unique_symbols
            if (
                tick := self._latest_price_before[
                    (source_key, symbol, event_ts_lte, observed_ts_lte)
                ]
            )
            is not None
        }

    def latest_price_tick(
        self,
        *,
        source_key: str,
        symbol: str,
        asof_ts: datetime,
    ) -> PriceObservation | None:
        key = (source_key, symbol, asof_ts)
        if key not in self._latest_price:
            self._latest_price[key] = self._store.latest_price_tick(
                source_key=source_key,
                symbol=symbol,
                asof_ts=asof_ts,
            )
        return self._latest_price[key]

    def latest_price_ticks(
        self,
        *,
        source_key: str,
        symbols: Sequence[str],
        asof_ts: datetime,
    ) -> dict[str, PriceObservation]:
        unique_symbols = tuple(dict.fromkeys(symbols))
        missing_symbols = [
            symbol
            for symbol in unique_symbols
            if (source_key, symbol, asof_ts) not in self._latest_price
        ]
        if missing_symbols:
            ticks = self._store.latest_price_ticks(
                source_key=source_key,
                symbols=missing_symbols,
                asof_ts=asof_ts,
            )
            for symbol in missing_symbols:
                self._latest_price[(source_key, symbol, asof_ts)] = ticks.get(symbol)
        return {
            symbol: tick
            for symbol in unique_symbols
            if (tick := self._latest_price[(source_key, symbol, asof_ts)]) is not None
        }

    def price_ticks_before(
        self,
        *,
        source_key: str,
        symbol: str,
        asof_ts: datetime,
        limit: int,
    ) -> tuple[PriceObservation, ...]:
        key = (source_key, symbol, asof_ts, limit)
        if key not in self._price_history:
            self._price_history[key] = self._store.price_ticks_before(
                source_key=source_key,
                symbol=symbol,
                asof_ts=asof_ts,
                limit=limit,
            )
        return self._price_history[key]

    def price_ticks_before_by_symbol(
        self,
        *,
        source_key: str,
        symbols: Sequence[str],
        asof_ts: datetime,
        limit: int,
    ) -> dict[str, tuple[PriceObservation, ...]]:
        unique_symbols = tuple(dict.fromkeys(symbols))
        missing_symbols = [
            symbol
            for symbol in unique_symbols
            if (source_key, symbol, asof_ts, limit) not in self._price_history
        ]
        if missing_symbols:
            histories = self._store.price_ticks_before_by_symbol(
                source_key=source_key,
                symbols=missing_symbols,
                asof_ts=asof_ts,
                limit=limit,
            )
            for symbol in missing_symbols:
                self._price_history[(source_key, symbol, asof_ts, limit)] = (
                    histories.get(symbol, ())
                )
        return {
            symbol: history
            for symbol in unique_symbols
            if (
                history := self._price_history[
                    (source_key, symbol, asof_ts, limit)
                ]
            )
        }

    def latest_orderbook_snapshot(
        self,
        *,
        venue: str,
        token_id: str,
        asof_ts: datetime,
    ) -> OrderBookObservation | None:
        key = (venue, token_id, asof_ts)
        if key not in self._latest_orderbook:
            self._latest_orderbook[key] = self._store.latest_orderbook_snapshot(
                venue=venue,
                token_id=token_id,
                asof_ts=asof_ts,
            )
        return self._latest_orderbook[key]

    def prime_threshold_prices(
        self,
        contracts: Sequence[ContractSpec],
        *,
        source_key: str,
        asof_ts: datetime,
    ) -> None:
        symbols_by_start_ts: dict[datetime, list[str]] = {}
        for contract in contracts:
            if contract.threshold_type != "start_price" or asof_ts < contract.start_ts:
                continue
            symbols_by_start_ts.setdefault(contract.start_ts, []).append(
                contract.settlement_symbol
            )
        for start_ts, symbols in symbols_by_start_ts.items():
            self.latest_price_ticks_before(
                source_key=source_key,
                symbols=symbols,
                event_ts_lte=start_ts,
                observed_ts_lte=asof_ts,
            )

    def prime_latest_prices(
        self,
        contracts: Sequence[ContractSpec],
        *,
        source_key: str,
        asof_ts: datetime,
    ) -> None:
        symbols = [
            contract.settlement_symbol
            for contract in contracts
            if asof_ts >= contract.start_ts
        ]
        self.latest_price_ticks(
            source_key=source_key,
            symbols=symbols,
            asof_ts=asof_ts,
        )

    def prime_price_histories(
        self,
        contracts: Sequence[ContractSpec],
        *,
        source_key: str,
        asof_ts: datetime,
        limit: int,
    ) -> None:
        symbols = [
            contract.settlement_symbol
            for contract in contracts
            if asof_ts >= contract.start_ts
        ]
        self.price_ticks_before_by_symbol(
            source_key=source_key,
            symbols=symbols,
            asof_ts=asof_ts,
            limit=limit,
        )

    def prime_latest_orderbooks(
        self,
        contracts: Sequence[ContractSpec],
        *,
        asof_ts: datetime,
    ) -> None:
        token_ids_by_venue: dict[str, list[str]] = {}
        for contract in contracts:
            if asof_ts < contract.start_ts:
                continue
            key = (contract.venue, contract.token_id, asof_ts)
            if key in self._latest_orderbook:
                continue
            token_ids_by_venue.setdefault(contract.venue, []).append(contract.token_id)
        for venue, token_ids in token_ids_by_venue.items():
            snapshots = self._store.latest_orderbook_snapshots(
                venue=venue,
                token_ids=token_ids,
                asof_ts=asof_ts,
            )
            for token_id in token_ids:
                self._latest_orderbook[(venue, token_id, asof_ts)] = snapshots.get(token_id)
