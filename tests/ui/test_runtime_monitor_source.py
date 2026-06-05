from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_monitor_does_not_keep_graphable_rows_from_failed_payload() -> None:
    source = (ROOT / "ui/src/App.tsx").read_text(encoding="utf-8")

    assert "payload?.ok === false" in source
    assert "Monte Carlo rollover returned 0 rows" not in source
    assert "showing last" not in source


def test_runtime_monitor_filters_expired_or_invalid_probability_rows() -> None:
    source = (ROOT / "ui/src/App.tsx").read_text(encoding="utf-8")

    assert "function isGraphableProbabilityRow" in source
    assert "contractExpiryMs(row)" in source
    assert "row.valid_until" in source
    assert "expiryMs > nowMs" in source
