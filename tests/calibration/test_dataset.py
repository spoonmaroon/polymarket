from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Protocol, cast

import pytest


ASOF_TS = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
EXPIRY_TS = ASOF_TS + timedelta(minutes=5)
EXPECTED_JSON_FIELDS = (
    "state_id",
    "contract_id",
    "market_slug",
    "asset",
    "side",
    "asof_ts",
    "expiry_ts",
    "tte_seconds",
    "k",
    "current_price",
    "distance_to_threshold",
    "z_path",
    "sigma_tau",
    "p_finish_mc",
    "p_no_touch_mc",
    "spread",
    "best_bid",
    "best_ask",
    "midpoint",
    "visible_depth",
    "orderbook_imbalance",
    "quote_age_ms",
    "source_age_ms",
    "volatility_regime",
    "probability_model_version",
    "skip_or_block_reason",
    "final_label",
    "resolved_outcome",
    "settlement_price_at_expiry",
)


JsonValue = str | int | float | bool | None


class CalibrationRow(Protocol):
    state_id: str

    def to_json_dict(self) -> dict[str, JsonValue]:
        raise NotImplementedError


RowFactory = Callable[..., CalibrationRow]
AppendRows = Callable[[Path, Sequence[CalibrationRow]], None]


def _dataset_module() -> ModuleType:
    try:
        return importlib.import_module("polymarket_engine.calibration.dataset")
    except ModuleNotFoundError as exc:
        pytest.fail(f"calibration dataset module is missing: {exc}")


def _row_factory() -> RowFactory:
    row_factory = getattr(_dataset_module(), "CalibrationDecisionRow", None)
    if not callable(row_factory):
        pytest.fail("CalibrationDecisionRow is not implemented")
    return cast(RowFactory, row_factory)


def _append_rows() -> AppendRows:
    append_rows = getattr(_dataset_module(), "append_calibration_rows", None)
    if not callable(append_rows):
        pytest.fail("append_calibration_rows is not implemented")
    return cast(AppendRows, append_rows)


def _default_dataset_path() -> Path:
    default_path = getattr(_dataset_module(), "DEFAULT_CALIBRATION_DATASET_PATH", None)
    if not isinstance(default_path, Path):
        pytest.fail("DEFAULT_CALIBRATION_DATASET_PATH is not implemented")
    return default_path


def _make_row(**overrides: Any) -> CalibrationRow:
    values: dict[str, Any] = {
        "state_id": "state-1",
        "contract_id": "condition-1",
        "market_slug": "btc-updown-5m-1781102700",
        "asset": "BTC",
        "side": "UP",
        "asof_ts": ASOF_TS,
        "expiry_ts": EXPIRY_TS,
        "tte_seconds": 300,
        "k": 65000.0,
        "current_price": 65123.45,
        "distance_to_threshold": 123.45,
        "z_path": 0.42,
        "sigma_tau": 0.015,
        "p_finish_mc": 0.71,
        "p_no_touch_mc": 0.64,
        "spread": 0.03,
        "best_bid": 0.68,
        "best_ask": 0.71,
        "midpoint": 0.695,
        "visible_depth": 1234.5,
        "orderbook_imbalance": -0.12,
        "quote_age_ms": 250.0,
        "source_age_ms": 1000.0,
        "volatility_regime": "normal",
        "probability_model_version": "mc-v1",
        "skip_or_block_reason": None,
    }
    values.update(overrides)
    return _row_factory()(**values)


def test_decision_row_serializes_replay_safe_shape_with_unresolved_labels() -> None:
    row = _make_row()

    payload = row.to_json_dict()

    assert tuple(payload) == EXPECTED_JSON_FIELDS
    assert payload["state_id"] == "state-1"
    assert payload["contract_id"] == "condition-1"
    assert payload["market_slug"] == "btc-updown-5m-1781102700"
    assert payload["asset"] == "BTC"
    assert payload["side"] == "UP"
    assert payload["asof_ts"] == "2026-06-10T12:00:00+00:00"
    assert payload["expiry_ts"] == "2026-06-10T12:05:00+00:00"
    assert payload["tte_seconds"] == 300
    assert payload["k"] == 65000.0
    assert payload["current_price"] == 65123.45
    assert payload["distance_to_threshold"] == 123.45
    assert payload["z_path"] == 0.42
    assert payload["sigma_tau"] == 0.015
    assert payload["p_finish_mc"] == 0.71
    assert payload["p_no_touch_mc"] == 0.64
    assert payload["spread"] == 0.03
    assert payload["best_bid"] == 0.68
    assert payload["best_ask"] == 0.71
    assert payload["midpoint"] == 0.695
    assert payload["visible_depth"] == 1234.5
    assert payload["orderbook_imbalance"] == -0.12
    assert payload["quote_age_ms"] == 250.0
    assert payload["source_age_ms"] == 1000.0
    assert payload["volatility_regime"] == "normal"
    assert payload["probability_model_version"] == "mc-v1"
    assert payload["skip_or_block_reason"] is None
    assert payload["final_label"] is None
    assert payload["resolved_outcome"] is None
    assert payload["settlement_price_at_expiry"] is None
    assert _default_dataset_path() == Path("data/research/calibration/asof_decision_states.jsonl")
    json.dumps(payload, allow_nan=False)


def test_append_calibration_rows_writes_jsonl_and_appends_in_order(tmp_path: Path) -> None:
    path = tmp_path / "research" / "calibration" / "asof_decision_states.jsonl"
    first = _make_row(state_id="state-1", asof_ts=ASOF_TS)
    second = _make_row(state_id="state-2", asof_ts=ASOF_TS + timedelta(seconds=1))

    _append_rows()(path, [first])
    _append_rows()(path, [second])

    lines = path.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert [record["state_id"] for record in records] == ["state-1", "state-2"]
    assert records[0]["asof_ts"] == "2026-06-10T12:00:00+00:00"
    assert records[1]["asof_ts"] == "2026-06-10T12:00:01+00:00"


def test_non_finite_numeric_values_are_sanitized_before_json_serialization(
    tmp_path: Path,
) -> None:
    row = _make_row(
        current_price=float("nan"),
        z_path=float("inf"),
        visible_depth=-float("inf"),
    )

    payload = row.to_json_dict()

    assert payload["current_price"] is None
    assert payload["z_path"] is None
    assert payload["visible_depth"] is None
    encoded = json.dumps(payload, allow_nan=False)
    assert "NaN" not in encoded
    assert "Infinity" not in encoded

    path = tmp_path / "calibration" / "rows.jsonl"
    _append_rows()(path, [row])
    written = path.read_text(encoding="utf-8")
    assert "NaN" not in written
    assert "Infinity" not in written
    assert json.loads(written)["current_price"] is None
