from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from polymarket_engine.probability.schema import ProbabilityInput

ProbabilityState = Literal["READY", "BLOCKED"]


@dataclass(frozen=True)
class ThresholdDiagnostics:
    contract_id: str
    market_slug: str
    asset: str
    side: str
    K: float
    K_source: str | None
    rule_hash: str
    timestamp: datetime
    previous_K: float | None
    new_K: float
    reason_for_change: str


@dataclass(frozen=True)
class ProbabilityRuntimeInput:
    probability_input: ProbabilityInput
    contract_id: str
    contract: str
    start_ts: datetime
    expiry_ts: datetime
    flags: tuple[str, ...]
    market_slug: str = ""
    volatility_regime: str | None = None
    probability_state: ProbabilityState = "READY"
    k_stable: bool = True
    threshold_diagnostics: ThresholdDiagnostics | None = None


def contract_label(*, asset: str, side: str, start_ts: datetime, expiry_ts: datetime) -> str:
    interval_minutes = max(1, round((expiry_ts - start_ts).total_seconds() / 60))
    return f"{asset} {interval_minutes}m {side}"
