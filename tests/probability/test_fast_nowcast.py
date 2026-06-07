from __future__ import annotations

from datetime import datetime, timezone

from polymarket_engine.probability.fast_nowcast import FastNowcastInput
from polymarket_engine.probability.fast_nowcast import compute_fast_nowcast


def test_fast_nowcast_moves_probability_with_z_path() -> None:
    nowcast = compute_fast_nowcast(
        FastNowcastInput(
            state_id="btc:UP:1",
            asof_ts=datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc),
            asset="BTC",
            side="UP",
            z_path=1.0,
            seconds_left=120.0,
            executable_price=0.62,
            sigma_tau=0.001,
            source_age_ms=50,
            book_age_ms=40,
        )
    )

    assert nowcast.model_version == "fast-nowcast-v1"
    assert 0.83 < nowcast.p_finish < 0.85
    assert nowcast.p_no_touch == 0.0
    assert nowcast.wave_phase == "forming"
    assert nowcast.backend == "analytic"


def test_fast_nowcast_marks_high_price_without_edge_as_missed() -> None:
    nowcast = compute_fast_nowcast(
        FastNowcastInput(
            state_id="btc:UP:2",
            asof_ts=datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc),
            asset="BTC",
            side="UP",
            z_path=1.0,
            seconds_left=20.0,
            executable_price=0.985,
            sigma_tau=0.001,
            source_age_ms=50,
            book_age_ms=40,
        )
    )

    assert nowcast.wave_phase == "missed"
    assert nowcast.wave_score >= 0.0
    assert nowcast.probability_kind == "NOWCAST"
