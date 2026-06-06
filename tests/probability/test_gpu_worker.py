from __future__ import annotations

from datetime import UTC, datetime, timedelta

from polymarket_engine.probability.gpu_worker import _event_payload_from_row
from polymarket_engine.probability.runtime_inputs import ProbabilityRuntimeInput
from polymarket_engine.probability.schema import ProbabilityInput


def test_event_payload_includes_simulation_preview_for_mc_rows() -> None:
    asof_ts = datetime(2026, 6, 6, 16, 0, tzinfo=UTC)
    runtime_input = ProbabilityRuntimeInput(
        probability_input=ProbabilityInput(
            state_id="state-btc-up",
            asof_ts=asof_ts,
            asset="BTC",
            side="UP",
            comparison_operator=">=",
            seconds_left=240.0,
            settlement_price=70_100.0,
            threshold=70_000.0,
            sigma_tau=0.012,
            executable_price=0.54,
            source_age_ms=120,
            book_age_ms=80,
            z_path=0.12,
        ),
        contract_id="btc-up",
        contract="BTC 5m UP",
        start_ts=asof_ts,
        expiry_ts=asof_ts + timedelta(minutes=5),
        flags=("OK",),
        market_slug="btc-updown-5m-1780752000",
    )
    preview = {
        "sampled_paths": [
            {
                "index": 0,
                "terminal_win": True,
                "no_touch_win": True,
                "points": [70_100.0, 70_120.0],
            }
        ]
    }

    payload = _event_payload_from_row(
        runtime_input=runtime_input,
        row={
            "probability_kind": "MC",
            "backend": "cuda",
            "p_finish": 0.61,
            "p_no_touch": 0.57,
            "z_path": 0.12,
            "sigma_tau": 0.012,
            "wave_phase": "none",
            "wave_score": 0.0,
            "simulation_preview": preview,
        },
        generated_at=asof_ts,
        output_id="output-btc-up",
    )

    assert payload["simulation_preview"] == preview
