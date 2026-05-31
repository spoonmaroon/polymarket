from datetime import datetime, timezone

import pytest

from polymarket_engine.features.asof_inputs import calculate_source_disagreement_bps, ensure_asof


def test_source_disagreement_bps_uses_worst_proxy_gap() -> None:
    assert calculate_source_disagreement_bps(100_000, [100_010, 99_950]) == 5.0


def test_source_disagreement_bps_rejects_nonpositive_primary_price() -> None:
    with pytest.raises(ValueError, match="primary_price must be positive"):
        calculate_source_disagreement_bps(0, [100_010])


def test_ensure_asof_rejects_future_data() -> None:
    asof_ts = datetime(2026, 5, 31, 20, 0, 0, tzinfo=timezone.utc)
    future_ts = datetime(2026, 5, 31, 20, 0, 1, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="after asof_ts"):
        ensure_asof(future_ts, asof_ts, "price_tick")
