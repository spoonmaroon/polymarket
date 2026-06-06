from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest

from polymarket_engine.validation.outcome_sidecar import run_outcome_refresh_loop


def test_outcome_sidecar_refreshes_on_own_cadence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_refresh(*args: Any, **kwargs: Any) -> int:
        nonlocal calls
        calls += 1
        return 0

    monkeypatch.setattr(
        "polymarket_engine.validation.outcome_sidecar.refresh_market_outcomes",
        fake_refresh,
    )
    monkeypatch.setattr(
        "polymarket_engine.validation.outcome_sidecar.time.sleep",
        lambda _: None,
    )

    run_outcome_refresh_loop(
        duckdb_path=tmp_path / "db.duckdb",
        outcome_status_path=tmp_path / "live" / "outcomes.json",
        interval_seconds=30.0,
        max_cycles=2,
    )

    assert calls == 2


@pytest.mark.parametrize(
    ("interval_seconds", "max_cycles", "message"),
    [
        (0.0, None, "interval_seconds must be positive"),
        (-1.0, 1, "interval_seconds must be positive"),
        (float("inf"), 1, "interval_seconds must be positive"),
        (30.0, 0, "max_cycles must be positive when provided"),
        (30.0, -1, "max_cycles must be positive when provided"),
    ],
)
def test_outcome_sidecar_rejects_invalid_cadence(
    tmp_path: Path,
    interval_seconds: float,
    max_cycles: int | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        run_outcome_refresh_loop(
            duckdb_path=tmp_path / "db.duckdb",
            outcome_status_path=tmp_path / "live" / "outcomes.json",
            interval_seconds=interval_seconds,
            max_cycles=max_cycles,
        )


def test_outcome_sidecar_continues_after_transient_duckdb_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def fake_refresh(*args: Any, **kwargs: Any) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise duckdb.IOException("Conflicting lock is held")
        return 0

    monkeypatch.setattr(
        "polymarket_engine.validation.outcome_sidecar.refresh_market_outcomes",
        fake_refresh,
    )
    monkeypatch.setattr(
        "polymarket_engine.validation.outcome_sidecar.time.sleep",
        sleeps.append,
    )

    run_outcome_refresh_loop(
        duckdb_path=tmp_path / "db.duckdb",
        outcome_status_path=tmp_path / "live" / "outcomes.json",
        interval_seconds=30.0,
        max_cycles=2,
    )

    assert calls == 2
    assert sleeps == [30.0]
