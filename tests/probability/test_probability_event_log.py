from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polymarket_engine.probability.event_log import ProbabilityEventLogRow
from polymarket_engine.probability.event_log import SimulationArtifactRow


def test_probability_event_log_row_serializes_stable_fields() -> None:
    asof = datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc)
    row = ProbabilityEventLogRow(
        event_id="event-1",
        output_id="prob-1",
        state_id="state-1",
        contract_id="btc-updown-5m-1:UP",
        market_slug="btc-updown-5m-1",
        asset="BTC",
        side="UP",
        start_ts=asof,
        expiry_ts=asof + timedelta(minutes=5),
        asof_ts=asof,
        probability_kind="MC",
        backend="cuda",
        model_version="cached-grid-v1",
        generator_version="cuda-lognormal-chainlink-sigma-v1",
        cache_key="cache-1",
        cache_status="REFRESH",
        p_finish=0.71,
        p_no_touch=0.22,
        z_path=0.55,
        sigma_tau=0.001,
        executable_price=0.63,
        spread=0.01,
        seconds_left=180.0,
        wave_phase="forming",
        wave_score=0.4,
        path_count=20_000,
        seed=123,
        queue_ms=4.0,
        runtime_ms=18.0,
        state_to_status_ms=35.0,
        total_lag_ms=42.0,
        generated_at=asof,
        valid_from=asof,
        valid_until=asof + timedelta(seconds=30),
        diagnostics={"reason": "unit-test"},
    )

    payload = row.to_json_dict()

    assert payload["market_slug"] == "btc-updown-5m-1"
    assert payload["probability_kind"] == "MC"
    assert payload["diagnostics"] == {"reason": "unit-test"}


def test_simulation_artifact_row_caps_sampled_paths() -> None:
    asof = datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc)
    sampled_paths = [
        {"index": index, "points": list(range(40)), "terminal_win": True}
        for index in range(80)
    ]

    row = SimulationArtifactRow(
        artifact_id="artifact-1",
        output_id="prob-1",
        state_id="state-1",
        asof_ts=asof,
        model_version="offline-lognormal-chainlink-sigma-v1",
        backend="cpu",
        path_count=80_000,
        terminal_win_count=44_000,
        no_touch_win_count=40_000,
        terminal_price_quantiles={"p05": 100.0, "p50": 101.0, "p95": 103.0},
        crossing_count_quantiles={"p50": 1.0, "p95": 4.0},
        sampled_paths=sampled_paths,
        diagnostics={"source": "unit-test"},
    )

    payload = row.to_json_dict()

    assert len(payload["sampled_paths"]) == 64
    assert all(len(path["points"]) == 32 for path in payload["sampled_paths"])
