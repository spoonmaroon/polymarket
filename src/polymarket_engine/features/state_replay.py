from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import DecisionState, VolatilitySnapshot
from polymarket_engine.features.state_builder import build_decision_state
from polymarket_engine.features.volatility import (
    VOLATILITY_REFERENCE_SOURCE_KEY,
    VolatilityConfig,
    build_volatility_snapshot,
)
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def build_decision_state_from_store(
    *,
    store: DuckDbIngestStore,
    contract: ContractSpec,
    asof_ts: datetime,
    resolved_threshold_price: float | None,
    settlement_source_key: str,
    proxy_source_keys: Sequence[str],
    volatility: VolatilitySnapshot | None,
    volatility_source_key: str | None = None,
    volatility_lookback_limit: int = 180,
    volatility_config: VolatilityConfig | None = None,
) -> DecisionState:
    threshold = None
    if contract.threshold_type == "start_price":
        threshold = store.latest_price_tick_before(
            source_key=settlement_source_key,
            symbol=contract.settlement_symbol,
            event_ts_lte=contract.start_ts,
            observed_ts_lte=asof_ts,
        )
    settlement = store.latest_price_tick(
        source_key=settlement_source_key,
        symbol=contract.settlement_symbol,
        asof_ts=asof_ts,
    )
    settlement_prices = () if settlement is None else (settlement,)
    proxy_prices = tuple(
        tick
        for source_key in proxy_source_keys
        if (
            tick := store.latest_price_tick(
                source_key=source_key,
                symbol=_proxy_symbol(source_key, contract),
                asof_ts=asof_ts,
            )
        )
        is not None
    )
    book = store.latest_orderbook_snapshot(
        venue=contract.venue,
        token_id=contract.token_id,
        asof_ts=asof_ts,
    )
    orderbooks = () if book is None else (book,)
    selected_volatility = volatility
    if selected_volatility is None and volatility_source_key is not None:
        if volatility_source_key != VOLATILITY_REFERENCE_SOURCE_KEY:
            raise ValueError(f"volatility_source_key must be {VOLATILITY_REFERENCE_SOURCE_KEY}")
        price_history = store.price_ticks_before(
            source_key=volatility_source_key,
            symbol=contract.settlement_symbol,
            asof_ts=asof_ts,
            limit=volatility_lookback_limit,
        )
        selected_volatility = build_volatility_snapshot(
            prices=price_history,
            asof_ts=asof_ts,
            seconds_left=(contract.expiry_ts - asof_ts).total_seconds(),
            config=volatility_config,
            symbol=contract.settlement_symbol,
        )
    return build_decision_state(
        contract=contract,
        asof_ts=asof_ts,
        resolved_threshold_price=resolved_threshold_price,
        settlement_prices=settlement_prices,
        proxy_prices=proxy_prices,
        orderbooks=orderbooks,
        volatility=selected_volatility,
        threshold_observation=threshold,
    )


def _proxy_symbol(source_key: str, contract: ContractSpec) -> str:
    if source_key == "coinbase_advanced_ws":
        return f"{contract.asset}-USD"
    if source_key == "binance_spot_ws":
        return f"{contract.asset}USDT"
    return contract.settlement_symbol
