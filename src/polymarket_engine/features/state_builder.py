from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TypeVar

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import (
    DataQualityFlag,
    DecisionState,
    OrderBookObservation,
    PriceObservation,
    VolatilitySnapshot,
)
from polymarket_engine.features.asof_inputs import calculate_source_disagreement_bps, ensure_asof


class DecisionStateUnavailable(ValueError):
    pass


TObservation = TypeVar("TObservation", PriceObservation, OrderBookObservation, VolatilitySnapshot)


def validate_observation_asof(
    observation: PriceObservation | OrderBookObservation | VolatilitySnapshot,
    asof_ts: datetime,
    field_name: str,
) -> None:
    _require_utc(asof_ts, "asof_ts")
    ensure_asof(observation.event_ts, asof_ts, f"{field_name} event_ts")
    ensure_asof(observation.observed_ts, asof_ts, f"{field_name} observed_ts")


def latest_asof(
    observations: Sequence[TObservation],
    asof_ts: datetime,
) -> TObservation | None:
    _require_utc(asof_ts, "asof_ts")
    allowed = [
        observation
        for observation in observations
        if observation.event_ts <= asof_ts and observation.observed_ts <= asof_ts
    ]
    if not allowed:
        return None
    return max(allowed, key=lambda observation: (observation.event_ts, observation.observed_ts))


def build_decision_state(
    *,
    contract: ContractSpec,
    asof_ts: datetime,
    settlement_prices: Sequence[PriceObservation],
    proxy_prices: Sequence[PriceObservation],
    orderbooks: Sequence[OrderBookObservation],
    volatility: VolatilitySnapshot | None,
    resolved_threshold_price: float | None = None,
    threshold_observation: PriceObservation | None = None,
    stale_source_after_ms: int = 2_000,
    stale_book_after_ms: int = 2_000,
    source_disagreement_block_bps: float = 10.0,
) -> DecisionState:
    _require_utc(asof_ts, "asof_ts")
    if asof_ts < contract.start_ts:
        raise DecisionStateUnavailable("asof_ts before contract start")
    ensure_asof(asof_ts, contract.expiry_ts, "asof_ts")
    threshold = _threshold(
        contract,
        resolved_threshold_price=resolved_threshold_price,
        threshold_observation=threshold_observation,
        asof_ts=asof_ts,
    )

    settlement_candidates = [
        price
        for price in settlement_prices
        if price.symbol == contract.settlement_symbol
        and _is_allowed_settlement_source(price.source_key, contract)
    ]
    settlement_price = latest_asof(settlement_candidates, asof_ts)
    if settlement_price is None:
        raise DecisionStateUnavailable("no settlement price at or before asof_ts")
    validate_observation_asof(settlement_price, asof_ts, "settlement_price")

    proxy_latest: dict[str, PriceObservation] = {}
    for proxy in proxy_prices:
        if (
            _symbol_matches_contract(proxy.symbol, contract)
            and proxy.event_ts <= asof_ts
            and proxy.observed_ts <= asof_ts
        ):
            current = proxy_latest.get(proxy.source_key)
            if current is None or (proxy.event_ts, proxy.observed_ts) > (
                current.event_ts,
                current.observed_ts,
            ):
                proxy_latest[proxy.source_key] = proxy

    book = latest_asof(
        [
            candidate
            for candidate in orderbooks
            if candidate.venue == contract.venue and candidate.token_id == contract.token_id
        ],
        asof_ts,
    )
    if book is not None:
        validate_observation_asof(book, asof_ts, "orderbook")

    if volatility is not None:
        validate_observation_asof(volatility, asof_ts, "volatility")

    proxy_price_values = {source_key: proxy.price for source_key, proxy in proxy_latest.items()}
    source_disagreement = calculate_source_disagreement_bps(
        settlement_price.price,
        list(proxy_price_values.values()),
    )

    source_event_age_ms = _age_ms(asof_ts, settlement_price.event_ts)
    source_observed_lag_ms = _age_ms(settlement_price.observed_ts, settlement_price.event_ts)
    book_age_ms = None if book is None else _age_ms(asof_ts, book.event_ts)
    book_observed_lag_ms = None if book is None else _age_ms(book.observed_ts, book.event_ts)
    flags = _flags(
        source_age_ms=source_event_age_ms,
        book_age_ms=book_age_ms,
        has_book=book is not None,
        has_complete_book=book is not None and book.best_bid is not None and book.best_ask is not None,
        source_disagreement_bps=source_disagreement,
        stale_source_after_ms=stale_source_after_ms,
        stale_book_after_ms=stale_book_after_ms,
        source_disagreement_block_bps=source_disagreement_block_bps,
        has_volatility=volatility is not None,
    )
    state_id = f"{contract.contract_id}:{asof_ts.isoformat()}"
    seconds_left = (contract.expiry_ts - asof_ts).total_seconds()

    return DecisionState(
        state_id=state_id,
        asof_ts=asof_ts,
        contract=contract,
        threshold=threshold.price,
        threshold_source_key=threshold.source_key,
        threshold_event_ts=threshold.event_ts,
        threshold_observed_ts=threshold.observed_ts,
        seconds_left=seconds_left,
        settlement_price=settlement_price.price,
        settlement_source_key=settlement_price.source_key,
        settlement_event_ts=settlement_price.event_ts,
        settlement_observed_ts=settlement_price.observed_ts,
        proxy_prices=proxy_price_values,
        source_disagreement_bps=source_disagreement,
        best_bid=None if book is None else book.best_bid,
        best_ask=None if book is None else book.best_ask,
        executable_price=None if book is None else book.best_ask,
        spread=None if book is None else book.spread,
        book_event_ts=None if book is None else book.event_ts,
        book_observed_ts=None if book is None else book.observed_ts,
        quote_age_ms=book_age_ms,
        source_age_ms=source_event_age_ms,
        source_observed_lag_ms=source_observed_lag_ms,
        book_age_ms=book_age_ms,
        book_observed_lag_ms=book_observed_lag_ms,
        realized_returns=() if volatility is None else volatility.realized_returns,
        short_realized_vol=None if volatility is None else volatility.short_realized_vol,
        medium_realized_vol=None if volatility is None else volatility.medium_realized_vol,
        long_realized_vol=None if volatility is None else volatility.long_realized_vol,
        sigma_tau=None if volatility is None else volatility.sigma_tau,
        volatility_regime=None if volatility is None else volatility.regime,
        data_quality_flags=tuple(flags),
    )


def _threshold(
    contract: ContractSpec,
    *,
    resolved_threshold_price: float | None,
    threshold_observation: PriceObservation | None,
    asof_ts: datetime,
) -> PriceObservation:
    if contract.threshold_type == "fixed_price":
        if contract.threshold_price is None:
            raise DecisionStateUnavailable("fixed threshold missing threshold_price")
        return PriceObservation(
            source_key="fixed_price",
            symbol=contract.settlement_symbol,
            event_ts=contract.start_ts,
            observed_ts=contract.start_ts,
            price=contract.threshold_price,
        )
    if threshold_observation is None:
        raise DecisionStateUnavailable("start-price contract requires threshold_observation")
    validate_observation_asof(threshold_observation, asof_ts, "threshold_observation")
    if threshold_observation.symbol != contract.settlement_symbol:
        raise DecisionStateUnavailable("threshold_observation symbol does not match contract")
    if not _is_allowed_settlement_source(threshold_observation.source_key, contract):
        raise DecisionStateUnavailable("threshold_observation source does not match contract")
    if threshold_observation.event_ts > contract.start_ts:
        raise DecisionStateUnavailable("threshold_observation event_ts is after contract start")
    if resolved_threshold_price is not None and resolved_threshold_price != threshold_observation.price:
        raise DecisionStateUnavailable("resolved_threshold_price conflicts with threshold_observation")
    return threshold_observation


def _age_ms(asof_ts: datetime, observed_ts: datetime) -> int:
    return int((asof_ts - observed_ts).total_seconds() * 1000)


def _symbol_matches_contract(symbol: str, contract: ContractSpec) -> bool:
    return _symbol_asset(symbol) == contract.asset


def _symbol_asset(symbol: str) -> str:
    normalized = symbol.upper().replace("-", "/")
    return normalized.split("/", maxsplit=1)[0]


def _flags(
    *,
    source_age_ms: int,
    book_age_ms: int | None,
    has_book: bool,
    has_complete_book: bool,
    source_disagreement_bps: float | None,
    stale_source_after_ms: int,
    stale_book_after_ms: int,
    source_disagreement_block_bps: float,
    has_volatility: bool,
) -> list[DataQualityFlag]:
    flags: list[DataQualityFlag] = []
    if source_age_ms > stale_source_after_ms:
        flags.append("stale_source")
    if not has_book:
        flags.append("missing_orderbook")
    elif not has_complete_book:
        flags.append("incomplete_orderbook")
    elif book_age_ms is not None and book_age_ms > stale_book_after_ms:
        flags.append("stale_orderbook")
    if source_disagreement_bps is not None and source_disagreement_bps > source_disagreement_block_bps:
        flags.append("source_disagreement")
    if not has_volatility:
        flags.append("missing_volatility")
    return flags


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise ValueError(f"{field_name} must be normalized to UTC")


def _is_allowed_settlement_source(source_key: str, contract: ContractSpec) -> bool:
    return source_key in {"polymarket_rtds_chainlink", contract.settlement_source_name}
