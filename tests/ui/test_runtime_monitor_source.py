from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_monitor_does_not_keep_graphable_rows_from_failed_payload() -> None:
    app_source = (ROOT / "ui/src/App.tsx").read_text(encoding="utf-8")
    helper_source = (ROOT / "ui/src/probabilityRows.ts").read_text(encoding="utf-8")

    assert "filterGraphableProbabilityRows(payload, nowMs)" in app_source
    assert "payload?.ok === false" not in helper_source
    assert "Monte Carlo rollover returned 0 rows" not in app_source
    assert "showing last" not in app_source


def test_runtime_monitor_filters_expired_or_invalid_probability_rows() -> None:
    source = (ROOT / "ui/src/probabilityRows.ts").read_text(encoding="utf-8")

    assert "function isGraphableProbabilityRow" in source
    assert "row.valid_until" in source
    assert "expiryMs > nowMs" in source
