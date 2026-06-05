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


def test_runtime_monitor_shows_prior_derived_sensitivity_grid() -> None:
    source = (ROOT / "ui/src/App.tsx").read_text(encoding="utf-8")
    lowered = source.lower()
    contract_state_start = source.index("<h3>Contract State</h3>")
    contract_state_end = source.index("</section>", contract_state_start)
    sensitivity_grid = source.index("<PriorSensitivityGrid row={row} />", contract_state_start)

    assert "PriorSensitivityGrid" in source
    assert "prior_sensitivity" in source
    assert "Prior quantile" in source
    assert "dollar move" not in lowered
    assert "fixed move" not in lowered
    assert "price_delta" not in lowered
    assert sensitivity_grid < contract_state_end
