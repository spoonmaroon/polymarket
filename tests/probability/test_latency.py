from __future__ import annotations

from datetime import datetime, timezone

from polymarket_engine.probability.latency import ProbabilityLatencyTrace


def test_probability_latency_trace_computes_segment_ms() -> None:
    base = datetime(2026, 6, 5, 17, 0, 0, tzinfo=timezone.utc)
    trace = ProbabilityLatencyTrace(
        state_asof_ts=base,
        tick_observed_ts=base,
        worker_received_ts=base.replace(microsecond=100_000),
        mc_started_ts=base.replace(microsecond=150_000),
        mc_finished_ts=base.replace(microsecond=450_000),
        status_written_ts=base.replace(microsecond=500_000),
        ui_seen_ts=None,
    )

    payload = trace.to_json_dict()

    assert payload["queue_ms"] == 50.0
    assert payload["runtime_ms"] == 300.0
    assert payload["state_to_status_ms"] == 500.0
    assert payload["total_lag_ms"] == 500.0


def test_probability_latency_trace_uses_ui_seen_for_total_lag() -> None:
    base = datetime(2026, 6, 5, 17, 0, 0, tzinfo=timezone.utc)
    trace = ProbabilityLatencyTrace(
        state_asof_ts=base,
        tick_observed_ts=None,
        worker_received_ts=None,
        mc_started_ts=None,
        mc_finished_ts=None,
        status_written_ts=base.replace(microsecond=500_000),
        ui_seen_ts=base.replace(microsecond=750_000),
    )

    assert trace.to_json_dict()["total_lag_ms"] == 750.0
