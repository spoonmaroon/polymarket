from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


def _ms_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, round((end - start).total_seconds() * 1000.0, 3))


@dataclass(frozen=True)
class ProbabilityLatencyTrace:
    state_asof_ts: datetime
    tick_observed_ts: datetime | None
    worker_received_ts: datetime | None
    mc_started_ts: datetime | None
    mc_finished_ts: datetime | None
    status_written_ts: datetime | None
    ui_seen_ts: datetime | None = None

    def queue_ms(self) -> float | None:
        return _ms_between(self.worker_received_ts, self.mc_started_ts)

    def runtime_ms(self) -> float | None:
        return _ms_between(self.mc_started_ts, self.mc_finished_ts)

    def state_to_status_ms(self) -> float | None:
        return _ms_between(self.state_asof_ts, self.status_written_ts)

    def total_lag_ms(self) -> float | None:
        return _ms_between(self.state_asof_ts, self.ui_seen_ts or self.status_written_ts)

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "state_asof_ts": self.state_asof_ts.isoformat(),
            "tick_observed_ts": _optional_isoformat(self.tick_observed_ts),
            "worker_received_ts": _optional_isoformat(self.worker_received_ts),
            "mc_started_ts": _optional_isoformat(self.mc_started_ts),
            "mc_finished_ts": _optional_isoformat(self.mc_finished_ts),
            "status_written_ts": _optional_isoformat(self.status_written_ts),
            "ui_seen_ts": _optional_isoformat(self.ui_seen_ts),
            "queue_ms": self.queue_ms(),
            "runtime_ms": self.runtime_ms(),
            "state_to_status_ms": self.state_to_status_ms(),
            "total_lag_ms": self.total_lag_ms(),
        }


def _optional_isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
