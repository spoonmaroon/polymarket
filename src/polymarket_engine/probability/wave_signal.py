from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

WavePhase = Literal["none", "forming", "breaking", "late", "missed"]


@dataclass(frozen=True)
class WaveSignalInput:
    p_finish: float
    p_no_touch: float
    executable_price: float | None
    edge_after_costs: float | None
    required_edge: float | None
    seconds_left: float
    source_age_ms: int
    book_age_ms: int


class WaveSignal(TypedDict):
    wave_score: float
    wave_phase: WavePhase
    wave_reasons: list[str]
    wave_markers: list[str]
    dynamic_edge: float | None
    dynamic_required_edge: float | None


def classify_wave_signal(signal_input: WaveSignalInput) -> WaveSignal:
    _validate_probability(signal_input.p_finish, "p_finish")
    _validate_probability(signal_input.p_no_touch, "p_no_touch")
    if signal_input.executable_price is not None:
        _validate_probability(signal_input.executable_price, "executable_price")
    if signal_input.seconds_left < 0:
        raise ValueError("seconds_left must be nonnegative")
    if signal_input.source_age_ms < 0 or signal_input.book_age_ms < 0:
        raise ValueError("source_age_ms and book_age_ms must be nonnegative")

    dynamic_edge = _dynamic_edge(signal_input)
    dynamic_required_edge = _dynamic_required_edge(signal_input)
    edge_ok = (
        dynamic_edge is not None
        and dynamic_required_edge is not None
        and dynamic_edge >= dynamic_required_edge
    )
    markers = _markers(signal_input.executable_price)
    reasons = _reasons(
        signal_input=signal_input,
        markers=markers,
        edge_ok=edge_ok,
        dynamic_edge=dynamic_edge,
        dynamic_required_edge=dynamic_required_edge,
    )
    phase = _phase(
        signal_input=signal_input,
        markers=markers,
        edge_ok=edge_ok,
        dynamic_edge=dynamic_edge,
        dynamic_required_edge=dynamic_required_edge,
    )
    return {
        "wave_score": _wave_score(signal_input, dynamic_edge, dynamic_required_edge),
        "wave_phase": phase,
        "wave_reasons": reasons,
        "wave_markers": markers,
        "dynamic_edge": dynamic_edge,
        "dynamic_required_edge": dynamic_required_edge,
    }


def _dynamic_edge(signal_input: WaveSignalInput) -> float | None:
    if signal_input.edge_after_costs is not None:
        return float(signal_input.edge_after_costs)
    if signal_input.executable_price is None:
        return None
    return signal_input.p_finish - signal_input.executable_price


def _dynamic_required_edge(signal_input: WaveSignalInput) -> float | None:
    if signal_input.required_edge is not None:
        return float(signal_input.required_edge)
    if signal_input.executable_price is None:
        return None
    age_penalty = 0.0
    if signal_input.source_age_ms > 1_000:
        age_penalty += 0.01
    if signal_input.book_age_ms > 1_000:
        age_penalty += 0.01
    terminal_penalty = 0.01 if signal_input.seconds_left <= 15 else 0.0
    return 0.03 + age_penalty + terminal_penalty


def _markers(executable_price: float | None) -> list[str]:
    if executable_price is None:
        return []
    markers: list[str] = []
    if executable_price >= 0.90:
        markers.append("P90")
    if executable_price >= 0.95:
        markers.append("P95")
    if executable_price >= 0.96:
        markers.append("TICK96")
    return markers


def _phase(
    *,
    signal_input: WaveSignalInput,
    markers: list[str],
    edge_ok: bool,
    dynamic_edge: float | None,
    dynamic_required_edge: float | None,
) -> WavePhase:
    visible_wave = bool(markers) or signal_input.p_finish >= 0.90
    if visible_wave and not edge_ok:
        return "missed"
    if "P95" in markers or "TICK96" in markers:
        return "late" if edge_ok else "missed"
    if "P90" in markers:
        return "breaking" if edge_ok else "missed"
    if edge_ok and signal_input.p_finish >= 0.75:
        return "forming"
    if (
        dynamic_edge is not None
        and dynamic_required_edge is not None
        and dynamic_edge >= dynamic_required_edge * 0.75
        and signal_input.p_finish >= 0.70
    ):
        return "forming"
    return "none"


def _wave_score(
    signal_input: WaveSignalInput,
    dynamic_edge: float | None,
    dynamic_required_edge: float | None,
) -> float:
    probability_score = _clamp((signal_input.p_finish - 0.50) / 0.50)
    edge_score = 0.0
    if (
        dynamic_edge is not None
        and dynamic_required_edge is not None
        and dynamic_required_edge > 0
    ):
        edge_score = _clamp(dynamic_edge / dynamic_required_edge)
    price_bonus = 0.0
    executable = signal_input.executable_price
    if executable is not None:
        if executable >= 0.96:
            price_bonus = 0.20
        elif executable >= 0.95:
            price_bonus = 0.15
        elif executable >= 0.90:
            price_bonus = 0.10
    return round(_clamp((0.55 * probability_score) + (0.45 * edge_score) + price_bonus), 3)


def _reasons(
    *,
    signal_input: WaveSignalInput,
    markers: list[str],
    edge_ok: bool,
    dynamic_edge: float | None,
    dynamic_required_edge: float | None,
) -> list[str]:
    reasons: list[str] = []
    if signal_input.executable_price is None:
        reasons.append("NO_EXECUTABLE_PRICE")
    if dynamic_edge is not None and dynamic_required_edge is not None:
        reasons.append("EDGE_OK" if edge_ok else "EDGE_SHORT")
    if signal_input.p_finish >= 0.90:
        reasons.append("HIGH_P_FINISH")
    if signal_input.p_no_touch >= 0.90:
        reasons.append("HIGH_P_NO_TOUCH")
    if "P90" in markers:
        reasons.append("PRICE_90")
    if "P95" in markers:
        reasons.append("PRICE_95")
    if "TICK96" in markers:
        reasons.append("TICK_SIZE_96")
    if signal_input.seconds_left <= 15:
        reasons.append("TERMINAL_WINDOW")
    return reasons


def _validate_probability(value: float, field_name: str) -> None:
    if isinstance(value, bool) or not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be between 0 and 1")


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
