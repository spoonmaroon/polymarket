from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from polymarket_engine.storage.raw_writer import RawEvent


class SourceQualityFlag(str, Enum):
    STALE_SOURCE = "stale_source"
    CLOCK_SKEW = "clock_skew"
    SOURCE_BLOCKED = "source_blocked"
    EMPTY_MESSAGE = "empty_message"
    PARSE_ERROR = "parse_error"
    GAP_DETECTED = "gap_detected"


@dataclass(frozen=True)
class CollectorEvent:
    source_key: str
    stream_key: str
    symbol: str
    event_ts: datetime
    observed_ts: datetime
    payload: dict[str, Any]
    quality_flags: tuple[SourceQualityFlag, ...] = ()

    @property
    def lag_ms(self) -> int:
        return int((self.observed_ts - self.event_ts).total_seconds() * 1000)

    def to_raw_event(self) -> RawEvent:
        return RawEvent(
            source_key=self.source_key,
            stream_key=self.stream_key,
            symbol=self.symbol,
            event_ts=self.event_ts,
            observed_ts=self.observed_ts,
            payload={
                **self.payload,
                "quality_flags": [flag.value for flag in self.quality_flags],
                "lag_ms": self.lag_ms,
            },
        )


@dataclass(frozen=True)
class SourceLag:
    source_key: str
    lag_ms: int
    stale_after_ms: int

    def quality_flags(self) -> tuple[SourceQualityFlag, ...]:
        flags: list[SourceQualityFlag] = []
        if self.lag_ms < 0:
            flags.append(SourceQualityFlag.CLOCK_SKEW)
        if self.lag_ms > self.stale_after_ms:
            flags.append(SourceQualityFlag.STALE_SOURCE)
        return tuple(flags)


@dataclass(frozen=True)
class SourceHealth:
    source_key: str
    connected: bool
    last_event_ts: datetime | None
    last_observed_ts: datetime | None
    last_error: str | None = None
    quality_flags: tuple[SourceQualityFlag, ...] = field(default_factory=tuple)

    @property
    def is_healthy(self) -> bool:
        return self.connected and self.last_error is None and not self.quality_flags
