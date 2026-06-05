from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

MAX_SIMULATION_ARTIFACT_PATHS = 64
MAX_SIMULATION_ARTIFACT_POINTS = 32


@dataclass(frozen=True)
class ProbabilityEventLogRow:
    event_id: str
    output_id: str | None
    state_id: str
    contract_id: str
    market_slug: str
    asset: str
    side: str
    start_ts: datetime
    expiry_ts: datetime
    asof_ts: datetime
    probability_kind: str
    backend: str
    model_version: str
    generator_version: str | None
    cache_key: str | None
    cache_status: str | None
    p_finish: float
    p_no_touch: float
    z_path: float
    sigma_tau: float | None
    executable_price: float | None
    spread: float | None
    seconds_left: float
    wave_phase: str
    wave_score: float
    path_count: int | None
    seed: int | None
    queue_ms: float | None
    runtime_ms: float | None
    state_to_status_ms: float | None
    total_lag_ms: float | None
    generated_at: datetime
    valid_from: datetime
    valid_until: datetime
    diagnostics: dict[str, Any]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "output_id": self.output_id,
            "state_id": self.state_id,
            "contract_id": self.contract_id,
            "market_slug": self.market_slug,
            "asset": self.asset,
            "side": self.side,
            "start_ts": self.start_ts.isoformat(),
            "expiry_ts": self.expiry_ts.isoformat(),
            "asof_ts": self.asof_ts.isoformat(),
            "probability_kind": self.probability_kind,
            "backend": self.backend,
            "model_version": self.model_version,
            "generator_version": self.generator_version,
            "cache_key": self.cache_key,
            "cache_status": self.cache_status,
            "p_finish": self.p_finish,
            "p_no_touch": self.p_no_touch,
            "z_path": self.z_path,
            "sigma_tau": self.sigma_tau,
            "executable_price": self.executable_price,
            "spread": self.spread,
            "seconds_left": self.seconds_left,
            "wave_phase": self.wave_phase,
            "wave_score": self.wave_score,
            "path_count": self.path_count,
            "seed": self.seed,
            "queue_ms": self.queue_ms,
            "runtime_ms": self.runtime_ms,
            "state_to_status_ms": self.state_to_status_ms,
            "total_lag_ms": self.total_lag_ms,
            "generated_at": self.generated_at.isoformat(),
            "valid_from": self.valid_from.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "diagnostics": self.diagnostics,
        }


@dataclass(frozen=True)
class SimulationArtifactRow:
    artifact_id: str
    output_id: str | None
    state_id: str
    asof_ts: datetime
    model_version: str
    backend: str
    path_count: int
    terminal_win_count: int
    no_touch_win_count: int
    terminal_price_quantiles: dict[str, float]
    crossing_count_quantiles: dict[str, float]
    sampled_paths: Sequence[Mapping[str, Any]]
    diagnostics: dict[str, Any]

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "output_id": self.output_id,
            "state_id": self.state_id,
            "asof_ts": self.asof_ts.isoformat(),
            "model_version": self.model_version,
            "backend": self.backend,
            "path_count": self.path_count,
            "terminal_win_count": self.terminal_win_count,
            "no_touch_win_count": self.no_touch_win_count,
            "terminal_price_quantiles": self.terminal_price_quantiles,
            "crossing_count_quantiles": self.crossing_count_quantiles,
            "sampled_paths": _bounded_sampled_paths(self.sampled_paths),
            "diagnostics": self.diagnostics,
        }


def _bounded_sampled_paths(paths: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    bounded: list[dict[str, Any]] = []
    for raw_path in paths[:MAX_SIMULATION_ARTIFACT_PATHS]:
        path = dict(raw_path)
        points = path.get("points")
        if isinstance(points, Sequence) and not isinstance(points, (str, bytes)):
            path["points"] = list(points[:MAX_SIMULATION_ARTIFACT_POINTS])
        bounded.append(path)
    return bounded
