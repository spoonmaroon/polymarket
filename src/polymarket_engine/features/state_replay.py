from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import DecisionState, VolatilitySnapshot
from polymarket_engine.features.state_builder import build_decision_state
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
) -> DecisionState:
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
                symbol=contract.settlement_symbol,
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
    return build_decision_state(
        contract=contract,
        asof_ts=asof_ts,
        resolved_threshold_price=resolved_threshold_price,
        settlement_prices=settlement_prices,
        proxy_prices=proxy_prices,
        orderbooks=orderbooks,
        volatility=volatility,
    )
