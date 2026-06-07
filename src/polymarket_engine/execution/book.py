from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BookLevel:
    price: float
    size: float

    def __post_init__(self) -> None:
        _require_probability_price(self.price, "price")
        _require_nonnegative_finite(self.size, "size")


@dataclass(frozen=True)
class ExecutionBookMetrics:
    entry_vwap: float
    exit_vwap: float
    entry_slippage: float
    exit_slippage: float
    spread: float
    entry_depth_available: bool
    exit_depth_available: bool
    quote_age_ms: int
    skip_reasons: tuple[str, ...]


def evaluate_execution_book(
    *,
    side: str,
    target_size: float,
    best_bid: float,
    best_ask: float,
    bids: tuple[BookLevel, ...],
    asks: tuple[BookLevel, ...],
    quote_age_ms: int,
    max_quote_age_ms: int,
) -> ExecutionBookMetrics:
    if side not in {"UP", "DOWN"}:
        raise ValueError("side must be UP or DOWN")
    _require_positive_finite(target_size, "target_size")
    _require_probability_price(best_bid, "best_bid")
    _require_probability_price(best_ask, "best_ask")
    if best_bid > best_ask:
        raise ValueError("best_bid must not exceed best_ask")
    _require_sorted_ascending(asks, "asks")
    _require_sorted_descending(bids, "bids")
    _require_nonnegative_age(quote_age_ms, "quote_age_ms")
    _require_nonnegative_age(max_quote_age_ms, "max_quote_age_ms")

    entry_vwap, entry_complete = _target_vwap(asks, target_size)
    exit_vwap, exit_complete = _target_vwap(bids, target_size)

    skip_reasons: list[str] = []
    if quote_age_ms > max_quote_age_ms:
        skip_reasons.append("stale_orderbook")
    if not entry_complete:
        skip_reasons.append("insufficient_entry_depth")
    if not exit_complete:
        skip_reasons.append("insufficient_exit_depth")

    return ExecutionBookMetrics(
        entry_vwap=entry_vwap,
        exit_vwap=exit_vwap,
        entry_slippage=max(0.0, entry_vwap - best_ask),
        exit_slippage=max(0.0, best_bid - exit_vwap),
        spread=max(0.0, best_ask - best_bid),
        entry_depth_available=entry_complete,
        exit_depth_available=exit_complete,
        quote_age_ms=quote_age_ms,
        skip_reasons=tuple(skip_reasons),
    )


def _target_vwap(levels: tuple[BookLevel, ...], target_size: float) -> tuple[float, bool]:
    remaining = target_size
    notional = 0.0
    filled = 0.0

    for level in levels:
        take = min(level.size, remaining)
        if take <= 0.0:
            continue
        notional += take * level.price
        filled += take
        remaining -= take
        if remaining <= 1e-12:
            break

    if filled <= 0.0:
        return 0.0, False
    return notional / filled, remaining <= 1e-12


def _require_probability_price(value: float, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0.0
        or value > 1.0
    ):
        raise ValueError(f"{field_name} must be finite and between 0 and 1")


def _require_positive_finite(value: float, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0.0
    ):
        raise ValueError(f"{field_name} must be positive and finite")


def _require_nonnegative_finite(value: float, field_name: str) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0.0
    ):
        raise ValueError(f"{field_name} must be nonnegative and finite")


def _require_nonnegative_age(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a nonnegative integer")


def _require_sorted_ascending(levels: tuple[BookLevel, ...], field_name: str) -> None:
    for previous, current in zip(levels, levels[1:]):
        if previous.price > current.price:
            raise ValueError(f"{field_name} must be sorted by ascending price")


def _require_sorted_descending(levels: tuple[BookLevel, ...], field_name: str) -> None:
    for previous, current in zip(levels, levels[1:]):
        if previous.price < current.price:
            raise ValueError(f"{field_name} must be sorted by descending price")
