from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from polymarket_engine.domain.contracts import Asset, ContractSide, ContractSpec
from polymarket_engine.domain.market_state import (
    DecisionState,
    OrderBookObservation,
    PriceObservation,
    VolatilitySnapshot,
)
from polymarket_engine.features import volatility as volatility_module
from polymarket_engine.features.state_builder import DecisionStateUnavailable
from polymarket_engine.features.state_replay import build_decision_state_from_store
from polymarket_engine.probability.generator_fragments import GeneratorFragment
from polymarket_engine.probability.generator_fragments import write_probability_fragments
from polymarket_engine.probability.hot_inputs import write_hot_probability_inputs
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


SETTLEMENT_SOURCE_KEY = "polymarket_rtds_chainlink"
LIVE_HEALTH_FRESHNESS_MS = 30_000
VOLATILITY_LOOKBACK_LIMIT = 180
DEFAULT_FRAGMENT_MAX_ROWS = 250_000


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


@dataclass
class CurrentDecisionStateReadCache:
    threshold_price_before: dict[
        tuple[str, str, datetime],
        PriceObservation | None,
    ] = field(default_factory=dict)
    price_history: dict[
        tuple[str, str, datetime, int],
        tuple[PriceObservation, ...],
    ] = field(default_factory=dict)

    def clear(self) -> None:
        self.threshold_price_before.clear()
        self.price_history.clear()


def hot_state_signature(payload: dict[str, Any]) -> str:
    semantic = {
        "current": payload.get("current", []),
        "next": payload.get("next", []),
        "orderbooks": payload.get("orderbooks", []),
        "chainlink_prices": payload.get("chainlink_prices", []),
        "prices": payload.get("prices", []),
        "websocket_status": _stable_websocket_status(
            payload.get("websocket_status", {})
        ),
    }
    encoded = json.dumps(semantic, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _stable_websocket_status(value: object) -> object:
    if isinstance(value, list):
        return [_stable_websocket_status(row) for row in value]
    if isinstance(value, dict):
        return {
            key: _stable_websocket_status(item)
            for key, item in value.items()
            if not _volatile_status_key(key)
        }
    return value


def _volatile_status_key(key: str) -> bool:
    normalized = key.lower()
    return (
        normalized.endswith("_age_ms")
        or normalized.endswith("_lag_ms")
        or normalized.endswith("_elapsed_ms")
        or normalized in {"age_ms", "lag_ms", "elapsed_ms"}
    )


def build_current_decision_state_snapshots(
    *,
    status_path: Path,
    store: DuckDbIngestStore,
    include_next: bool = False,
    read_cache: CurrentDecisionStateReadCache | None = None,
    probability_inputs_path: Path | None = None,
    probability_fragments_path: Path | None = None,
    fragment_max_rows: int = DEFAULT_FRAGMENT_MAX_ROWS,
) -> CurrentDecisionStateSnapshotResult:
    payload = _read_status(status_path)
    asof_ts = _parse_ts(payload["generated_at"])
    token_metadata = _token_metadata(payload.get("orderbooks", []))
    status_prices = _prices_from_status(_price_rows_from_status(payload))
    status_orderbooks = _orderbooks_from_status(payload.get("orderbooks", []))
    contracts = _contracts_from_status(
        payload,
        token_metadata=token_metadata,
        include_next=include_next,
    )
    state_contracts = tuple(contract for contract in contracts if contract.start_ts <= asof_ts)
    read_store = _CachedStateReadStore(store, read_cache=read_cache)
    read_store.prime_threshold_prices(
        state_contracts,
        source_key=SETTLEMENT_SOURCE_KEY,
        asof_ts=asof_ts,
    )
    read_store.seed_latest_prices(
        status_prices,
        source_key=SETTLEMENT_SOURCE_KEY,
        asof_ts=asof_ts,
    )
    read_store.prime_latest_prices(
        state_contracts,
        source_key=SETTLEMENT_SOURCE_KEY,
        asof_ts=asof_ts,
    )
    read_store.prime_price_histories(
        state_contracts,
        source_key=SETTLEMENT_SOURCE_KEY,
        asof_ts=asof_ts,
        limit=VOLATILITY_LOOKBACK_LIMIT,
    )
    read_store.seed_latest_orderbooks(status_orderbooks, asof_ts=asof_ts)
    read_store.prime_latest_orderbooks(state_contracts, asof_ts=asof_ts)
    volatilities = _volatility_snapshots_for_contracts(
        state_contracts,
        read_store=read_store,
        source_key=SETTLEMENT_SOURCE_KEY,
        asof_ts=asof_ts,
        lookback_limit=VOLATILITY_LOOKBACK_LIMIT,
    )
    store.upsert_contract_specs(contracts)
    states: list[DecisionState] = []
    unavailable: list[UnavailableDecisionState] = []
    for contract in state_contracts:
        volatility = volatilities.get((contract.settlement_symbol, contract.expiry_ts))
        try:
            state = build_decision_state_from_store(
                store=cast(DuckDbIngestStore, read_store),
                contract=contract,
                asof_ts=asof_ts,
                resolved_threshold_price=None,
                settlement_source_key=SETTLEMENT_SOURCE_KEY,
                proxy_source_keys=(),
                volatility=volatility,
                volatility_source_key=SETTLEMENT_SOURCE_KEY,
                volatility_lookback_limit=VOLATILITY_LOOKBACK_LIMIT,
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
    if probability_inputs_path is not None:
        write_hot_probability_inputs(
            out_path=probability_inputs_path,
            states=states,
            generated_at=asof_ts,
        )
    if probability_fragments_path is not None:
        write_probability_fragments(
            out_path=probability_fragments_path,
            fragments=_probability_fragments_for_states(
                states,
                read_store=read_store,
                max_rows=fragment_max_rows,
            ),
            generated_at=asof_ts,
        )
    return CurrentDecisionStateSnapshotResult(
        asof_ts=asof_ts,
        contracts_upserted=len(contracts),
        states_written=len(states),
        unavailable=tuple(unavailable),
    )


def _volatility_snapshots_for_contracts(
    contracts: Sequence[ContractSpec],
    *,
    read_store: "_CachedStateReadStore",
    source_key: str,
    asof_ts: datetime,
    lookback_limit: int,
) -> dict[tuple[str, datetime], VolatilitySnapshot]:
    snapshots: dict[tuple[str, datetime], VolatilitySnapshot] = {}
    for contract in contracts:
        if asof_ts < contract.start_ts:
            continue
        key = (contract.settlement_symbol, contract.expiry_ts)
        if key in snapshots:
            continue
        price_history = read_store.price_ticks_before(
            source_key=source_key,
            symbol=contract.settlement_symbol,
            asof_ts=asof_ts,
            limit=lookback_limit,
        )
        snapshots[key] = volatility_module.build_volatility_snapshot(
            prices=price_history,
            asof_ts=asof_ts,
            seconds_left=(contract.expiry_ts - asof_ts).total_seconds(),
            symbol=contract.settlement_symbol,
        )
    return snapshots


def _probability_fragments_for_states(
    states: Sequence[DecisionState],
    *,
    read_store: "_CachedStateReadStore",
    max_rows: int,
) -> tuple[GeneratorFragment, ...]:
    if max_rows <= 0:
        raise ValueError("fragment_max_rows must be positive")
    fragments: list[GeneratorFragment] = []
    for state in states:
        if len(fragments) >= max_rows:
            break
        history = read_store.price_ticks_before(
            source_key=SETTLEMENT_SOURCE_KEY,
            symbol=state.contract.settlement_symbol,
            asof_ts=state.asof_ts,
            limit=VOLATILITY_LOOKBACK_LIMIT,
        )
        if len(history) < 2:
            continue
        first = history[0]
        last = history[-1]
        horizon_seconds = int((last.event_ts - first.event_ts).total_seconds())
        if horizon_seconds <= 0:
            continue
        fragments.append(
            GeneratorFragment(
                fragment_id=_fragment_id(state=state, first=first, last=last),
                asset=state.contract.asset,
                asof_ts=max(last.event_ts, last.observed_ts),
                prices=tuple(float(row.price) for row in history),
                horizon_seconds=horizon_seconds,
                source_key=SETTLEMENT_SOURCE_KEY,
                z_path_bucket=_z_path_bucket(state),
                quality_bucket="OK" if not state.data_quality_flags else "BLOCKED",
                metadata={
                    "state_id": state.state_id,
                    "contract_id": state.contract.contract_id,
                    "side": state.contract.side,
                    "symbol": state.contract.settlement_symbol,
                    "price_count": len(history),
                },
            )
        )
    return tuple(fragments)


def _fragment_id(
    *,
    state: DecisionState,
    first: PriceObservation,
    last: PriceObservation,
) -> str:
    digest = hashlib.sha256(
        "|".join(
            (
                state.state_id,
                first.event_ts.isoformat(),
                last.event_ts.isoformat(),
                str(first.price),
                str(last.price),
            )
        ).encode()
    ).hexdigest()
    return f"frag-{digest[:24]}"


def _z_path_bucket(state: DecisionState) -> str:
    if state.sigma_tau is None or state.sigma_tau <= 0:
        return "near"
    signed_log_distance = state.z_path if hasattr(state, "z_path") else None
    if signed_log_distance is None:
        signed_log_distance = math.log(state.settlement_price / state.threshold)
        if state.contract.side == "DOWN":
            signed_log_distance *= -1
        signed_log_distance /= state.sigma_tau
    if signed_log_distance < -1:
        return "deep_down"
    if signed_log_distance > 1:
        return "deep_up"
    return "near"


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


def _orderbooks_from_status(rows: object) -> tuple[OrderBookObservation, ...]:
    if not isinstance(rows, list):
        return ()
    orderbooks: list[OrderBookObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "event_ts" not in row or "observed_ts" not in row:
            continue
        token_id = row.get("token_id")
        contract_id = row.get("contract_id")
        if not isinstance(token_id, str) or not token_id:
            continue
        if not isinstance(contract_id, str) or not contract_id:
            continue
        orderbooks.append(
            OrderBookObservation(
                venue=str(row.get("venue") or "polymarket"),
                contract_id=contract_id,
                token_id=token_id,
                event_ts=_parse_ts(row["event_ts"]),
                observed_ts=_parse_ts(row["observed_ts"]),
                best_bid=_optional_float(row.get("best_bid")),
                best_ask=_optional_float(row.get("best_ask")),
                bid_size_top=_optional_float(row.get("bid_size_top")),
                ask_size_top=_optional_float(row.get("ask_size_top")),
                spread=_optional_float(row.get("spread")),
                depth_json=json.dumps(
                    {
                        "bids": row.get("bids") if isinstance(row.get("bids"), list) else [],
                        "asks": row.get("asks") if isinstance(row.get("asks"), list) else [],
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        )
    return tuple(orderbooks)


def _price_rows_from_status(payload: dict[str, Any]) -> tuple[object, ...]:
    rows: list[object] = []
    for key in ("chainlink_prices", "prices"):
        value = payload.get(key, [])
        if isinstance(value, list):
            rows.extend(value)
    return tuple(rows)


def _prices_from_status(rows: Sequence[object]) -> tuple[PriceObservation, ...]:
    prices: list[PriceObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if "event_ts" not in row or "observed_ts" not in row or "price" not in row:
            continue
        source_key = row.get("source_key")
        symbol = row.get("symbol")
        if not isinstance(source_key, str) or not source_key:
            continue
        if not isinstance(symbol, str) or not symbol:
            continue
        prices.append(
            PriceObservation(
                source_key=source_key,
                symbol=symbol,
                event_ts=_parse_ts(row["event_ts"]),
                observed_ts=_parse_ts(row["observed_ts"]),
                price=_required_float(row["price"], "price"),
            )
        )
    return tuple(prices)


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


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str | int | float):
        return float(value)
    raise ValueError(f"status numeric field must be string or number, got {type(value).__name__}")


def _required_float(value: object, field_name: str) -> float:
    parsed = _optional_float(value)
    if parsed is None:
        raise ValueError(f"status numeric field {field_name} is required")
    return parsed


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
    def __init__(
        self,
        store: DuckDbIngestStore,
        *,
        read_cache: CurrentDecisionStateReadCache | None,
    ) -> None:
        self._store = store
        self._read_cache = (
            CurrentDecisionStateReadCache() if read_cache is None else read_cache
        )
        self._latest_price: dict[tuple[str, str, datetime], PriceObservation | None] = {}
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
        key = (source_key, symbol, event_ts_lte)
        if key not in self._read_cache.threshold_price_before:
            self._read_cache.threshold_price_before[key] = self._store.latest_price_tick_before(
                source_key=source_key,
                symbol=symbol,
                event_ts_lte=event_ts_lte,
                observed_ts_lte=observed_ts_lte,
            )
        return self._read_cache.threshold_price_before[key]

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
            if (source_key, symbol, event_ts_lte)
            not in self._read_cache.threshold_price_before
        ]
        if missing_symbols:
            ticks = self._store.latest_price_ticks_before(
                source_key=source_key,
                symbols=missing_symbols,
                event_ts_lte=event_ts_lte,
                observed_ts_lte=observed_ts_lte,
            )
            for symbol in missing_symbols:
                self._read_cache.threshold_price_before[
                    (source_key, symbol, event_ts_lte)
                ] = ticks.get(symbol)
        return {
            symbol: tick
            for symbol in unique_symbols
            if (
                tick := self._read_cache.threshold_price_before[
                    (source_key, symbol, event_ts_lte)
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
        if key not in self._read_cache.price_history:
            self._read_cache.price_history[key] = self._store.price_ticks_before(
                source_key=source_key,
                symbol=symbol,
                asof_ts=asof_ts,
                limit=limit,
            )
        return self._read_cache.price_history[key]

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
            if (source_key, symbol, asof_ts, limit) not in self._read_cache.price_history
        ]
        if missing_symbols:
            histories = self._store.price_ticks_before_by_symbol(
                source_key=source_key,
                symbols=missing_symbols,
                asof_ts=asof_ts,
                limit=limit,
            )
            for symbol in missing_symbols:
                self._read_cache.price_history[(source_key, symbol, asof_ts, limit)] = (
                    histories.get(symbol, ())
                )
        return {
            symbol: history
            for symbol in unique_symbols
            if (
                history := self._read_cache.price_history[
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

    def seed_latest_orderbooks(
        self,
        orderbooks: Sequence[OrderBookObservation],
        *,
        asof_ts: datetime,
    ) -> None:
        for book in orderbooks:
            if book.event_ts > asof_ts or book.observed_ts > asof_ts:
                continue
            key = (book.venue, book.token_id, asof_ts)
            current = self._latest_orderbook.get(key)
            if current is None or (book.event_ts, book.observed_ts) > (
                current.event_ts,
                current.observed_ts,
            ):
                self._latest_orderbook[key] = book

    def seed_latest_prices(
        self,
        prices: Sequence[PriceObservation],
        *,
        source_key: str,
        asof_ts: datetime,
    ) -> None:
        for price in prices:
            if price.source_key != source_key:
                continue
            if price.event_ts > asof_ts or price.observed_ts > asof_ts:
                continue
            key = (price.source_key, price.symbol, asof_ts)
            current = self._latest_price.get(key)
            if current is None or (price.event_ts, price.observed_ts) > (
                current.event_ts,
                current.observed_ts,
            ):
                self._latest_price[key] = price

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
