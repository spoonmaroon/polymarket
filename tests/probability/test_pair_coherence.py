from __future__ import annotations

import pytest

from polymarket_engine.probability.pair_coherence import normalize_binary_probability_pairs


def _row(side: str, p_finish: float) -> dict[str, object]:
    return {
        "contract": f"BTC 5m {side}",
        "contract_id": f"btc-{side.lower()}",
        "market_slug": "btc-updown-5m-1780752000",
        "asset": "BTC",
        "side": side,
        "start_ts": "2026-06-07T21:40:00+00:00",
        "expiry_ts": "2026-06-07T21:45:00+00:00",
        "p_finish": p_finish,
    }


def test_normalize_binary_probability_pairs_preserves_relative_odds_and_records_gap() -> None:
    rows = normalize_binary_probability_pairs((_row("UP", 0.28), _row("DOWN", 0.62)))
    by_side = {row["side"]: row for row in rows}

    assert by_side["UP"]["p_finish"] == pytest.approx(0.28 / 0.90)
    assert by_side["DOWN"]["p_finish"] == pytest.approx(0.62 / 0.90)
    assert by_side["UP"]["pair_probability_sum_before"] == pytest.approx(0.90)
    assert by_side["DOWN"]["pair_probability_sum_before"] == pytest.approx(0.90)
    assert by_side["UP"]["pair_complement_gap"] == pytest.approx(0.10)
    assert by_side["DOWN"]["pair_complement_gap"] == pytest.approx(0.10)
    assert by_side["UP"]["pair_normalized"] is True
    assert by_side["DOWN"]["pair_normalized"] is True
    assert by_side["UP"]["counterparty_p_finish"] == pytest.approx(0.62 / 0.90)


def test_normalize_binary_probability_pairs_leaves_already_coherent_pair_unchanged() -> None:
    rows = normalize_binary_probability_pairs((_row("UP", 0.56), _row("DOWN", 0.44)))
    by_side = {row["side"]: row for row in rows}

    assert by_side["UP"]["p_finish"] == pytest.approx(0.56)
    assert by_side["DOWN"]["p_finish"] == pytest.approx(0.44)
    assert by_side["UP"]["pair_probability_sum_before"] == pytest.approx(1.0)
    assert by_side["UP"]["pair_complement_gap"] == pytest.approx(0.0)
    assert by_side["UP"]["pair_normalized"] is False


def test_normalize_binary_probability_pairs_does_not_invent_missing_side() -> None:
    rows = normalize_binary_probability_pairs((_row("UP", 0.56),))

    assert rows[0]["p_finish"] == pytest.approx(0.56)
    assert "pair_probability_sum_before" not in rows[0]
