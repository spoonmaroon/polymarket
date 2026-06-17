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
    normalized_rows = tuple(
        sorted(
            ((_asof(row), _state_id(row), index, row) for index, row in enumerate(rows)),
            key=lambda item: (item[0], item[1], item[2]),
        )
    )
    ordered_rows = tuple(item[3] for item in normalized_rows)
    if not ordered_rows:
        return ()

    row_asofs = {id(row): asof for asof, _state_id, _index, row in normalized_rows}
    first_row_ts = row_asofs[id(ordered_rows[0])]
    last_row_ts = row_asofs[id(ordered_rows[-1])]
    split_start = _floor_day(first_row_ts) + timedelta(days=config.min_train_days)
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

        train_rows = tuple(row for row in ordered_rows if row_asofs[id(row)] < train_cutoff)
        validation_rows = tuple(
            row
            for row in ordered_rows
            if validation_start <= row_asofs[id(row)] < validation_end
        )
        if train_rows and validation_rows:
            splits.append(
                WalkForwardSplit(
                    split_index=split_index,
                    train_rows=train_rows,
                    validation_rows=validation_rows,
                    train_start=row_asofs[id(train_rows[0])],
                    train_end=row_asofs[id(train_rows[-1])],
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


def _floor_day(value: datetime) -> datetime:
    utc_value = value.astimezone(timezone.utc)
    return utc_value.replace(hour=0, minute=0, second=0, microsecond=0)


def _state_id(row: JsonRow) -> str:
    value = row.get("state_id")
    if isinstance(value, str):
        return value
    return ""
