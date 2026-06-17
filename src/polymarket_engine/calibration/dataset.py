from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CALIBRATION_DATASET_PATH = Path("data/research/calibration/asof_decision_states.jsonl")

JsonValue = str | int | float | bool | None


@dataclass(frozen=True)
class CalibrationDecisionRow:
    state_id: str
    contract_id: str
    market_slug: str
    asset: str
    side: str
    asof_ts: datetime
    expiry_ts: datetime
    tte_seconds: int
    k: float
    k_source: str
    rule_hash: str
    current_price: float
    distance_to_threshold: float
    z_path: float
    sigma_tau: float
    sigma_valid: bool
    sigma_age_ms: float
    short_realized_vol: float
    medium_realized_vol: float
    long_realized_vol: float
    volatility_regime: str
    p_finish_mc: float
    p_no_touch_mc: float
    mc_generator_dispersion: float
    spread: float
    best_bid: float
    best_ask: float
    midpoint: float
    target_size_ask_vwap: float | None
    target_size_bid_vwap: float | None
    visible_depth: float
    orderbook_imbalance: float
    quote_age_ms: float
    source_age_ms: float
    source_disagreement: float
    threshold_cross_count: int
    near_threshold_congestion: int
    recent_wick_size: float
    event_window_flag: str
    probability_model_version: str
    feature_version: str
    runtime_phase: str
    offload_allowed: bool
    skip_or_block_reason: str | None = None
    final_label: int | None = None
    resolved_outcome: str | None = None
    settlement_price_at_expiry: float | None = None

    def to_json_dict(self) -> dict[str, JsonValue]:
        return {
            "state_id": self.state_id,
            "contract_id": self.contract_id,
            "market_slug": self.market_slug,
            "asset": self.asset,
            "side": self.side,
            "asof_ts": _iso_timestamp(self.asof_ts),
            "expiry_ts": _iso_timestamp(self.expiry_ts),
            "tte_seconds": self.tte_seconds,
            "k": _finite_float_or_none(self.k),
            "k_source": self.k_source,
            "rule_hash": self.rule_hash,
            "current_price": _finite_float_or_none(self.current_price),
            "distance_to_threshold": _finite_float_or_none(self.distance_to_threshold),
            "z_path": _finite_float_or_none(self.z_path),
            "sigma_tau": _finite_float_or_none(self.sigma_tau),
            "sigma_valid": bool(self.sigma_valid),
            "sigma_age_ms": _finite_float_or_none(self.sigma_age_ms),
            "short_realized_vol": _finite_float_or_none(self.short_realized_vol),
            "medium_realized_vol": _finite_float_or_none(self.medium_realized_vol),
            "long_realized_vol": _finite_float_or_none(self.long_realized_vol),
            "volatility_regime": self.volatility_regime,
            "p_finish_mc": _finite_float_or_none(self.p_finish_mc),
            "p_no_touch_mc": _finite_float_or_none(self.p_no_touch_mc),
            "mc_generator_dispersion": _finite_float_or_none(
                self.mc_generator_dispersion
            ),
            "spread": _finite_float_or_none(self.spread),
            "best_bid": _finite_float_or_none(self.best_bid),
            "best_ask": _finite_float_or_none(self.best_ask),
            "midpoint": _finite_float_or_none(self.midpoint),
            "target_size_ask_vwap": _finite_float_or_none(self.target_size_ask_vwap),
            "target_size_bid_vwap": _finite_float_or_none(self.target_size_bid_vwap),
            "visible_depth": _finite_float_or_none(self.visible_depth),
            "orderbook_imbalance": _finite_float_or_none(self.orderbook_imbalance),
            "quote_age_ms": _finite_float_or_none(self.quote_age_ms),
            "source_age_ms": _finite_float_or_none(self.source_age_ms),
            "source_disagreement": _finite_float_or_none(self.source_disagreement),
            "threshold_cross_count": self.threshold_cross_count,
            "near_threshold_congestion": self.near_threshold_congestion,
            "recent_wick_size": _finite_float_or_none(self.recent_wick_size),
            "event_window_flag": self.event_window_flag,
            "probability_model_version": self.probability_model_version,
            "feature_version": self.feature_version,
            "runtime_phase": self.runtime_phase,
            "offload_allowed": bool(self.offload_allowed),
            "skip_or_block_reason": self.skip_or_block_reason,
            "final_label": self.final_label,
            "resolved_outcome": self.resolved_outcome,
            "settlement_price_at_expiry": _finite_float_or_none(
                self.settlement_price_at_expiry
            ),
        }


def append_calibration_rows(path: Path | str, rows: Iterable[CalibrationDecisionRow]) -> None:
    dataset_path = Path(path)
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    with dataset_path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(
                    row.to_json_dict(),
                    allow_nan=False,
                    separators=(",", ":"),
                )
            )
            handle.write("\n")


def append_calibration_row(path: Path | str, row: CalibrationDecisionRow) -> None:
    append_calibration_rows(path, (row,))


def _iso_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("calibration dataset timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


def _finite_float_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    return float(value)
