from __future__ import annotations

import pytest

from polymarket_engine.probability.wave_signal import WaveSignalInput
from polymarket_engine.probability.wave_signal import classify_wave_signal


def test_wave_signal_marks_breaking_when_price_is_90_and_dynamic_edge_passes() -> None:
    signal = classify_wave_signal(
        WaveSignalInput(
            p_finish=0.97,
            p_no_touch=0.86,
            executable_price=0.90,
            edge_after_costs=0.045,
            required_edge=0.030,
            seconds_left=18.0,
            source_age_ms=80,
            book_age_ms=90,
        )
    )

    assert signal["wave_phase"] == "breaking"
    assert signal["wave_score"] == pytest.approx(1.0)
    assert signal["wave_markers"] == ["P90"]
    assert signal["dynamic_edge"] == pytest.approx(0.045)
    assert signal["dynamic_required_edge"] == pytest.approx(0.030)
    assert "EDGE_OK" in signal["wave_reasons"]


def test_wave_signal_marks_late_when_price_is_95_but_edge_still_passes() -> None:
    signal = classify_wave_signal(
        WaveSignalInput(
            p_finish=0.99,
            p_no_touch=0.95,
            executable_price=0.955,
            edge_after_costs=0.026,
            required_edge=0.020,
            seconds_left=9.0,
            source_age_ms=70,
            book_age_ms=75,
        )
    )

    assert signal["wave_phase"] == "late"
    assert signal["wave_markers"] == ["P90", "P95"]
    assert "PRICE_95" in signal["wave_reasons"]


def test_wave_signal_marks_tick_size_regime_at_96() -> None:
    signal = classify_wave_signal(
        WaveSignalInput(
            p_finish=0.995,
            p_no_touch=0.96,
            executable_price=0.965,
            edge_after_costs=0.021,
            required_edge=0.020,
            seconds_left=5.0,
            source_age_ms=50,
            book_age_ms=55,
        )
    )

    assert signal["wave_phase"] == "late"
    assert signal["wave_markers"] == ["P90", "P95", "TICK96"]
    assert "TICK_SIZE_96" in signal["wave_reasons"]


def test_wave_signal_marks_missed_when_visible_wave_zone_lacks_edge() -> None:
    signal = classify_wave_signal(
        WaveSignalInput(
            p_finish=0.96,
            p_no_touch=0.91,
            executable_price=0.94,
            edge_after_costs=0.010,
            required_edge=0.030,
            seconds_left=11.0,
            source_age_ms=80,
            book_age_ms=85,
        )
    )

    assert signal["wave_phase"] == "missed"
    assert signal["wave_markers"] == ["P90"]
    assert "EDGE_SHORT" in signal["wave_reasons"]


def test_wave_signal_uses_fallback_edge_when_gate_fields_are_missing() -> None:
    signal = classify_wave_signal(
        WaveSignalInput(
            p_finish=0.88,
            p_no_touch=0.82,
            executable_price=0.72,
            edge_after_costs=None,
            required_edge=None,
            seconds_left=24.0,
            source_age_ms=120,
            book_age_ms=130,
        )
    )

    assert signal["wave_phase"] == "forming"
    assert signal["dynamic_edge"] == pytest.approx(0.16)
    assert signal["dynamic_required_edge"] == pytest.approx(0.03)
    assert signal["wave_markers"] == []
