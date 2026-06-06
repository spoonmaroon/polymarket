from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from polymarket_engine.probability.schema import ProbabilityInput


@dataclass(frozen=True)
class ProbabilityRuntimeInput:
    probability_input: ProbabilityInput
    contract_id: str
    contract: str
    start_ts: datetime
    expiry_ts: datetime
    flags: tuple[str, ...]


def contract_label(*, asset: str, side: str, start_ts: datetime, expiry_ts: datetime) -> str:
    interval_minutes = max(1, round((expiry_ts - start_ts).total_seconds() / 60))
    return f"{asset} {interval_minutes}m {side}"
