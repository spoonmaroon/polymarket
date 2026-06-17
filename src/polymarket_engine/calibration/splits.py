from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


JsonRow = Mapping[str, object]


@dataclass(frozen=True)
class WalkForwardSplitConfig:
    min_train_days: int
    validation_days: int
    purge_seconds: int
    embargo_seconds: int


@dataclass(frozen=True)
class WalkForwardSplit:
    split_index: int
    train_rows: tuple[JsonRow, ...]
    validation_rows: tuple[JsonRow, ...]
    train_start: datetime | None
    train_end: datetime | None
    validation_start: datetime
    validation_end: datetime


def walk_forward_splits(
    rows: Sequence[JsonRow],
    config: WalkForwardSplitConfig,
) -> tuple[WalkForwardSplit, ...]:
    _validate_config(config)
    ordered_rows = tuple(sorted(rows, key=_asof))
    if not ordered_rows:
        return ()

    first_row_ts = _asof(ordered_rows[0])
    last_row_ts = _asof(ordered_rows[-1])
    split_start = first_row_ts + timedelta(days=config.min_train_days)
    validation_span = timedelta(days=config.validation_days)
    purge_span = timedelta(seconds=config.purge_seconds)
    embargo_span = timedelta(seconds=config.embargo_seconds)

    splits: list[WalkForwardSplit] = []
    split_index = 0
    while split_start <= last_row_ts:
        validation_start = split_start
        validation_end = validation_start + validation_span
        train_cutoff = validation_start - purge_span
        next_split_start = validation_end + embargo_span

        train_rows = tuple(row for row in ordered_rows if _asof(row) < train_cutoff)
        validation_rows = tuple(
            row for row in ordered_rows if validation_start <= _asof(row) < validation_end
        )
        if train_rows and validation_rows:
            splits.append(
                WalkForwardSplit(
                    split_index=split_index,
                    train_rows=train_rows,
                    validation_rows=validation_rows,
                    train_start=_asof(train_rows[0]),
                    train_end=_asof(train_rows[-1]),
                    validation_start=validation_start,
                    validation_end=validation_end,
                )
            )
            split_index += 1

        split_start = next_split_start

    return tuple(splits)


def _validate_config(config: WalkForwardSplitConfig) -> None:
    if config.min_train_days < 0:
        raise ValueError("min_train_days must be nonnegative")
    if config.validation_days <= 0:
        raise ValueError("validation_days must be positive")
    if config.purge_seconds < 0:
        raise ValueError("purge_seconds must be nonnegative")
    if config.embargo_seconds < 0:
        raise ValueError("embargo_seconds must be nonnegative")


def _asof(row: JsonRow) -> datetime:
    value = row.get("asof_ts")
    if not isinstance(value, str):
        raise ValueError("row asof_ts must be an ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
