from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class RawPartition:
    root: Path
    source_key: str
    stream_key: str
    event_ts: datetime

    @property
    def date_part(self) -> str:
        ts = self.event_ts.astimezone(timezone.utc)
        return ts.strftime("date=%Y-%m-%d")

    @property
    def hour_part(self) -> str:
        ts = self.event_ts.astimezone(timezone.utc)
        return ts.strftime("hour=%H")

    @property
    def directory(self) -> Path:
        return self.root / self.source_key / self.stream_key / self.date_part / self.hour_part
