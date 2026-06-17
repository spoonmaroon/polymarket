from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polymarket_engine.calibration.splits import WalkForwardSplitConfig
from polymarket_engine.calibration.splits import walk_forward_splits


def _daily_rows() -> list[dict[str, object]]:
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    return [
        {"state_id": f"state-{idx}", "asof_ts": (base + timedelta(days=idx)).isoformat()}
        for idx in range(8)
    ]


def test_walk_forward_splits_use_past_train_and_future_validation() -> None:
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
    assert [row["state_id"] for row in splits[0].train_rows] == ["state-0", "state-1"]
    assert [row["state_id"] for row in splits[0].validation_rows] == ["state-2", "state-3"]
    assert [row["state_id"] for row in splits[1].train_rows] == [
        "state-0",
        "state-1",
        "state-2",
        "state-3",
    ]
    assert [row["state_id"] for row in splits[1].validation_rows] == ["state-4", "state-5"]
    assert [row["state_id"] for row in splits[2].train_rows] == [
        "state-0",
        "state-1",
        "state-2",
        "state-3",
        "state-4",
        "state-5",
    ]
    assert [row["state_id"] for row in splits[2].validation_rows] == ["state-6", "state-7"]


def test_walk_forward_splits_apply_purge_and_embargo_without_leakage() -> None:
    base = datetime(2026, 6, 3, 12, tzinfo=timezone.utc)
    rows = [
        {"state_id": "train-safe", "asof_ts": (base - timedelta(days=2)).isoformat()},
        {"state_id": "purged", "asof_ts": (base - timedelta(seconds=30)).isoformat()},
        {"state_id": "validate", "asof_ts": base.isoformat()},
        {
            "state_id": "embargoed",
            "asof_ts": (base + timedelta(days=1, seconds=30)).isoformat(),
        },
        {"state_id": "next-safe", "asof_ts": (base + timedelta(days=2)).isoformat()},
    ]

    splits = walk_forward_splits(
        rows,
        WalkForwardSplitConfig(
            min_train_days=2,
            validation_days=1,
            purge_seconds=60,
            embargo_seconds=60,
        ),
    )

    assert len(splits) == 2

    first = splits[0]
    assert [row["state_id"] for row in first.train_rows] == ["train-safe"]
    assert [row["state_id"] for row in first.validation_rows] == ["validate"]

    second = splits[1]
    assert [row["state_id"] for row in second.train_rows] == [
        "train-safe",
        "purged",
        "validate",
    ]
    assert [row["state_id"] for row in second.validation_rows] == ["next-safe"]


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
