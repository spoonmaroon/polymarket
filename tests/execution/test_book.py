from __future__ import annotations

from typing import Any

import pytest

from polymarket_engine.execution.book import BookLevel, evaluate_execution_book


def test_execution_book_scores_entry_and_exit_liquidity() -> None:
    result = evaluate_execution_book(
        side="UP",
        target_size=20.0,
        best_bid=0.60,
        best_ask=0.62,
        bids=(BookLevel(price=0.60, size=10.0), BookLevel(price=0.59, size=20.0)),
        asks=(BookLevel(price=0.62, size=12.0), BookLevel(price=0.63, size=20.0)),
        quote_age_ms=120,
        max_quote_age_ms=500,
    )

    assert round(result.entry_vwap, 4) == 0.624
    assert round(result.exit_vwap, 4) == 0.595
    assert round(result.entry_slippage, 4) == 0.004
    assert round(result.exit_slippage, 4) == 0.005
    assert round(result.spread, 4) == 0.02
    assert result.entry_depth_available is True
    assert result.exit_depth_available is True
    assert result.quote_age_ms == 120
    assert result.skip_reasons == ()


def test_execution_book_blocks_when_exit_depth_is_missing() -> None:
    result = evaluate_execution_book(
        side="DOWN",
        target_size=20.0,
        best_bid=0.48,
        best_ask=0.50,
        bids=(BookLevel(price=0.48, size=5.0),),
        asks=(BookLevel(price=0.50, size=50.0),),
        quote_age_ms=100,
        max_quote_age_ms=500,
    )

    assert result.entry_depth_available is True
    assert result.exit_depth_available is False
    assert "insufficient_exit_depth" in result.skip_reasons


def test_execution_book_marks_missing_or_zero_entry_depth_unavailable() -> None:
    result = evaluate_execution_book(
        side="UP",
        target_size=10.0,
        best_bid=0.40,
        best_ask=0.42,
        bids=(BookLevel(price=0.40, size=10.0),),
        asks=(BookLevel(price=0.42, size=0.0),),
        quote_age_ms=50,
        max_quote_age_ms=500,
    )

    assert result.entry_vwap == 0.0
    assert result.entry_depth_available is False
    assert "insufficient_entry_depth" in result.skip_reasons


def test_execution_book_marks_stale_orderbook() -> None:
    result = evaluate_execution_book(
        side="UP",
        target_size=1.0,
        best_bid=0.40,
        best_ask=0.42,
        bids=(BookLevel(price=0.40, size=1.0),),
        asks=(BookLevel(price=0.42, size=1.0),),
        quote_age_ms=501,
        max_quote_age_ms=500,
    )

    assert result.skip_reasons == ("stale_orderbook",)


def test_execution_book_rejects_crossed_top_of_book() -> None:
    with pytest.raises(ValueError, match="best_bid"):
        evaluate_execution_book(
            side="UP",
            target_size=1.0,
            best_bid=0.55,
            best_ask=0.54,
            bids=(BookLevel(price=0.55, size=1.0),),
            asks=(BookLevel(price=0.54, size=1.0),),
            quote_age_ms=10,
            max_quote_age_ms=500,
        )


def test_execution_book_rejects_unsorted_asks() -> None:
    with pytest.raises(ValueError, match="asks"):
        evaluate_execution_book(
            side="UP",
            target_size=2.0,
            best_bid=0.50,
            best_ask=0.52,
            bids=(BookLevel(price=0.50, size=2.0),),
            asks=(BookLevel(price=0.53, size=1.0), BookLevel(price=0.52, size=1.0)),
            quote_age_ms=10,
            max_quote_age_ms=500,
        )


def test_execution_book_rejects_unsorted_bids() -> None:
    with pytest.raises(ValueError, match="bids"):
        evaluate_execution_book(
            side="UP",
            target_size=2.0,
            best_bid=0.50,
            best_ask=0.52,
            bids=(BookLevel(price=0.49, size=1.0), BookLevel(price=0.50, size=1.0)),
            asks=(BookLevel(price=0.52, size=2.0),),
            quote_age_ms=10,
            max_quote_age_ms=500,
        )


@pytest.mark.parametrize(
    ("price", "size", "match"),
    (
        (-0.01, 1.0, "price"),
        (1.01, 1.0, "price"),
        (float("nan"), 1.0, "price"),
        (0.5, -0.01, "size"),
        (0.5, float("inf"), "size"),
    ),
)
def test_book_level_validates_price_and_size(price: float, size: float, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        BookLevel(price=price, size=size)


@pytest.mark.parametrize(
    ("kwargs", "match"),
    (
        ({"side": "MAYBE"}, "side"),
        ({"target_size": 0.0}, "target_size"),
        ({"target_size": float("inf")}, "target_size"),
        ({"best_bid": -0.01}, "best_bid"),
        ({"best_ask": 1.01}, "best_ask"),
        ({"quote_age_ms": -1}, "quote_age_ms"),
        ({"max_quote_age_ms": -1}, "max_quote_age_ms"),
    ),
)
def test_evaluate_execution_book_validates_inputs(
    kwargs: dict[str, Any],
    match: str,
) -> None:
    values: dict[str, Any] = {
        "side": "UP",
        "target_size": 1.0,
        "best_bid": 0.40,
        "best_ask": 0.42,
        "bids": (BookLevel(price=0.40, size=1.0),),
        "asks": (BookLevel(price=0.42, size=1.0),),
        "quote_age_ms": 10,
        "max_quote_age_ms": 500,
    }

    with pytest.raises(ValueError, match=match):
        evaluate_execution_book(**{**values, **kwargs})
