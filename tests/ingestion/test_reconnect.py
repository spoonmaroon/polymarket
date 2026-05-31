from polymarket_engine.ingestion.reconnect import compute_reconnect_delay


def test_first_reconnect_delay_starts_at_base_without_jitter() -> None:
    assert compute_reconnect_delay(0, base=1.0, cap=30.0, jitter_pct=0.25, random_value=0.5) == 1.0


def test_reconnect_delay_grows_exponentially_without_jitter() -> None:
    assert compute_reconnect_delay(3, base=1.0, cap=30.0, jitter_pct=0.25, random_value=0.5) == 8.0


def test_reconnect_delay_is_capped_before_jitter() -> None:
    assert compute_reconnect_delay(99, base=1.0, cap=30.0, jitter_pct=0.0, random_value=0.5) == 30.0


def test_reconnect_delay_applies_symmetric_jitter() -> None:
    low = compute_reconnect_delay(2, base=1.0, cap=30.0, jitter_pct=0.25, random_value=0.0)
    high = compute_reconnect_delay(2, base=1.0, cap=30.0, jitter_pct=0.25, random_value=1.0)

    assert low == 3.0
    assert high == 5.0
