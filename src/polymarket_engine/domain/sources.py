from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourceRole(str, Enum):
    CONTRACT_METADATA = "contract_metadata"
    EXECUTABLE_MARKET = "executable_market"
    SETTLEMENT_REFERENCE = "settlement_reference"
    PRICE_PROXY = "price_proxy"
    CONTEXT_LAYER = "context_layer"


class SourceStatus(str, Enum):
    PART_ONE = "part_one"
    DEFERRED = "deferred"
    UPGRADE_PATH = "upgrade_path"


@dataclass(frozen=True)
class DataSource:
    key: str
    role: SourceRole
    status: SourceStatus
    symbols: tuple[str, ...]
    reason: str


LOCKED_SOURCES: dict[str, DataSource] = {
    "polymarket_markets": DataSource(
        key="polymarket_markets",
        role=SourceRole.CONTRACT_METADATA,
        status=SourceStatus.PART_ONE,
        symbols=("BTC", "ETH"),
        reason="Venue-defined contract object, threshold, expiry, token ids, rules, and resolution text.",
    ),
    "polymarket_clob": DataSource(
        key="polymarket_clob",
        role=SourceRole.EXECUTABLE_MARKET,
        status=SourceStatus.PART_ONE,
        symbols=("BTC", "ETH"),
        reason="Executable bid, ask, spread, and depth used for edge after costs.",
    ),
    "polymarket_market_ws": DataSource(
        key="polymarket_market_ws",
        role=SourceRole.EXECUTABLE_MARKET,
        status=SourceStatus.PART_ONE,
        symbols=("BTC", "ETH"),
        reason="Fast order book updates for short-dated binary convergence.",
    ),
    "polymarket_rtds_chainlink": DataSource(
        key="polymarket_rtds_chainlink",
        role=SourceRole.SETTLEMENT_REFERENCE,
        status=SourceStatus.PART_ONE,
        symbols=("BTC/USD", "ETH/USD"),
        reason="Venue-supported crypto price stream closest to settlement-source behavior.",
    ),
    "binance_spot_ws": DataSource(
        key="binance_spot_ws",
        role=SourceRole.PRICE_PROXY,
        status=SourceStatus.PART_ONE,
        symbols=("BTCUSDT", "ETHUSDT"),
        reason="Liquid free proxy for high-frequency price path and volatility reconstruction.",
    ),
    "coinbase_advanced_ws": DataSource(
        key="coinbase_advanced_ws",
        role=SourceRole.PRICE_PROXY,
        status=SourceStatus.PART_ONE,
        symbols=("BTC-USD", "ETH-USD"),
        reason="Independent USD proxy for source disagreement checks.",
    ),
    "etf_gex_context": DataSource(
        key="etf_gex_context",
        role=SourceRole.CONTEXT_LAYER,
        status=SourceStatus.DEFERRED,
        symbols=("IBIT", "FBTC"),
        reason="Useful as volatility/skew/risk-appetite context after core replay integrity is proven.",
    ),
    "jupiter_prediction_markets": DataSource(
        key="jupiter_prediction_markets",
        role=SourceRole.EXECUTABLE_MARKET,
        status=SourceStatus.DEFERRED,
        symbols=("BTC", "ETH", "SOL"),
        reason="Multi-venue expansion after one venue's replay and labeling are reliable.",
    ),
    "chainlink_data_streams_direct": DataSource(
        key="chainlink_data_streams_direct",
        role=SourceRole.SETTLEMENT_REFERENCE,
        status=SourceStatus.UPGRADE_PATH,
        symbols=("BTC/USD", "ETH/USD"),
        reason="Direct settlement-reference upgrade once access, latency, and history are verified.",
    ),
}


def part_one_sources() -> tuple[DataSource, ...]:
    return tuple(source for source in LOCKED_SOURCES.values() if source.status == SourceStatus.PART_ONE)
