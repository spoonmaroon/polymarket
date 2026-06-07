from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from polymarket_engine.probability.wave_signal import WavePhase
from polymarket_engine.probability.wave_signal import WaveSignalInput
from polymarket_engine.probability.wave_signal import classify_wave_signal


@dataclass(frozen=True)
class FastNowcastInput:
    state_id: str
    asof_ts: datetime
    asset: Literal["BTC", "ETH"]
    side: Literal["UP", "DOWN"]
    z_path: float
    seconds_left: float
    executable_price: float | None
    sigma_tau: float | None
    source_age_ms: int
    book_age_ms: int


@dataclass(frozen=True)
class FastNowcastOutput:
    state_id: str
    asof_ts: datetime
    backend: str
    probability_kind: str
    model_version: str
    p_finish: float
    p_no_touch: float
    z_path: float
    wave_phase: WavePhase
    wave_score: float
    wave_reasons: list[str]
    wave_markers: list[str]
    dynamic_edge: float | None
    dynamic_required_edge: float | None

    def to_json_dict(self) -> dict[str, object]:
        return {
            "state_id": self.state_id,
            "asof_ts": self.asof_ts.isoformat(),
            "backend": self.backend,
            "probability_kind": self.probability_kind,
            "model_version": self.model_version,
            "p_finish": self.p_finish,
            "p_no_touch": self.p_no_touch,
            "z_path": self.z_path,
            "wave_phase": self.wave_phase,
            "wave_score": self.wave_score,
            "wave_reasons": self.wave_reasons,
            "wave_markers": self.wave_markers,
            "dynamic_edge": self.dynamic_edge,
            "dynamic_required_edge": self.dynamic_required_edge,
        }


def compute_fast_nowcast(input_: FastNowcastInput) -> FastNowcastOutput:
    p_finish = min(1.0, max(0.0, _normal_cdf(input_.z_path)))
    signal = classify_wave_signal(
        WaveSignalInput(
            p_finish=p_finish,
            p_no_touch=0.0,
            executable_price=input_.executable_price,
            edge_after_costs=None,
            required_edge=None,
            seconds_left=input_.seconds_left,
            source_age_ms=input_.source_age_ms,
            book_age_ms=input_.book_age_ms,
        )
    )
    return FastNowcastOutput(
        state_id=input_.state_id,
        asof_ts=input_.asof_ts,
        backend="analytic",
        probability_kind="NOWCAST",
        model_version="fast-nowcast-v1",
        p_finish=p_finish,
        p_no_touch=0.0,
        z_path=input_.z_path,
        wave_phase=signal["wave_phase"],
        wave_score=signal["wave_score"],
        wave_reasons=signal["wave_reasons"],
        wave_markers=signal["wave_markers"],
        dynamic_edge=signal["dynamic_edge"],
        dynamic_required_edge=signal["dynamic_required_edge"],
    )


def _normal_cdf(value: float) -> float:
    return 0.5 * math.erfc(-value / math.sqrt(2.0))
