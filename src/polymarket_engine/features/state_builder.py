from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
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
    ensure_asof(observation.event_ts, asof_ts, f"{field_name} event_ts")
    ensure_asof(observation.observed_ts, asof_ts, f"{field_name} observed_ts")


def latest_asof(
    observations: Sequence[TObservation],
    asof_ts: datetime,
) -> TObservation | None:
    allowed = [
        observation
        for observation in observations
        if observation.event_ts <= asof_ts and observation.observed_ts <= asof_ts
    ]
    if not allowed:
        return None
    return max(allowed, key=lambda observation: (observation.observed_ts, observation.event_ts))


def build_decision_state(
    *,
    contract: ContractSpec,
    asof_ts: datetime,
    resolved_threshold_price: float | None,
    settlement_prices: Sequence[PriceObservation],
    proxy_prices: Sequence[PriceObservation],
    orderbooks: Sequence[OrderBookObservation],
    volatility: VolatilitySnapshot | None,
    stale_source_after_ms: int = 2_000,
    stale_book_after_ms: int = 2_000,
    source_disagreement_block_bps: float = 10.0,
) -> DecisionState:
    ensure_asof(asof_ts, contract.expiry_ts, "asof_ts")
    threshold = _threshold(contract, resolved_threshold_price)

    settlement_candidates = [
        price
        for price in settlement_prices
        if price.symbol == contract.settlement_symbol
        and price.source_key in {"polymarket_rtds_chainlink", contract.settlement_source_name}
    ]
    settlement_price = latest_asof(settlement_candidates, asof_ts)
    if settlement_price is None:
        raise DecisionStateUnavailable("no settlement price at or before asof_ts")
    validate_observation_asof(settlement_price, asof_ts, "settlement_price")

    proxy_latest: dict[str, PriceObservation] = {}
    for proxy in proxy_prices:
        if proxy.event_ts <= asof_ts and proxy.observed_ts <= asof_ts:
            current = proxy_latest.get(proxy.source_key)
            if current is None or (proxy.observed_ts, proxy.event_ts) > (
                current.observed_ts,
                current.event_ts,
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

    source_age_ms = _age_ms(asof_ts, settlement_price.observed_ts)
    book_age_ms = None if book is None else _age_ms(asof_ts, book.observed_ts)
    flags = _flags(
        source_age_ms=source_age_ms,
        book_age_ms=book_age_ms,
        has_book=book is not None,
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
        threshold=threshold,
        seconds_left=seconds_left,
        settlement_price=settlement_price.price,
        settlement_source_key=settlement_price.source_key,
        proxy_prices=proxy_price_values,
        source_disagreement_bps=source_disagreement,
        best_bid=None if book is None else book.best_bid,
        best_ask=None if book is None else book.best_ask,
        executable_price=None if book is None else book.best_ask,
        spread=None if book is None else book.spread,
        quote_age_ms=book_age_ms,
        source_age_ms=source_age_ms,
        book_age_ms=book_age_ms,
        realized_returns=() if volatility is None else volatility.realized_returns,
        short_realized_vol=None if volatility is None else volatility.short_realized_vol,
        medium_realized_vol=None if volatility is None else volatility.medium_realized_vol,
        long_realized_vol=None if volatility is None else volatility.long_realized_vol,
        sigma_tau=None if volatility is None else volatility.sigma_tau,
        volatility_regime=None if volatility is None else volatility.regime,
        data_quality_flags=tuple(flags),
    )


def _threshold(contract: ContractSpec, resolved_threshold_price: float | None) -> float:
    if contract.threshold_type == "fixed_price":
        if contract.threshold_price is None:
            raise DecisionStateUnavailable("fixed threshold missing threshold_price")
        return contract.threshold_price
    if resolved_threshold_price is None:
        raise DecisionStateUnavailable("start-price contract requires resolved_threshold_price")
    if resolved_threshold_price <= 0:
        raise DecisionStateUnavailable("resolved_threshold_price must be positive")
    return resolved_threshold_price


def _age_ms(asof_ts: datetime, observed_ts: datetime) -> int:
    return int((asof_ts - observed_ts).total_seconds() * 1000)


def _flags(
    *,
    source_age_ms: int,
    book_age_ms: int | None,
    has_book: bool,
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
    elif book_age_ms is not None and book_age_ms > stale_book_after_ms:
        flags.append("stale_orderbook")
    if source_disagreement_bps is not None and source_disagreement_bps > source_disagreement_block_bps:
        flags.append("source_disagreement")
    if not has_volatility:
        flags.append("missing_volatility")
    return flags
