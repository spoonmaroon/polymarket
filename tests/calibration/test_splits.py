from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polymarket_engine.calibration.splits import WalkForwardSplitConfig
from polymarket_engine.calibration.splits import walk_forward_splits


def _daily_rows() -> list[dict[str, object]]:
    base = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    return [
        {"state_id": f"state-{idx}", "asof_ts": (base + timedelta(days=idx)).isoformat()}
        for idx in range(8)
    ]


def test_walk_forward_splits_floor_first_timestamp_to_utc_day() -> None:
    splits = walk_forward_splits(
        _daily_rows(),
        WalkForwardSplitConfig(
            min_train_days=2,
            validation_days=2,
            purge_seconds=0,
            embargo_seconds=0,
        ),
    )

    assert len(splits) == 3

    first = splits[0]
    assert first.validation_start == datetime(2026, 6, 3, tzinfo=timezone.utc)
    assert [row["state_id"] for row in first.train_rows] == ["state-0", "state-1"]
    assert [row["state_id"] for row in first.validation_rows] == ["state-2", "state-3"]

    second = splits[1]
    assert second.validation_start == datetime(2026, 6, 5, tzinfo=timezone.utc)
    assert [row["state_id"] for row in second.train_rows] == [
        "state-0",
        "state-1",
        "state-2",
        "state-3",
    ]
    assert [row["state_id"] for row in second.validation_rows] == ["state-4", "state-5"]

    third = splits[2]
    assert third.validation_start == datetime(2026, 6, 7, tzinfo=timezone.utc)
    assert [row["state_id"] for row in third.train_rows] == [
        "state-0",
        "state-1",
        "state-2",
        "state-3",
        "state-4",
        "state-5",
    ]
    assert [row["state_id"] for row in third.validation_rows] == ["state-6", "state-7"]


def test_walk_forward_splits_apply_purge_and_embargo_with_min_train_days_zero() -> None:
    bootstrap = datetime(2026, 6, 3, 12, tzinfo=timezone.utc)
    validation_start = datetime(2026, 6, 4, 0, 1, tzinfo=timezone.utc)
    rows = [
        {"state_id": "bootstrap", "asof_ts": bootstrap.isoformat()},
        {"state_id": "train-safe", "asof_ts": datetime(2026, 6, 3, 23, 50, tzinfo=timezone.utc).isoformat()},
        {"state_id": "purged", "asof_ts": datetime(2026, 6, 4, 0, 0, 30, tzinfo=timezone.utc).isoformat()},
        {"state_id": "validate", "asof_ts": validation_start.isoformat()},
        {
            "state_id": "embargoed",
            "asof_ts": datetime(2026, 6, 5, 0, 1, 30, tzinfo=timezone.utc).isoformat(),
        },
        {"state_id": "next-safe", "asof_ts": datetime(2026, 6, 5, 0, 11, tzinfo=timezone.utc).isoformat()},
    ]

    splits = walk_forward_splits(
        rows,
        WalkForwardSplitConfig(
            min_train_days=0,
            validation_days=1,
            purge_seconds=60,
            embargo_seconds=60,
        ),
    )

    assert len(splits) == 2

    first = splits[0]
    assert first.validation_start == validation_start
    assert [row["state_id"] for row in first.train_rows] == ["bootstrap", "train-safe"]
    assert [row["state_id"] for row in first.validation_rows] == ["validate"]

    second = splits[1]
    assert second.validation_start == datetime(2026, 6, 5, 0, 2, tzinfo=timezone.utc)
    assert [row["state_id"] for row in second.train_rows] == [
        "bootstrap",
        "train-safe",
        "purged",
        "validate",
    ]
    assert [row["state_id"] for row in second.validation_rows] == ["next-safe"]


def test_walk_forward_splits_order_equal_timestamps_by_state_id() -> None:
    rows = [
        {"state_id": "state-b", "asof_ts": "2026-06-02T12:00:00+00:00"},
        {"state_id": "state-a", "asof_ts": "2026-06-02T12:00:00+00:00"},
        {"state_id": "state-c", "asof_ts": "2026-06-03T12:00:00+00:00"},
    ]

    splits = walk_forward_splits(
        rows,
        WalkForwardSplitConfig(
            min_train_days=1,
            validation_days=1,
            purge_seconds=0,
            embargo_seconds=0,
        ),
    )

    assert len(splits) == 1
    assert [row["state_id"] for row in splits[0].train_rows] == ["state-a", "state-b"]
    assert [row["state_id"] for row in splits[0].validation_rows] == ["state-c"]


def test_walk_forward_splits_require_positive_validation_days() -> None:
    try:
        walk_forward_splits(
            _daily_rows(),
            WalkForwardSplitConfig(
                min_train_days=1,
                validation_days=0,
                purge_seconds=0,
                embargo_seconds=0,
            ),
        )
    except ValueError as exc:
        assert str(exc) == "validation_days must be positive"
    else:
        raise AssertionError("expected validation_days validation to raise")
