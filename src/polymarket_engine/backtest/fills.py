from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class BacktestFillConfig:
    stake_usd: float
    min_edge: float
    max_quote_age_ms: int
    fee_rate: float


@dataclass(frozen=True)
class BacktestTrade:
    state_id: str
    contract_id: str
    asset: str
    side: str
    asof_ts: str
    expiry_ts: str
    probability: float
    entry_price: float
    exit_bid_at_entry: float
    stake_usd: float
    shares: float
    final_label: int
    gross_payout: float
    fees: float
    pnl: float
    edge_at_entry: float

    def to_json_dict(self) -> dict[str, object]:
        return {
            "state_id": self.state_id,
            "contract_id": self.contract_id,
            "asset": self.asset,
            "side": self.side,
            "asof_ts": self.asof_ts,
            "expiry_ts": self.expiry_ts,
            "probability": self.probability,
            "entry_price": self.entry_price,
            "exit_bid_at_entry": self.exit_bid_at_entry,
            "stake_usd": self.stake_usd,
            "shares": self.shares,
            "final_label": self.final_label,
            "gross_payout": self.gross_payout,
            "fees": self.fees,
            "pnl": self.pnl,
            "edge_at_entry": self.edge_at_entry,
        }


def simulate_hold_to_expiry_trade(
    row: Mapping[str, object],
    config: BacktestFillConfig,
    *,
    probability_field: str,
) -> BacktestTrade | None:
    _validate_config(config)
    if row.get("skip_or_block_reason") is not None:
        return None
    quote_age_ms = _float(row.get("quote_age_ms"))
    if quote_age_ms < 0 or quote_age_ms > config.max_quote_age_ms:
        return None
    probability = _probability(row.get(probability_field), probability_field)
    entry_price = _probability(
        row.get("target_size_ask_vwap") or row.get("best_ask"),
        "target_size_ask_vwap",
    )
    exit_bid = _probability(
        row.get("target_size_bid_vwap") or row.get("best_bid"),
        "target_size_bid_vwap",
    )
    final_label = _label(row.get("final_label"))
    edge_at_entry = probability - entry_price - config.fee_rate
    if edge_at_entry < config.min_edge:
        return None
    shares = config.stake_usd / entry_price
    gross_payout = shares * float(final_label)
    fees = config.stake_usd * config.fee_rate
    pnl = gross_payout - config.stake_usd - fees
    return BacktestTrade(
        state_id=str(row["state_id"]),
        contract_id=str(row["contract_id"]),
        asset=str(row.get("asset") or ""),
        side=str(row.get("side") or ""),
        asof_ts=str(row["asof_ts"]),
        expiry_ts=str(row["expiry_ts"]),
        probability=probability,
        entry_price=entry_price,
        exit_bid_at_entry=exit_bid,
        stake_usd=config.stake_usd,
        shares=shares,
        final_label=final_label,
        gross_payout=gross_payout,
        fees=fees,
        pnl=pnl,
        edge_at_entry=edge_at_entry,
    )


def _validate_config(config: BacktestFillConfig) -> None:
    if config.stake_usd <= 0 or not math.isfinite(config.stake_usd):
        raise ValueError("stake_usd must be positive and finite")
    if config.min_edge < 0 or not math.isfinite(config.min_edge):
        raise ValueError("min_edge must be nonnegative and finite")
    if config.max_quote_age_ms < 0:
        raise ValueError("max_quote_age_ms must be nonnegative")
    if config.fee_rate < 0 or not math.isfinite(config.fee_rate):
        raise ValueError("fee_rate must be nonnegative and finite")


def _probability(value: object, field: str) -> float:
    number = _float(value)
    if number <= 0.0 or number > 1.0:
        raise ValueError(f"{field} must be in (0, 1]")
    return number


def _float(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean values are not numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("numeric field is required") from exc
    if not math.isfinite(number):
        raise ValueError("numeric field must be finite")
    return number


def _label(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    raise ValueError("final_label must be 0 or 1")
