# Backtest Replay and XGBoost Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a replay-safe offline backtest engine and the first ML calibration track, ending with logistic and XGBoost probability calibrator artifacts that can be evaluated against executable fills.

**Architecture:** The calibration JSONL dataset becomes the common contract between replay, backtest, and ML. DuckDB exports as-of decision/probability rows plus final labels; the backtester consumes that dataset without touching live services; calibrators emit prediction JSONL that can be run through the same backtest ledger. Live trading stays disabled.

**Tech Stack:** Python 3.11, DuckDB, Polars/Numpy, pytest, existing `polymarket-engine` CLI, optional research dependency group for `scikit-learn` and `xgboost`.

---

## Scope And Boundaries

This is one linked project, not two separate projects, because XGBoost needs the replay-safe dataset and backtest ledger to be meaningful.

Do not add live order placement, private keys, or a paper daemon in this plan. The result is offline research software: export dataset, run replay/backtest, train calibrators, compare raw MC vs calibrated probability. A live paper daemon can be a follow-up once the offline path proves useful.

Strict replay rule: every feature column must be derived from data available at or before `asof_ts`. Future settlement and final outcome columns are labels only.

## File Structure

- Modify: `src/polymarket_engine/calibration/dataset.py`
  - Extend `CalibrationDecisionRow` to match `docs/observations_2.md` Phase 1 fields.
  - Keep strict JSON serialization and timezone-aware timestamps.
- Create: `src/polymarket_engine/calibration/export.py`
  - Read DuckDB `features.asof_state_inputs`, `features.ensemble_decisions`, `features.probability_outputs`, `core.contracts`, `core.price_ticks`, and `validation.market_outcome_history`.
  - Produce labeled `CalibrationDecisionRow` records without using label fields as features.
- Create: `src/polymarket_engine/calibration/splits.py`
  - Walk-forward train/validation split helpers with purge and embargo.
- Create: `src/polymarket_engine/backtest/__init__.py`
  - Public exports for the backtest package.
- Create: `src/polymarket_engine/backtest/fills.py`
  - Conservative entry fill and hold-to-expiry PnL math.
- Create: `src/polymarket_engine/backtest/report.py`
  - Aggregate backtest metrics and strict JSON report shape.
- Create: `src/polymarket_engine/backtest/runner.py`
  - Load dataset JSONL, simulate trades, write report JSON.
- Create: `src/polymarket_engine/calibration/features.py`
  - Deterministic feature vector extraction for logistic and XGBoost models.
- Create: `src/polymarket_engine/calibration/logistic.py`
  - Pure NumPy logistic calibration baseline.
- Create: `src/polymarket_engine/calibration/xgboost_model.py`
  - Optional XGBoost training/prediction wrapper with a clear missing-dependency error.
- Create: `src/polymarket_engine/calibration/train.py`
  - Train calibrators and write prediction JSONL.
- Modify: `src/polymarket_engine/cli.py`
  - Add `export-calibration-dataset`, `run-backtest`, and `train-calibrator`.
  - Add `--probability-field` to `calibration-report`.
- Modify: `pyproject.toml`
  - Add a `research` dependency group for `scikit-learn` and `xgboost`.
- Tests:
  - Modify: `tests/calibration/test_dataset.py`
  - Modify: `tests/calibration/test_reports.py`
  - Modify: `tests/test_cli.py`
  - Create: `tests/calibration/test_export.py`
  - Create: `tests/calibration/test_splits.py`
  - Create: `tests/backtest/test_fills.py`
  - Create: `tests/backtest/test_runner.py`
  - Create: `tests/calibration/test_features.py`
  - Create: `tests/calibration/test_logistic.py`
  - Create: `tests/calibration/test_xgboost_model.py`
  - Create: `tests/calibration/test_train.py`

## Subagent Delegation

- Agent A: dataset/export contract. Owns Tasks 1-2.
- Agent B: backtest ledger/report. Owns Tasks 3-5.
- Agent C: calibration modeling. Owns Tasks 6-8.
- Agent D: CLI/docs/verification pass. Owns Task 9.

## Risk Areas

- Leakage: exporter must never use future prices, future Polymarket quotes, or final settlement as feature values.
- Label availability: `validation.market_outcome_history` can be missing official winners for recent contracts; exporter must skip unlabeled rows unless `--include-unlabeled` is set.
- Dependency bloat: XGBoost belongs in a research dependency group, not the live runtime dependencies.
- Overlap leakage: nearby 5-minute decision states are correlated; model validation must be walk-forward with purge/embargo.
- False confidence: backtest must report calibration and PnL separately. High PnL on tiny sample counts is not proof.

---

### Task 1: Extend Calibration Dataset Contract

**Files:**
- Modify: `src/polymarket_engine/calibration/dataset.py`
- Modify: `tests/calibration/test_dataset.py`

- [ ] **Step 1: Write the failing dataset-shape test**

Replace `EXPECTED_JSON_FIELDS` in `tests/calibration/test_dataset.py` with:

```python
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
    "k_source",
    "rule_hash",
    "current_price",
    "distance_to_threshold",
    "z_path",
    "sigma_tau",
    "sigma_valid",
    "sigma_age_ms",
    "short_realized_vol",
    "medium_realized_vol",
    "long_realized_vol",
    "volatility_regime",
    "p_finish_mc",
    "p_no_touch_mc",
    "mc_generator_dispersion",
    "spread",
    "best_bid",
    "best_ask",
    "midpoint",
    "target_size_ask_vwap",
    "target_size_bid_vwap",
    "visible_depth",
    "orderbook_imbalance",
    "quote_age_ms",
    "source_age_ms",
    "source_disagreement",
    "threshold_cross_count",
    "near_threshold_congestion",
    "recent_wick_size",
    "event_window_flag",
    "probability_model_version",
    "feature_version",
    "runtime_phase",
    "offload_allowed",
    "skip_or_block_reason",
    "final_label",
    "resolved_outcome",
    "settlement_price_at_expiry",
)
```

Extend `_make_row(...)` defaults in the same file:

```python
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
    "k_source": "polymarket_rtds_chainlink",
    "rule_hash": "rule-hash-1",
    "current_price": 65123.45,
    "distance_to_threshold": 123.45,
    "z_path": 0.42,
    "sigma_tau": 0.015,
    "sigma_valid": True,
    "sigma_age_ms": 900.0,
    "short_realized_vol": 0.01,
    "medium_realized_vol": 0.012,
    "long_realized_vol": 0.014,
    "volatility_regime": "normal",
    "p_finish_mc": 0.71,
    "p_no_touch_mc": 0.64,
    "mc_generator_dispersion": 0.04,
    "spread": 0.03,
    "best_bid": 0.68,
    "best_ask": 0.71,
    "midpoint": 0.695,
    "target_size_ask_vwap": 0.715,
    "target_size_bid_vwap": 0.675,
    "visible_depth": 1234.5,
    "orderbook_imbalance": -0.12,
    "quote_age_ms": 250.0,
    "source_age_ms": 1000.0,
    "source_disagreement": 1.4,
    "threshold_cross_count": 2,
    "near_threshold_congestion": 7,
    "recent_wick_size": 0.0008,
    "event_window_flag": "regular",
    "probability_model_version": "mc-v1",
    "feature_version": "calibration-features-v2",
    "runtime_phase": "READY",
    "offload_allowed": True,
    "skip_or_block_reason": None,
}
```

Add assertions to `test_decision_row_serializes_replay_safe_shape_with_unresolved_labels`:

```python
assert payload["k_source"] == "polymarket_rtds_chainlink"
assert payload["rule_hash"] == "rule-hash-1"
assert payload["sigma_valid"] is True
assert payload["sigma_age_ms"] == 900.0
assert payload["short_realized_vol"] == 0.01
assert payload["medium_realized_vol"] == 0.012
assert payload["long_realized_vol"] == 0.014
assert payload["mc_generator_dispersion"] == 0.04
assert payload["target_size_ask_vwap"] == 0.715
assert payload["target_size_bid_vwap"] == 0.675
assert payload["source_disagreement"] == 1.4
assert payload["threshold_cross_count"] == 2
assert payload["near_threshold_congestion"] == 7
assert payload["recent_wick_size"] == 0.0008
assert payload["event_window_flag"] == "regular"
assert payload["feature_version"] == "calibration-features-v2"
assert payload["runtime_phase"] == "READY"
assert payload["offload_allowed"] is True
```

- [ ] **Step 2: Run the dataset test to verify RED**

Run:

```bash
uv run pytest tests/calibration/test_dataset.py::test_decision_row_serializes_replay_safe_shape_with_unresolved_labels -q
```

Expected: FAIL because `CalibrationDecisionRow.__init__` does not accept fields such as `k_source`, `sigma_valid`, and `feature_version`.

- [ ] **Step 3: Extend `CalibrationDecisionRow`**

Replace the dataclass field block and `to_json_dict(...)` in `src/polymarket_engine/calibration/dataset.py` with this shape:

```python
@dataclass(frozen=True)
class CalibrationDecisionRow:
    state_id: str
    contract_id: str
    market_slug: str
    asset: str
    side: str
    asof_ts: datetime
    expiry_ts: datetime
    tte_seconds: int
    k: float
    k_source: str
    rule_hash: str
    current_price: float
    distance_to_threshold: float
    z_path: float
    sigma_tau: float
    sigma_valid: bool
    sigma_age_ms: float
    short_realized_vol: float
    medium_realized_vol: float
    long_realized_vol: float
    volatility_regime: str
    p_finish_mc: float
    p_no_touch_mc: float
    mc_generator_dispersion: float
    spread: float
    best_bid: float
    best_ask: float
    midpoint: float
    target_size_ask_vwap: float
    target_size_bid_vwap: float
    visible_depth: float
    orderbook_imbalance: float
    quote_age_ms: float
    source_age_ms: float
    source_disagreement: float
    threshold_cross_count: int
    near_threshold_congestion: int
    recent_wick_size: float
    event_window_flag: str
    probability_model_version: str
    feature_version: str
    runtime_phase: str
    offload_allowed: bool
    skip_or_block_reason: str | None = None
    final_label: int | None = None
    resolved_outcome: str | None = None
    settlement_price_at_expiry: float | None = None

    def to_json_dict(self) -> dict[str, JsonValue]:
        return {
            "state_id": self.state_id,
            "contract_id": self.contract_id,
            "market_slug": self.market_slug,
            "asset": self.asset,
            "side": self.side,
            "asof_ts": _iso_timestamp(self.asof_ts),
            "expiry_ts": _iso_timestamp(self.expiry_ts),
            "tte_seconds": self.tte_seconds,
            "k": _finite_float_or_none(self.k),
            "k_source": self.k_source,
            "rule_hash": self.rule_hash,
            "current_price": _finite_float_or_none(self.current_price),
            "distance_to_threshold": _finite_float_or_none(self.distance_to_threshold),
            "z_path": _finite_float_or_none(self.z_path),
            "sigma_tau": _finite_float_or_none(self.sigma_tau),
            "sigma_valid": bool(self.sigma_valid),
            "sigma_age_ms": _finite_float_or_none(self.sigma_age_ms),
            "short_realized_vol": _finite_float_or_none(self.short_realized_vol),
            "medium_realized_vol": _finite_float_or_none(self.medium_realized_vol),
            "long_realized_vol": _finite_float_or_none(self.long_realized_vol),
            "volatility_regime": self.volatility_regime,
            "p_finish_mc": _finite_float_or_none(self.p_finish_mc),
            "p_no_touch_mc": _finite_float_or_none(self.p_no_touch_mc),
            "mc_generator_dispersion": _finite_float_or_none(self.mc_generator_dispersion),
            "spread": _finite_float_or_none(self.spread),
            "best_bid": _finite_float_or_none(self.best_bid),
            "best_ask": _finite_float_or_none(self.best_ask),
            "midpoint": _finite_float_or_none(self.midpoint),
            "target_size_ask_vwap": _finite_float_or_none(self.target_size_ask_vwap),
            "target_size_bid_vwap": _finite_float_or_none(self.target_size_bid_vwap),
            "visible_depth": _finite_float_or_none(self.visible_depth),
            "orderbook_imbalance": _finite_float_or_none(self.orderbook_imbalance),
            "quote_age_ms": _finite_float_or_none(self.quote_age_ms),
            "source_age_ms": _finite_float_or_none(self.source_age_ms),
            "source_disagreement": _finite_float_or_none(self.source_disagreement),
            "threshold_cross_count": self.threshold_cross_count,
            "near_threshold_congestion": self.near_threshold_congestion,
            "recent_wick_size": _finite_float_or_none(self.recent_wick_size),
            "event_window_flag": self.event_window_flag,
            "probability_model_version": self.probability_model_version,
            "feature_version": self.feature_version,
            "runtime_phase": self.runtime_phase,
            "offload_allowed": bool(self.offload_allowed),
            "skip_or_block_reason": self.skip_or_block_reason,
            "final_label": self.final_label,
            "resolved_outcome": self.resolved_outcome,
            "settlement_price_at_expiry": _finite_float_or_none(
                self.settlement_price_at_expiry
            ),
        }
```

- [ ] **Step 4: Run dataset tests to verify GREEN**

Run:

```bash
uv run pytest tests/calibration/test_dataset.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```bash
git add src/polymarket_engine/calibration/dataset.py tests/calibration/test_dataset.py
git commit -m "feat(calibration): extend replay-safe dataset fields"
```

---

### Task 2: Export Replay-Safe Labeled Dataset From DuckDB

**Files:**
- Create: `src/polymarket_engine/calibration/export.py`
- Modify: `src/polymarket_engine/cli.py`
- Create: `tests/calibration/test_export.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write exporter tests**

Create `tests/calibration/test_export.py`:

```python
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

from polymarket_engine.calibration.export import (
    CalibrationExportConfig,
    export_calibration_dataset,
)
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


ASOF = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)
START = ASOF - timedelta(minutes=1)
EXPIRY = ASOF + timedelta(minutes=4)


def _seed_export_db(path: Path) -> None:
    store = DuckDbIngestStore(path)
    store.apply_schema()
    with duckdb.connect(str(path)) as conn:
        conn.execute(
            """
            insert into core.contracts values (
              'contract-up','polymarket','market-1','condition-1',
              'btc-updown-5m-1781102700','BTC','UP','token-up',
              'price',65000.0,'>',?,?,?,?,?,'BTC/USD','rule text','rule-hash-1',
              'parser-v1',?,?
            )
            """,
            [
                START,
                EXPIRY,
                EXPIRY,
                "Chainlink",
                "chainlink-url",
                ASOF - timedelta(minutes=2),
                ASOF - timedelta(minutes=2),
            ],
        )
        conn.execute(
            """
            insert into features.asof_state_inputs values (
              'state-1','contract-up',?,'BTC','UP',65000.0,
              'polymarket_rtds_chainlink',?,?,300.0,65123.45,
              'polymarket_rtds_chainlink',?,?,'{}',1.4,0.68,0.71,0.71,0.03,
              ?,?,250.0,1000.0,1000.0,250.0,250.0,'{}',
              0.01,0.012,0.014,0.015,'normal','[]',?
            )
            """,
            [
                ASOF,
                START,
                START,
                ASOF - timedelta(seconds=1),
                ASOF - timedelta(seconds=1),
                ASOF - timedelta(milliseconds=250),
                ASOF - timedelta(milliseconds=250),
                ASOF,
            ],
        )
        conn.execute(
            """
            insert into features.ensemble_decisions values (
              'decision-1','state-1','contract-up',?,'read_only','TRADE_CANDIDATE',
              0.71,0.64,0.42,0.02,0.03,'[]',
              '{"uncertainty_buffer":0.01}',
              '{"mc_dispersion":0.04}',
              '{"entry_vwap":0.715,"exit_vwap":0.675,"visible_depth":1234.5,"orderbook_imbalance":-0.12}',
              '{"supervised_live_action":"DISABLED"}',?
            )
            """,
            [ASOF, ASOF],
        )
        conn.execute(
            """
            insert into validation.market_outcome_history values (
              'market-1','condition-1','btc-updown-5m-1781102700','BTC','5m',
              ?,?,'token-up','token-down',65000.0,?,?,65100.0,?,?,
              'UP','computed',?,'UP','token-up','resolved','clob',?,
              'rule-hash-1',false,?
            )
            """,
            [
                START,
                EXPIRY,
                START,
                START,
                EXPIRY,
                EXPIRY,
                EXPIRY,
                EXPIRY,
                EXPIRY,
            ],
        )
        for offset, price in ((-20, 64990.0), (-10, 65010.0), (-5, 64998.0), (-1, 65123.45)):
            ts = ASOF + timedelta(seconds=offset)
            conn.execute(
                "insert into core.price_ticks values ('polymarket_rtds_chainlink','BTC/USD',?,?,?,null,null,null,null)",
                [ts, ts, price],
            )


def test_export_calibration_dataset_writes_labeled_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "poly.duckdb"
    out_path = tmp_path / "calibration.jsonl"
    _seed_export_db(db_path)

    result = export_calibration_dataset(
        CalibrationExportConfig(
            duckdb_path=db_path,
            out_path=out_path,
            start_ts=ASOF - timedelta(minutes=5),
            end_ts=ASOF + timedelta(minutes=1),
            include_unlabeled=False,
            limit=100,
        )
    )

    assert result.rows_written == 1
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["state_id"] == "state-1"
    assert payload["final_label"] == 1
    assert payload["resolved_outcome"] == "UP"
    assert payload["settlement_price_at_expiry"] == 65100.0
    assert payload["p_finish_mc"] == 0.71
    assert payload["mc_generator_dispersion"] == 0.04
    assert payload["target_size_ask_vwap"] == 0.715
    assert payload["target_size_bid_vwap"] == 0.675
    assert payload["threshold_cross_count"] == 3
    assert payload["near_threshold_congestion"] >= 1
    assert payload["event_window_flag"] == "regular"
    assert payload["feature_version"] == "calibration-features-v2"


def test_export_skips_unlabeled_rows_by_default(tmp_path: Path) -> None:
    db_path = tmp_path / "poly.duckdb"
    out_path = tmp_path / "calibration.jsonl"
    _seed_export_db(db_path)
    with duckdb.connect(str(db_path)) as conn:
        conn.execute("delete from validation.market_outcome_history")

    result = export_calibration_dataset(
        CalibrationExportConfig(
            duckdb_path=db_path,
            out_path=out_path,
            start_ts=None,
            end_ts=None,
            include_unlabeled=False,
            limit=100,
        )
    )

    assert result.rows_written == 0
    assert out_path.read_text(encoding="utf-8") == ""
```

Add CLI parser coverage to `tests/test_cli.py`:

```python
def test_parse_export_calibration_dataset_args() -> None:
    args = parse_args(
        [
            "export-calibration-dataset",
            "--duckdb-path",
            "data/db/polymarket.duckdb",
            "--out",
            "data/research/calibration/asof_decision_states.jsonl",
            "--start-ts",
            "2026-06-10T00:00:00+00:00",
            "--end-ts",
            "2026-06-11T00:00:00+00:00",
            "--limit",
            "500",
        ]
    )

    assert args.command == "export-calibration-dataset"
    assert args.duckdb_path == Path("data/db/polymarket.duckdb")
    assert args.out == Path("data/research/calibration/asof_decision_states.jsonl")
    assert args.start_ts == "2026-06-10T00:00:00+00:00"
    assert args.end_ts == "2026-06-11T00:00:00+00:00"
    assert args.limit == 500
    assert args.include_unlabeled is False
```

- [ ] **Step 2: Run exporter tests to verify RED**

Run:

```bash
uv run pytest tests/calibration/test_export.py tests/test_cli.py::test_parse_export_calibration_dataset_args -q
```

Expected: FAIL because `polymarket_engine.calibration.export` and the CLI command do not exist.

- [ ] **Step 3: Implement `calibration/export.py`**

Create `src/polymarket_engine/calibration/export.py`:

```python
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb

from polymarket_engine.calibration.dataset import CalibrationDecisionRow
from polymarket_engine.calibration.dataset import append_calibration_rows


FEATURE_VERSION = "calibration-features-v2"


@dataclass(frozen=True)
class CalibrationExportConfig:
    duckdb_path: Path
    out_path: Path
    start_ts: datetime | None
    end_ts: datetime | None
    include_unlabeled: bool
    limit: int


@dataclass(frozen=True)
class CalibrationExportResult:
    rows_written: int
    out_path: str

    def to_json_dict(self) -> dict[str, object]:
        return {"rows_written": self.rows_written, "out_path": self.out_path}


def export_calibration_dataset(config: CalibrationExportConfig) -> CalibrationExportResult:
    if config.limit <= 0:
        raise ValueError("limit must be positive")
    rows: list[CalibrationDecisionRow] = []
    with duckdb.connect(str(config.duckdb_path), read_only=True) as conn:
        for payload in _candidate_rows(conn, config):
            row = _row_from_payload(conn, payload, include_unlabeled=config.include_unlabeled)
            if row is not None:
                rows.append(row)
    config.out_path.parent.mkdir(parents=True, exist_ok=True)
    config.out_path.write_text("", encoding="utf-8")
    append_calibration_rows(config.out_path, rows)
    return CalibrationExportResult(rows_written=len(rows), out_path=str(config.out_path))


def _candidate_rows(
    conn: duckdb.DuckDBPyConnection,
    config: CalibrationExportConfig,
) -> tuple[dict[str, Any], ...]:
    filters = []
    params: list[object] = []
    if config.start_ts is not None:
        filters.append("s.asof_ts >= ?")
        params.append(config.start_ts)
    if config.end_ts is not None:
        filters.append("s.asof_ts < ?")
        params.append(config.end_ts)
    where_sql = " where " + " and ".join(filters) if filters else ""
    query = f"""
        select
            s.state_id,
            s.contract_id,
            c.market_id,
            c.slug as market_slug,
            c.asset,
            c.side,
            s.asof_ts::VARCHAR as asof_ts,
            c.expiry_ts::VARCHAR as expiry_ts,
            c.start_ts::VARCHAR as start_ts,
            s.seconds_left,
            s.threshold,
            s.threshold_source_key,
            c.rule_hash,
            s.settlement_price,
            s.source_disagreement_bps,
            s.sigma_tau,
            s.source_age_ms,
            s.short_realized_vol,
            s.medium_realized_vol,
            s.long_realized_vol,
            s.volatility_regime,
            s.best_bid,
            s.best_ask,
            s.spread,
            s.quote_age_ms,
            s.data_quality_flags_json,
            e.p_finish,
            e.p_no_touch,
            e.z_path,
            e.decision_hint,
            e.skip_reasons_json,
            e.generator_summary_json,
            e.execution_summary_json,
            h.official_winner,
            h.computed_winner,
            h.end_price,
            h.official_resolution_status
        from features.asof_state_inputs as s
        join core.contracts as c on c.contract_id = s.contract_id
        left join features.ensemble_decisions as e on e.state_id = s.state_id
        left join validation.market_outcome_history as h on h.market_id = c.market_id
        {where_sql}
        order by s.asof_ts asc, s.state_id asc
        limit ?
    """
    params.append(config.limit)
    columns = [column[0] for column in conn.execute(query, params).description]
    return tuple(dict(zip(columns, row, strict=True)) for row in conn.fetchall())


def _row_from_payload(
    conn: duckdb.DuckDBPyConnection,
    payload: dict[str, Any],
    *,
    include_unlabeled: bool,
) -> CalibrationDecisionRow | None:
    asof_ts = _parse_ts(payload["asof_ts"])
    expiry_ts = _parse_ts(payload["expiry_ts"])
    start_ts = _parse_ts(payload["start_ts"])
    winner = payload.get("official_winner") or payload.get("computed_winner")
    if winner not in {"UP", "DOWN"} and not include_unlabeled:
        return None
    label = None if winner not in {"UP", "DOWN"} else int(str(payload["side"]) == winner)
    best_bid = _float(payload.get("best_bid"), 0.0)
    best_ask = _float(payload.get("best_ask"), 0.0)
    spread = _float(payload.get("spread"), max(0.0, best_ask - best_bid))
    midpoint = (best_bid + best_ask) / 2.0 if best_bid or best_ask else 0.0
    execution = _json_object(payload.get("execution_summary_json"))
    generator = _json_object(payload.get("generator_summary_json"))
    threshold = _float(payload.get("threshold"), 0.0)
    current_price = _float(payload.get("settlement_price"), 0.0)
    side = str(payload["side"])
    distance = current_price - threshold
    if side == "DOWN":
        distance *= -1.0
    return CalibrationDecisionRow(
        state_id=str(payload["state_id"]),
        contract_id=str(payload["contract_id"]),
        market_slug=str(payload["market_slug"]),
        asset=str(payload["asset"]),
        side=side,
        asof_ts=asof_ts,
        expiry_ts=expiry_ts,
        tte_seconds=max(0, int(float(payload["seconds_left"]))),
        k=threshold,
        k_source=str(payload.get("threshold_source_key") or ""),
        rule_hash=str(payload.get("rule_hash") or ""),
        current_price=current_price,
        distance_to_threshold=distance,
        z_path=_float(payload.get("z_path"), _z_path(current_price, threshold, payload.get("sigma_tau"), side)),
        sigma_tau=_float(payload.get("sigma_tau"), 0.0),
        sigma_valid=_float(payload.get("sigma_tau"), 0.0) > 0.0,
        sigma_age_ms=_float(payload.get("source_age_ms"), 0.0),
        short_realized_vol=_float(payload.get("short_realized_vol"), 0.0),
        medium_realized_vol=_float(payload.get("medium_realized_vol"), 0.0),
        long_realized_vol=_float(payload.get("long_realized_vol"), 0.0),
        volatility_regime=str(payload.get("volatility_regime") or "unknown"),
        p_finish_mc=_float(payload.get("p_finish"), 0.0),
        p_no_touch_mc=_float(payload.get("p_no_touch"), 0.0),
        mc_generator_dispersion=_float(generator.get("mc_dispersion"), 0.0),
        spread=spread,
        best_bid=best_bid,
        best_ask=best_ask,
        midpoint=midpoint,
        target_size_ask_vwap=_float(execution.get("entry_vwap"), best_ask),
        target_size_bid_vwap=_float(execution.get("exit_vwap"), best_bid),
        visible_depth=_float(execution.get("visible_depth"), 0.0),
        orderbook_imbalance=_float(execution.get("orderbook_imbalance"), 0.0),
        quote_age_ms=_float(payload.get("quote_age_ms"), 0.0),
        source_age_ms=_float(payload.get("source_age_ms"), 0.0),
        source_disagreement=_float(payload.get("source_disagreement_bps"), 0.0),
        threshold_cross_count=_threshold_cross_count(conn, payload, start_ts, asof_ts, threshold),
        near_threshold_congestion=_near_threshold_congestion(conn, payload, asof_ts, threshold),
        recent_wick_size=_recent_wick_size(conn, payload, asof_ts, current_price),
        event_window_flag="final_60s" if (expiry_ts - asof_ts).total_seconds() <= 60 else "regular",
        probability_model_version="ensemble-mc-v1",
        feature_version=FEATURE_VERSION,
        runtime_phase="READY",
        offload_allowed=True,
        skip_or_block_reason=_block_reason(payload),
        final_label=label,
        resolved_outcome=winner if winner in {"UP", "DOWN"} else None,
        settlement_price_at_expiry=_float(payload.get("end_price"), None),
    )


def _threshold_cross_count(
    conn: duckdb.DuckDBPyConnection,
    payload: dict[str, Any],
    start_ts: datetime,
    asof_ts: datetime,
    threshold: float,
) -> int:
    row = conn.execute(
        """
        with signed as (
            select
                case when price >= ? then 1 else -1 end as side,
                lag(case when price >= ? then 1 else -1 end) over (order by event_ts, observed_ts) as prev_side
            from core.price_ticks
            where symbol = ? and event_ts >= ? and event_ts <= ?
        )
        select count(*) from signed where prev_side is not null and prev_side != side
        """,
        [threshold, threshold, f"{payload['asset']}/USD", start_ts, asof_ts],
    ).fetchone()
    return int(row[0] if row else 0)


def _near_threshold_congestion(
    conn: duckdb.DuckDBPyConnection,
    payload: dict[str, Any],
    asof_ts: datetime,
    threshold: float,
) -> int:
    tolerance = max(1.0, threshold * 0.0001)
    row = conn.execute(
        """
        select count(*)
        from core.price_ticks
        where symbol = ? and event_ts >= ? and event_ts <= ? and abs(price - ?) <= ?
        """,
        [f"{payload['asset']}/USD", asof_ts - timedelta(seconds=60), asof_ts, threshold, tolerance],
    ).fetchone()
    return int(row[0] if row else 0)


def _recent_wick_size(
    conn: duckdb.DuckDBPyConnection,
    payload: dict[str, Any],
    asof_ts: datetime,
    current_price: float,
) -> float:
    if current_price <= 0:
        return 0.0
    row = conn.execute(
        """
        select min(price), max(price)
        from core.price_ticks
        where symbol = ? and event_ts >= ? and event_ts <= ?
        """,
        [f"{payload['asset']}/USD", asof_ts - timedelta(seconds=60), asof_ts],
    ).fetchone()
    if not row or row[0] is None or row[1] is None:
        return 0.0
    return max(0.0, float(row[1]) - float(row[0])) / current_price


def _block_reason(payload: dict[str, Any]) -> str | None:
    quality_flags = _json_list(payload.get("data_quality_flags_json"))
    skip_reasons = _json_list(payload.get("skip_reasons_json"))
    if quality_flags:
        return ",".join(str(item) for item in quality_flags)
    if skip_reasons:
        return ",".join(str(item) for item in skip_reasons)
    return None


def _z_path(current_price: float, threshold: float, sigma: object, side: str) -> float:
    sigma_value = _float(sigma, 0.0)
    if current_price <= 0 or threshold <= 0 or sigma_value <= 0:
        return 0.0
    signed = math.log(current_price / threshold)
    if side == "DOWN":
        signed *= -1.0
    return signed / sigma_value


def _json_object(value: object) -> dict[str, object]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _json_list(value: object) -> list[object]:
    if not isinstance(value, str) or not value:
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return decoded if isinstance(decoded, list) else []


def _float(value: object, default: float | None) -> float:
    if value is None and default is None:
        return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = float(default or 0.0)
    return result if math.isfinite(result) else float(default or 0.0)


def _parse_ts(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
```

- [ ] **Step 4: Add CLI command**

In `src/polymarket_engine/cli.py`, add parser setup after `calibration-report`:

```python
    export_calibration = subparsers.add_parser("export-calibration-dataset")
    export_calibration.add_argument("--duckdb-path", type=Path, required=True)
    export_calibration.add_argument(
        "--out",
        type=Path,
        default=Path("data/research/calibration/asof_decision_states.jsonl"),
    )
    export_calibration.add_argument("--start-ts", default=None)
    export_calibration.add_argument("--end-ts", default=None)
    export_calibration.add_argument("--include-unlabeled", action="store_true")
    export_calibration.add_argument("--limit", type=int, default=10_000)
```

Add dispatch in `run_collect_command(...)`:

```python
    if args.command == "export-calibration-dataset":
        return _run_export_calibration_dataset(args)
```

Add helper:

```python
def _run_export_calibration_dataset(args: argparse.Namespace) -> int:
    from polymarket_engine.calibration.export import CalibrationExportConfig
    from polymarket_engine.calibration.export import export_calibration_dataset

    result = export_calibration_dataset(
        CalibrationExportConfig(
            duckdb_path=args.duckdb_path,
            out_path=args.out,
            start_ts=_parse_optional_cli_datetime(args.start_ts),
            end_ts=_parse_optional_cli_datetime(args.end_ts),
            include_unlabeled=args.include_unlabeled,
            limit=args.limit,
        )
    )
    print(json.dumps(result.to_json_dict(), sort_keys=True, separators=(",", ":")))
    return 0
```

Add parser utility near `_isoformat_optional(...)`:

```python
def _parse_optional_cli_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
```

If `timezone` is not imported in `cli.py`, extend the datetime import:

```python
from datetime import datetime, timezone
```

- [ ] **Step 5: Run exporter tests to verify GREEN**

Run:

```bash
uv run pytest tests/calibration/test_export.py tests/test_cli.py::test_parse_export_calibration_dataset_args -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```bash
git add src/polymarket_engine/calibration/export.py src/polymarket_engine/cli.py tests/calibration/test_export.py tests/test_cli.py
git commit -m "feat(calibration): export labeled replay dataset"
```

---

### Task 3: Add Walk-Forward Split Utilities

**Files:**
- Create: `src/polymarket_engine/calibration/splits.py`
- Create: `tests/calibration/test_splits.py`

- [ ] **Step 1: Write split tests**

Create `tests/calibration/test_splits.py`:

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polymarket_engine.calibration.splits import WalkForwardSplitConfig
from polymarket_engine.calibration.splits import walk_forward_splits


def _rows() -> list[dict[str, object]]:
    base = datetime(2026, 6, 1, tzinfo=timezone.utc)
    return [
        {"state_id": f"state-{idx}", "asof_ts": (base + timedelta(days=idx)).isoformat()}
        for idx in range(8)
    ]


def test_walk_forward_splits_use_past_train_and_future_validation() -> None:
    splits = walk_forward_splits(
        _rows(),
        WalkForwardSplitConfig(
            min_train_days=2,
            validation_days=2,
            purge_seconds=0,
            embargo_seconds=0,
        ),
    )

    assert len(splits) == 3
    assert [row["state_id"] for row in splits[0].train_rows] == ["state-0", "state-1"]
    assert [row["state_id"] for row in splits[0].validation_rows] == ["state-2", "state-3"]
    assert [row["state_id"] for row in splits[1].train_rows] == [
        "state-0",
        "state-1",
        "state-2",
        "state-3",
    ]
    assert [row["state_id"] for row in splits[1].validation_rows] == ["state-4", "state-5"]


def test_walk_forward_splits_apply_purge_and_embargo() -> None:
    base = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
    rows = [
        {"state_id": "train-safe", "asof_ts": (base - timedelta(minutes=10)).isoformat()},
        {"state_id": "purged", "asof_ts": (base - timedelta(seconds=30)).isoformat()},
        {"state_id": "validate", "asof_ts": base.isoformat()},
        {"state_id": "embargoed", "asof_ts": (base + timedelta(seconds=30)).isoformat()},
        {"state_id": "next-safe", "asof_ts": (base + timedelta(minutes=10)).isoformat()},
    ]

    splits = walk_forward_splits(
        rows,
        WalkForwardSplitConfig(
            min_train_days=0,
            validation_days=1,
            purge_seconds=60,
            embargo_seconds=60,
        ),
    )

    split = splits[0]
    assert [row["state_id"] for row in split.train_rows] == ["train-safe"]
    assert [row["state_id"] for row in split.validation_rows] == ["validate"]
```

- [ ] **Step 2: Run split tests to verify RED**

Run:

```bash
uv run pytest tests/calibration/test_splits.py -q
```

Expected: FAIL because `calibration.splits` does not exist.

- [ ] **Step 3: Implement split utilities**

Create `src/polymarket_engine/calibration/splits.py`:

```python
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


JsonRow = Mapping[str, object]


@dataclass(frozen=True)
class WalkForwardSplitConfig:
    min_train_days: int
    validation_days: int
    purge_seconds: int
    embargo_seconds: int


@dataclass(frozen=True)
class WalkForwardSplit:
    split_index: int
    train_rows: tuple[JsonRow, ...]
    validation_rows: tuple[JsonRow, ...]
    train_start: datetime | None
    train_end: datetime | None
    validation_start: datetime
    validation_end: datetime


def walk_forward_splits(
    rows: Sequence[JsonRow],
    config: WalkForwardSplitConfig,
) -> tuple[WalkForwardSplit, ...]:
    if config.validation_days <= 0:
        raise ValueError("validation_days must be positive")
    if config.min_train_days < 0:
        raise ValueError("min_train_days must be nonnegative")
    ordered = tuple(sorted(rows, key=lambda row: _asof(row)))
    if not ordered:
        return ()
    first_ts = _floor_day(_asof(ordered[0]))
    last_ts = _asof(ordered[-1])
    split_start = first_ts + timedelta(days=config.min_train_days)
    validation_span = timedelta(days=config.validation_days)
    purge = timedelta(seconds=config.purge_seconds)
    embargo = timedelta(seconds=config.embargo_seconds)
    splits: list[WalkForwardSplit] = []
    split_index = 0
    while split_start <= last_ts:
        validation_start = split_start
        validation_end = validation_start + validation_span
        train_cutoff = validation_start - purge
        embargo_end = validation_end + embargo
        train_rows = tuple(row for row in ordered if _asof(row) < train_cutoff)
        validation_rows = tuple(
            row
            for row in ordered
            if validation_start <= _asof(row) < validation_end
        )
        if train_rows and validation_rows:
            splits.append(
                WalkForwardSplit(
                    split_index=split_index,
                    train_rows=train_rows,
                    validation_rows=validation_rows,
                    train_start=_asof(train_rows[0]),
                    train_end=_asof(train_rows[-1]),
                    validation_start=validation_start,
                    validation_end=validation_end,
                )
            )
            split_index += 1
        split_start = embargo_end
    return tuple(splits)


def _asof(row: JsonRow) -> datetime:
    value = row.get("asof_ts")
    if not isinstance(value, str):
        raise ValueError("row asof_ts must be an ISO string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _floor_day(value: datetime) -> datetime:
    value = value.astimezone(timezone.utc)
    return value.replace(hour=0, minute=0, second=0, microsecond=0)
```

- [ ] **Step 4: Run split tests to verify GREEN**

Run:

```bash
uv run pytest tests/calibration/test_splits.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```bash
git add src/polymarket_engine/calibration/splits.py tests/calibration/test_splits.py
git commit -m "feat(calibration): add walk-forward splits"
```

---

### Task 4: Add Conservative Backtest Fill Ledger

**Files:**
- Create: `src/polymarket_engine/backtest/__init__.py`
- Create: `src/polymarket_engine/backtest/fills.py`
- Create: `tests/backtest/test_fills.py`

- [ ] **Step 1: Write fill tests**

Create `tests/backtest/test_fills.py`:

```python
from __future__ import annotations

from polymarket_engine.backtest.fills import BacktestFillConfig
from polymarket_engine.backtest.fills import simulate_hold_to_expiry_trade


def _row(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "state_id": "state-1",
        "contract_id": "contract-1",
        "asset": "BTC",
        "side": "UP",
        "asof_ts": "2026-06-10T12:00:00+00:00",
        "expiry_ts": "2026-06-10T12:05:00+00:00",
        "p_finish_mc": 0.72,
        "target_size_ask_vwap": 0.64,
        "target_size_bid_vwap": 0.62,
        "best_ask": 0.63,
        "best_bid": 0.61,
        "quote_age_ms": 250.0,
        "skip_or_block_reason": None,
        "final_label": 1,
    }
    values.update(overrides)
    return values


def test_simulate_hold_to_expiry_trade_uses_ask_vwap_for_entry() -> None:
    trade = simulate_hold_to_expiry_trade(
        _row(),
        BacktestFillConfig(stake_usd=100.0, min_edge=0.02, max_quote_age_ms=1000, fee_rate=0.0),
        probability_field="p_finish_mc",
    )

    assert trade is not None
    assert trade.state_id == "state-1"
    assert trade.entry_price == 0.64
    assert round(trade.shares, 6) == 156.25
    assert round(trade.pnl, 6) == 56.25
    assert round(trade.edge_at_entry, 6) == 0.08


def test_simulate_hold_to_expiry_trade_blocks_stale_quotes() -> None:
    trade = simulate_hold_to_expiry_trade(
        _row(quote_age_ms=2000.0),
        BacktestFillConfig(stake_usd=100.0, min_edge=0.02, max_quote_age_ms=1000, fee_rate=0.0),
        probability_field="p_finish_mc",
    )

    assert trade is None


def test_simulate_hold_to_expiry_trade_requires_positive_edge() -> None:
    trade = simulate_hold_to_expiry_trade(
        _row(p_finish_mc=0.65),
        BacktestFillConfig(stake_usd=100.0, min_edge=0.02, max_quote_age_ms=1000, fee_rate=0.0),
        probability_field="p_finish_mc",
    )

    assert trade is None


def test_simulate_hold_to_expiry_trade_loss_loses_stake() -> None:
    trade = simulate_hold_to_expiry_trade(
        _row(final_label=0),
        BacktestFillConfig(stake_usd=100.0, min_edge=0.02, max_quote_age_ms=1000, fee_rate=0.0),
        probability_field="p_finish_mc",
    )

    assert trade is not None
    assert trade.pnl == -100.0
```

- [ ] **Step 2: Run fill tests to verify RED**

Run:

```bash
uv run pytest tests/backtest/test_fills.py -q
```

Expected: FAIL because `polymarket_engine.backtest` does not exist.

- [ ] **Step 3: Implement fill ledger**

Create `src/polymarket_engine/backtest/__init__.py`:

```python
from polymarket_engine.backtest.fills import BacktestFillConfig
from polymarket_engine.backtest.fills import BacktestTrade
from polymarket_engine.backtest.fills import simulate_hold_to_expiry_trade

__all__ = [
    "BacktestFillConfig",
    "BacktestTrade",
    "simulate_hold_to_expiry_trade",
]
```

Create `src/polymarket_engine/backtest/fills.py`:

```python
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class BacktestFillConfig:
    stake_usd: float
    min_edge: float
    max_quote_age_ms: int
    fee_rate: float


@dataclass(frozen=True)
class BacktestTrade:
    state_id: str
    contract_id: str
    asset: str
    side: str
    asof_ts: str
    expiry_ts: str
    probability: float
    entry_price: float
    exit_bid_at_entry: float
    stake_usd: float
    shares: float
    final_label: int
    gross_payout: float
    fees: float
    pnl: float
    edge_at_entry: float

    def to_json_dict(self) -> dict[str, object]:
        return {
            "state_id": self.state_id,
            "contract_id": self.contract_id,
            "asset": self.asset,
            "side": self.side,
            "asof_ts": self.asof_ts,
            "expiry_ts": self.expiry_ts,
            "probability": self.probability,
            "entry_price": self.entry_price,
            "exit_bid_at_entry": self.exit_bid_at_entry,
            "stake_usd": self.stake_usd,
            "shares": self.shares,
            "final_label": self.final_label,
            "gross_payout": self.gross_payout,
            "fees": self.fees,
            "pnl": self.pnl,
            "edge_at_entry": self.edge_at_entry,
        }


def simulate_hold_to_expiry_trade(
    row: Mapping[str, object],
    config: BacktestFillConfig,
    *,
    probability_field: str,
) -> BacktestTrade | None:
    _validate_config(config)
    if row.get("skip_or_block_reason") is not None:
        return None
    quote_age_ms = _float(row.get("quote_age_ms"))
    if quote_age_ms > config.max_quote_age_ms:
        return None
    probability = _probability(row.get(probability_field), probability_field)
    entry_price = _probability(
        row.get("target_size_ask_vwap") or row.get("best_ask"),
        "target_size_ask_vwap",
    )
    exit_bid = _probability(
        row.get("target_size_bid_vwap") or row.get("best_bid"),
        "target_size_bid_vwap",
    )
    final_label = _label(row.get("final_label"))
    edge_at_entry = probability - entry_price - config.fee_rate
    if edge_at_entry < config.min_edge:
        return None
    shares = config.stake_usd / entry_price
    gross_payout = shares * float(final_label)
    fees = config.stake_usd * config.fee_rate
    pnl = gross_payout - config.stake_usd - fees
    return BacktestTrade(
        state_id=str(row["state_id"]),
        contract_id=str(row["contract_id"]),
        asset=str(row.get("asset") or ""),
        side=str(row.get("side") or ""),
        asof_ts=str(row["asof_ts"]),
        expiry_ts=str(row["expiry_ts"]),
        probability=probability,
        entry_price=entry_price,
        exit_bid_at_entry=exit_bid,
        stake_usd=config.stake_usd,
        shares=shares,
        final_label=final_label,
        gross_payout=gross_payout,
        fees=fees,
        pnl=pnl,
        edge_at_entry=edge_at_entry,
    )


def _validate_config(config: BacktestFillConfig) -> None:
    if config.stake_usd <= 0 or not math.isfinite(config.stake_usd):
        raise ValueError("stake_usd must be positive and finite")
    if config.min_edge < 0 or not math.isfinite(config.min_edge):
        raise ValueError("min_edge must be nonnegative and finite")
    if config.max_quote_age_ms < 0:
        raise ValueError("max_quote_age_ms must be nonnegative")
    if config.fee_rate < 0 or not math.isfinite(config.fee_rate):
        raise ValueError("fee_rate must be nonnegative and finite")


def _probability(value: object, field: str) -> float:
    number = _float(value)
    if number <= 0.0 or number > 1.0:
        raise ValueError(f"{field} must be in (0, 1]")
    return number


def _float(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean values are not numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("numeric field is required") from exc
    if not math.isfinite(number):
        raise ValueError("numeric field must be finite")
    return number


def _label(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    raise ValueError("final_label must be 0 or 1")
```

- [ ] **Step 4: Run fill tests to verify GREEN**

Run:

```bash
uv run pytest tests/backtest/test_fills.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```bash
git add src/polymarket_engine/backtest tests/backtest/test_fills.py
git commit -m "feat(backtest): add conservative fill ledger"
```

---

### Task 5: Add Backtest Report And CLI

**Files:**
- Create: `src/polymarket_engine/backtest/report.py`
- Create: `src/polymarket_engine/backtest/runner.py`
- Create: `tests/backtest/test_runner.py`
- Modify: `src/polymarket_engine/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write backtest runner tests**

Create `tests/backtest/test_runner.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from polymarket_engine.backtest.runner import BacktestRunConfig
from polymarket_engine.backtest.runner import run_backtest


def _record(state_id: str, probability: float, label: int) -> dict[str, object]:
    return {
        "state_id": state_id,
        "contract_id": f"contract-{state_id}",
        "asset": "BTC",
        "side": "UP",
        "asof_ts": "2026-06-10T12:00:00+00:00",
        "expiry_ts": "2026-06-10T12:05:00+00:00",
        "p_finish_mc": probability,
        "target_size_ask_vwap": 0.64,
        "target_size_bid_vwap": 0.62,
        "best_ask": 0.64,
        "best_bid": 0.62,
        "quote_age_ms": 250.0,
        "skip_or_block_reason": None,
        "final_label": label,
    }


def test_run_backtest_writes_report_and_trade_rows(tmp_path: Path) -> None:
    input_path = tmp_path / "dataset.jsonl"
    out_path = tmp_path / "backtest.json"
    input_path.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                _record("state-1", 0.72, 1),
                _record("state-2", 0.73, 0),
                _record("state-3", 0.60, 1),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    report = run_backtest(
        BacktestRunConfig(
            input_path=input_path,
            out_path=out_path,
            probability_field="p_finish_mc",
            stake_usd=100.0,
            min_edge=0.02,
            max_quote_age_ms=1000,
            fee_rate=0.0,
        )
    )

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert report.trade_count == 2
    assert payload["schema_version"] == "polymarket-backtest-report-v1"
    assert payload["input_row_count"] == 3
    assert payload["trade_count"] == 2
    assert payload["skipped_count"] == 1
    assert payload["win_rate"] == 0.5
    assert round(payload["total_pnl"], 6) == -43.75
    assert [trade["state_id"] for trade in payload["trades"]] == ["state-1", "state-2"]
```

Add CLI parser test:

```python
def test_parse_run_backtest_args() -> None:
    args = parse_args(
        [
            "run-backtest",
            "--input",
            "data/research/calibration/asof_decision_states.jsonl",
            "--out",
            "data/research/backtests/raw_mc.json",
            "--probability-field",
            "p_finish_mc",
            "--stake-usd",
            "100",
            "--min-edge",
            "0.02",
            "--max-quote-age-ms",
            "1000",
        ]
    )

    assert args.command == "run-backtest"
    assert args.input == Path("data/research/calibration/asof_decision_states.jsonl")
    assert args.out == Path("data/research/backtests/raw_mc.json")
    assert args.probability_field == "p_finish_mc"
    assert args.stake_usd == 100.0
    assert args.min_edge == 0.02
    assert args.max_quote_age_ms == 1000
```

- [ ] **Step 2: Run backtest runner tests to verify RED**

Run:

```bash
uv run pytest tests/backtest/test_runner.py tests/test_cli.py::test_parse_run_backtest_args -q
```

Expected: FAIL because `backtest.runner` and the CLI command do not exist.

- [ ] **Step 3: Implement report and runner**

Create `src/polymarket_engine/backtest/report.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from polymarket_engine.backtest.fills import BacktestTrade


@dataclass(frozen=True)
class BacktestReport:
    schema_version: str
    input_row_count: int
    trade_count: int
    skipped_count: int
    win_rate: float | None
    total_staked: float
    total_pnl: float
    roi: float | None
    mean_edge: float | None
    trades: tuple[BacktestTrade, ...]

    def to_json_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "input_row_count": self.input_row_count,
            "trade_count": self.trade_count,
            "skipped_count": self.skipped_count,
            "win_rate": self.win_rate,
            "total_staked": self.total_staked,
            "total_pnl": self.total_pnl,
            "roi": self.roi,
            "mean_edge": self.mean_edge,
            "trades": [trade.to_json_dict() for trade in self.trades],
        }


def build_backtest_report(
    *,
    input_row_count: int,
    trades: tuple[BacktestTrade, ...],
) -> BacktestReport:
    trade_count = len(trades)
    total_staked = sum(trade.stake_usd for trade in trades)
    total_pnl = sum(trade.pnl for trade in trades)
    wins = sum(1 for trade in trades if trade.final_label == 1)
    return BacktestReport(
        schema_version="polymarket-backtest-report-v1",
        input_row_count=input_row_count,
        trade_count=trade_count,
        skipped_count=input_row_count - trade_count,
        win_rate=(wins / trade_count) if trade_count else None,
        total_staked=total_staked,
        total_pnl=total_pnl,
        roi=(total_pnl / total_staked) if total_staked else None,
        mean_edge=(sum(trade.edge_at_entry for trade in trades) / trade_count)
        if trade_count
        else None,
        trades=trades,
    )
```

Create `src/polymarket_engine/backtest/runner.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from polymarket_engine.backtest.fills import BacktestFillConfig
from polymarket_engine.backtest.fills import simulate_hold_to_expiry_trade
from polymarket_engine.backtest.report import BacktestReport
from polymarket_engine.backtest.report import build_backtest_report
from polymarket_engine.calibration.reports import load_calibration_jsonl


@dataclass(frozen=True)
class BacktestRunConfig:
    input_path: Path
    out_path: Path
    probability_field: str
    stake_usd: float
    min_edge: float
    max_quote_age_ms: int
    fee_rate: float


def run_backtest(config: BacktestRunConfig) -> BacktestReport:
    rows = load_calibration_jsonl(config.input_path)
    fill_config = BacktestFillConfig(
        stake_usd=config.stake_usd,
        min_edge=config.min_edge,
        max_quote_age_ms=config.max_quote_age_ms,
        fee_rate=config.fee_rate,
    )
    trades = tuple(
        trade
        for row in rows
        if (
            trade := simulate_hold_to_expiry_trade(
                row,
                fill_config,
                probability_field=config.probability_field,
            )
        )
        is not None
    )
    report = build_backtest_report(input_row_count=len(rows), trades=trades)
    config.out_path.parent.mkdir(parents=True, exist_ok=True)
    config.out_path.write_text(
        json.dumps(report.to_json_dict(), allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report
```

- [ ] **Step 4: Add backtest CLI**

In `src/polymarket_engine/cli.py`, add parser setup:

```python
    run_backtest = subparsers.add_parser("run-backtest")
    run_backtest.add_argument("--input", type=Path, required=True)
    run_backtest.add_argument("--out", type=Path, required=True)
    run_backtest.add_argument("--probability-field", default="p_finish_mc")
    run_backtest.add_argument("--stake-usd", type=float, default=100.0)
    run_backtest.add_argument("--min-edge", type=float, default=0.02)
    run_backtest.add_argument("--max-quote-age-ms", type=int, default=1000)
    run_backtest.add_argument("--fee-rate", type=float, default=0.0)
```

Add dispatch:

```python
    if args.command == "run-backtest":
        return _run_backtest(args)
```

Add helper:

```python
def _run_backtest(args: argparse.Namespace) -> int:
    from polymarket_engine.backtest.runner import BacktestRunConfig
    from polymarket_engine.backtest.runner import run_backtest

    report = run_backtest(
        BacktestRunConfig(
            input_path=args.input,
            out_path=args.out,
            probability_field=args.probability_field,
            stake_usd=args.stake_usd,
            min_edge=args.min_edge,
            max_quote_age_ms=args.max_quote_age_ms,
            fee_rate=args.fee_rate,
        )
    )
    print(json.dumps(report.to_json_dict(), sort_keys=True, separators=(",", ":")))
    return 0
```

- [ ] **Step 5: Run backtest tests to verify GREEN**

Run:

```bash
uv run pytest tests/backtest/test_fills.py tests/backtest/test_runner.py tests/test_cli.py::test_parse_run_backtest_args -q
```

Expected: PASS.

- [ ] **Step 6: Commit Task 5**

```bash
git add src/polymarket_engine/backtest src/polymarket_engine/cli.py tests/backtest tests/test_cli.py
git commit -m "feat(backtest): add offline backtest runner"
```

---

### Task 6: Add Deterministic Feature Matrix

**Files:**
- Create: `src/polymarket_engine/calibration/features.py`
- Create: `tests/calibration/test_features.py`

- [ ] **Step 1: Write feature tests**

Create `tests/calibration/test_features.py`:

```python
from __future__ import annotations

from polymarket_engine.calibration.features import DEFAULT_FEATURE_NAMES
from polymarket_engine.calibration.features import feature_matrix


def _row(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "p_finish_mc": 0.8,
        "p_no_touch_mc": 0.6,
        "mc_generator_dispersion": 0.04,
        "tte_seconds": 120,
        "z_path": 0.7,
        "sigma_tau": 0.015,
        "distance_to_threshold": 50.0,
        "spread": 0.03,
        "orderbook_imbalance": -0.2,
        "visible_depth": 1000.0,
        "quote_age_ms": 250.0,
        "source_age_ms": 800.0,
        "threshold_cross_count": 2,
        "near_threshold_congestion": 4,
        "recent_wick_size": 0.001,
        "asset": "BTC",
        "side": "UP",
        "volatility_regime": "normal",
        "final_label": 1,
    }
    values.update(overrides)
    return values


def test_feature_matrix_uses_stable_feature_order() -> None:
    matrix, labels = feature_matrix([_row(), _row(asset="ETH", side="DOWN", final_label=0)])

    assert labels == [1, 0]
    assert len(matrix) == 2
    assert len(matrix[0]) == len(DEFAULT_FEATURE_NAMES)
    assert DEFAULT_FEATURE_NAMES[:4] == (
        "logit_p_finish_mc",
        "p_no_touch_mc",
        "mc_generator_dispersion",
        "tte_seconds",
    )
    assert matrix[0][DEFAULT_FEATURE_NAMES.index("asset_BTC")] == 1.0
    assert matrix[1][DEFAULT_FEATURE_NAMES.index("asset_ETH")] == 1.0
    assert matrix[0][DEFAULT_FEATURE_NAMES.index("side_UP")] == 1.0
    assert matrix[1][DEFAULT_FEATURE_NAMES.index("side_DOWN")] == 1.0


def test_feature_matrix_clips_logit_input() -> None:
    matrix, labels = feature_matrix([_row(p_finish_mc=1.0), _row(p_finish_mc=0.0, final_label=0)])

    assert labels == [1, 0]
    logit_index = DEFAULT_FEATURE_NAMES.index("logit_p_finish_mc")
    assert matrix[0][logit_index] < 20.0
    assert matrix[1][logit_index] > -20.0
```

- [ ] **Step 2: Run feature tests to verify RED**

Run:

```bash
uv run pytest tests/calibration/test_features.py -q
```

Expected: FAIL because `calibration.features` does not exist.

- [ ] **Step 3: Implement feature extraction**

Create `src/polymarket_engine/calibration/features.py`:

```python
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


DEFAULT_FEATURE_NAMES = (
    "logit_p_finish_mc",
    "p_no_touch_mc",
    "mc_generator_dispersion",
    "tte_seconds",
    "z_path",
    "sigma_tau",
    "distance_to_threshold",
    "spread",
    "orderbook_imbalance",
    "visible_depth",
    "quote_age_ms",
    "source_age_ms",
    "threshold_cross_count",
    "near_threshold_congestion",
    "recent_wick_size",
    "asset_BTC",
    "asset_ETH",
    "side_UP",
    "side_DOWN",
    "volatility_regime_low",
    "volatility_regime_normal",
    "volatility_regime_high",
    "volatility_regime_unknown",
)


def feature_matrix(
    rows: Sequence[Mapping[str, object]],
    *,
    feature_names: Sequence[str] = DEFAULT_FEATURE_NAMES,
) -> tuple[list[list[float]], list[int]]:
    matrix: list[list[float]] = []
    labels: list[int] = []
    for row in rows:
        label = _label(row.get("final_label"))
        matrix.append([_feature(row, name) for name in feature_names])
        labels.append(label)
    return matrix, labels


def prediction_matrix(
    rows: Sequence[Mapping[str, object]],
    *,
    feature_names: Sequence[str] = DEFAULT_FEATURE_NAMES,
) -> list[list[float]]:
    return [[_feature(row, name) for name in feature_names] for row in rows]


def _feature(row: Mapping[str, object], name: str) -> float:
    if name == "logit_p_finish_mc":
        return _logit(_prob(row.get("p_finish_mc")))
    if name in {
        "p_no_touch_mc",
        "mc_generator_dispersion",
        "tte_seconds",
        "z_path",
        "sigma_tau",
        "distance_to_threshold",
        "spread",
        "orderbook_imbalance",
        "visible_depth",
        "quote_age_ms",
        "source_age_ms",
        "threshold_cross_count",
        "near_threshold_congestion",
        "recent_wick_size",
    }:
        return _float(row.get(name))
    if name == "asset_BTC":
        return 1.0 if row.get("asset") == "BTC" else 0.0
    if name == "asset_ETH":
        return 1.0 if row.get("asset") == "ETH" else 0.0
    if name == "side_UP":
        return 1.0 if row.get("side") == "UP" else 0.0
    if name == "side_DOWN":
        return 1.0 if row.get("side") == "DOWN" else 0.0
    if name == "volatility_regime_low":
        return 1.0 if row.get("volatility_regime") == "low" else 0.0
    if name == "volatility_regime_normal":
        return 1.0 if row.get("volatility_regime") == "normal" else 0.0
    if name == "volatility_regime_high":
        return 1.0 if row.get("volatility_regime") == "high" else 0.0
    if name == "volatility_regime_unknown":
        return 1.0 if row.get("volatility_regime") not in {"low", "normal", "high"} else 0.0
    raise ValueError(f"unsupported feature name: {name}")


def _prob(value: object) -> float:
    return min(1.0 - 1e-6, max(1e-6, _float(value)))


def _float(value: object) -> float:
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _label(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and value in (0, 1):
        return value
    raise ValueError("final_label must be 0 or 1")


def _logit(probability: float) -> float:
    return math.log(probability / (1.0 - probability))
```

- [ ] **Step 4: Run feature tests to verify GREEN**

Run:

```bash
uv run pytest tests/calibration/test_features.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 6**

```bash
git add src/polymarket_engine/calibration/features.py tests/calibration/test_features.py
git commit -m "feat(calibration): add model feature matrix"
```

---

### Task 7: Add Logistic Regression Calibrator Baseline

**Files:**
- Create: `src/polymarket_engine/calibration/logistic.py`
- Create: `tests/calibration/test_logistic.py`

- [ ] **Step 1: Write logistic tests**

Create `tests/calibration/test_logistic.py`:

```python
from __future__ import annotations

from polymarket_engine.calibration.logistic import LogisticCalibrator
from polymarket_engine.calibration.logistic import fit_logistic_calibrator


def test_fit_logistic_calibrator_learns_directional_signal() -> None:
    matrix = [[-2.0], [-1.0], [1.0], [2.0]]
    labels = [0, 0, 1, 1]

    model = fit_logistic_calibrator(
        matrix,
        labels,
        feature_names=("signal",),
        learning_rate=0.2,
        iterations=300,
        l2=0.0,
    )

    low, high = model.predict_proba([[-2.0], [2.0]])
    assert low < 0.35
    assert high > 0.65
    assert model.model_version == "MC_Calibrator_LogReg_v1"


def test_logistic_calibrator_round_trips_json() -> None:
    model = LogisticCalibrator(
        model_version="MC_Calibrator_LogReg_v1",
        feature_names=("signal",),
        intercept=-0.1,
        coefficients=(0.5,),
    )

    restored = LogisticCalibrator.from_json_dict(model.to_json_dict())

    assert restored == model
    assert restored.predict_proba([[0.0]]) == model.predict_proba([[0.0]])
```

- [ ] **Step 2: Run logistic tests to verify RED**

Run:

```bash
uv run pytest tests/calibration/test_logistic.py -q
```

Expected: FAIL because `calibration.logistic` does not exist.

- [ ] **Step 3: Implement logistic calibrator**

Create `src/polymarket_engine/calibration/logistic.py`:

```python
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class LogisticCalibrator:
    model_version: str
    feature_names: tuple[str, ...]
    intercept: float
    coefficients: tuple[float, ...]

    def predict_proba(self, matrix: Sequence[Sequence[float]]) -> list[float]:
        coefficients = np.array(self.coefficients, dtype=float)
        output: list[float] = []
        for row in matrix:
            values = np.array(row, dtype=float)
            if values.shape[0] != coefficients.shape[0]:
                raise ValueError("feature length does not match model")
            output.append(_sigmoid(float(self.intercept + np.dot(values, coefficients))))
        return output

    def to_json_dict(self) -> dict[str, object]:
        return {
            "model_version": self.model_version,
            "feature_names": list(self.feature_names),
            "intercept": self.intercept,
            "coefficients": list(self.coefficients),
        }

    @classmethod
    def from_json_dict(cls, payload: dict[str, object]) -> LogisticCalibrator:
        return cls(
            model_version=str(payload["model_version"]),
            feature_names=tuple(str(item) for item in payload["feature_names"]),  # type: ignore[index]
            intercept=float(payload["intercept"]),
            coefficients=tuple(float(item) for item in payload["coefficients"]),  # type: ignore[index]
        )


def fit_logistic_calibrator(
    matrix: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    feature_names: Sequence[str],
    learning_rate: float,
    iterations: int,
    l2: float,
) -> LogisticCalibrator:
    if not matrix:
        raise ValueError("matrix must be non-empty")
    x = np.array(matrix, dtype=float)
    y = np.array(labels, dtype=float)
    if x.ndim != 2:
        raise ValueError("matrix must be two-dimensional")
    if y.shape[0] != x.shape[0]:
        raise ValueError("labels length must match matrix rows")
    if x.shape[1] != len(feature_names):
        raise ValueError("feature_names length must match matrix columns")
    weights = np.zeros(x.shape[1], dtype=float)
    intercept = 0.0
    for _ in range(iterations):
        logits = intercept + x.dot(weights)
        probabilities = 1.0 / (1.0 + np.exp(-np.clip(logits, -30.0, 30.0)))
        errors = probabilities - y
        weights -= learning_rate * ((x.T.dot(errors) / x.shape[0]) + l2 * weights)
        intercept -= learning_rate * float(np.mean(errors))
    return LogisticCalibrator(
        model_version="MC_Calibrator_LogReg_v1",
        feature_names=tuple(feature_names),
        intercept=float(intercept),
        coefficients=tuple(float(value) for value in weights),
    )


def _sigmoid(value: float) -> float:
    clipped = max(-30.0, min(30.0, value))
    return 1.0 / (1.0 + math.exp(-clipped))
```

- [ ] **Step 4: Run logistic tests to verify GREEN**

Run:

```bash
uv run pytest tests/calibration/test_logistic.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit Task 7**

```bash
git add src/polymarket_engine/calibration/logistic.py tests/calibration/test_logistic.py
git commit -m "feat(calibration): add logistic calibrator baseline"
```

---

### Task 8: Add XGBoost Calibrator And Training CLI

**Files:**
- Modify: `pyproject.toml`
- Create: `src/polymarket_engine/calibration/xgboost_model.py`
- Create: `src/polymarket_engine/calibration/train.py`
- Create: `tests/calibration/test_xgboost_model.py`
- Create: `tests/calibration/test_train.py`
- Modify: `src/polymarket_engine/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Add research dependencies**

Modify `pyproject.toml`:

```toml
[dependency-groups]
dev = [
    "mypy>=1.15.0",
    "pytest>=8.3.0",
    "ruff>=0.11.0",
]
research = [
    "scikit-learn>=1.6.0",
    "xgboost>=3.0.0",
]
```

Run:

```bash
uv sync --group dev --group research
```

Expected: dependency lock updates successfully.

- [ ] **Step 2: Write XGBoost and train tests**

Create `tests/calibration/test_xgboost_model.py`:

```python
from __future__ import annotations

import pytest

pytest.importorskip("xgboost")

from polymarket_engine.calibration.xgboost_model import fit_xgboost_calibrator


def test_fit_xgboost_calibrator_predicts_probabilities() -> None:
    matrix = [[-2.0], [-1.0], [1.0], [2.0]]
    labels = [0, 0, 1, 1]

    model = fit_xgboost_calibrator(
        matrix,
        labels,
        feature_names=("signal",),
        max_depth=2,
        eta=0.3,
        rounds=8,
    )

    probabilities = model.predict_proba([[-2.0], [2.0]])
    assert len(probabilities) == 2
    assert all(0.0 <= value <= 1.0 for value in probabilities)
    assert model.model_version == "MC_Calibrator_GBDT_v1"
```

Create `tests/calibration/test_train.py`:

```python
from __future__ import annotations

import json
from pathlib import Path

from polymarket_engine.calibration.train import TrainCalibratorConfig
from polymarket_engine.calibration.train import train_calibrator


def _row(state_id: str, probability: float, label: int) -> dict[str, object]:
    return {
        "state_id": state_id,
        "asof_ts": "2026-06-10T12:00:00+00:00",
        "p_finish_mc": probability,
        "p_no_touch_mc": 0.6,
        "mc_generator_dispersion": 0.03,
        "tte_seconds": 120,
        "z_path": probability,
        "sigma_tau": 0.015,
        "distance_to_threshold": 10.0,
        "spread": 0.02,
        "orderbook_imbalance": 0.1,
        "visible_depth": 1000.0,
        "quote_age_ms": 200.0,
        "source_age_ms": 500.0,
        "threshold_cross_count": 1,
        "near_threshold_congestion": 2,
        "recent_wick_size": 0.001,
        "asset": "BTC",
        "side": "UP",
        "volatility_regime": "normal",
        "final_label": label,
    }


def test_train_logreg_writes_model_and_predictions(tmp_path: Path) -> None:
    input_path = tmp_path / "dataset.jsonl"
    model_path = tmp_path / "model.json"
    predictions_path = tmp_path / "predictions.jsonl"
    input_path.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                _row("a", 0.2, 0),
                _row("b", 0.3, 0),
                _row("c", 0.8, 1),
                _row("d", 0.9, 1),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = train_calibrator(
        TrainCalibratorConfig(
            input_path=input_path,
            model_path=model_path,
            predictions_path=predictions_path,
            model_type="logreg",
        )
    )

    assert result.rows_trained == 4
    model_payload = json.loads(model_path.read_text(encoding="utf-8"))
    assert model_payload["model_version"] == "MC_Calibrator_LogReg_v1"
    predictions = [json.loads(line) for line in predictions_path.read_text(encoding="utf-8").splitlines()]
    assert [row["state_id"] for row in predictions] == ["a", "b", "c", "d"]
    assert all("p_finish_final" in row for row in predictions)
```

Add CLI parser test:

```python
def test_parse_train_calibrator_args() -> None:
    args = parse_args(
        [
            "train-calibrator",
            "--input",
            "data/research/calibration/asof_decision_states.jsonl",
            "--model-type",
            "logreg",
            "--model-out",
            "data/research/models/logreg.json",
            "--predictions-out",
            "data/research/calibration/logreg_predictions.jsonl",
        ]
    )

    assert args.command == "train-calibrator"
    assert args.input == Path("data/research/calibration/asof_decision_states.jsonl")
    assert args.model_type == "logreg"
    assert args.model_out == Path("data/research/models/logreg.json")
    assert args.predictions_out == Path("data/research/calibration/logreg_predictions.jsonl")
```

- [ ] **Step 3: Run model tests to verify RED**

Run:

```bash
uv run pytest tests/calibration/test_xgboost_model.py tests/calibration/test_train.py tests/test_cli.py::test_parse_train_calibrator_args -q
```

Expected: FAIL because model modules and CLI command do not exist.

- [ ] **Step 4: Implement XGBoost wrapper**

Create `src/polymarket_engine/calibration/xgboost_model.py`:

```python
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class XGBoostCalibrator:
    model_version: str
    feature_names: tuple[str, ...]
    booster: object

    def predict_proba(self, matrix: Sequence[Sequence[float]]) -> list[float]:
        xgb = _xgboost()
        dmatrix = xgb.DMatrix(matrix, feature_names=list(self.feature_names))
        return [float(value) for value in self.booster.predict(dmatrix)]

    def save_model(self, path: str) -> None:
        self.booster.save_model(path)


def fit_xgboost_calibrator(
    matrix: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    feature_names: Sequence[str],
    max_depth: int,
    eta: float,
    rounds: int,
) -> XGBoostCalibrator:
    if not matrix:
        raise ValueError("matrix must be non-empty")
    xgb = _xgboost()
    dtrain = xgb.DMatrix(matrix, label=list(labels), feature_names=list(feature_names))
    booster = xgb.train(
        {
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "max_depth": max_depth,
            "eta": eta,
            "seed": 7,
        },
        dtrain,
        num_boost_round=rounds,
    )
    return XGBoostCalibrator(
        model_version="MC_Calibrator_GBDT_v1",
        feature_names=tuple(feature_names),
        booster=booster,
    )


def _xgboost() -> object:
    try:
        import xgboost as xgb
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "xgboost is required for MC_Calibrator_GBDT_v1; run "
            "`uv sync --group research`"
        ) from exc
    return xgb
```

- [ ] **Step 5: Implement training orchestration**

Create `src/polymarket_engine/calibration/train.py`:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from polymarket_engine.calibration.features import DEFAULT_FEATURE_NAMES
from polymarket_engine.calibration.features import feature_matrix
from polymarket_engine.calibration.logistic import fit_logistic_calibrator
from polymarket_engine.calibration.reports import load_calibration_jsonl
from polymarket_engine.calibration.xgboost_model import fit_xgboost_calibrator


@dataclass(frozen=True)
class TrainCalibratorConfig:
    input_path: Path
    model_path: Path
    predictions_path: Path
    model_type: str


@dataclass(frozen=True)
class TrainCalibratorResult:
    model_type: str
    rows_trained: int
    model_path: str
    predictions_path: str

    def to_json_dict(self) -> dict[str, object]:
        return {
            "model_type": self.model_type,
            "rows_trained": self.rows_trained,
            "model_path": self.model_path,
            "predictions_path": self.predictions_path,
        }


def train_calibrator(config: TrainCalibratorConfig) -> TrainCalibratorResult:
    rows = list(load_calibration_jsonl(config.input_path))
    matrix, labels = feature_matrix(rows, feature_names=DEFAULT_FEATURE_NAMES)
    if config.model_type == "logreg":
        model = fit_logistic_calibrator(
            matrix,
            labels,
            feature_names=DEFAULT_FEATURE_NAMES,
            learning_rate=0.05,
            iterations=500,
            l2=0.001,
        )
        probabilities = model.predict_proba(matrix)
        model_payload = model.to_json_dict()
    elif config.model_type == "xgboost":
        model = fit_xgboost_calibrator(
            matrix,
            labels,
            feature_names=DEFAULT_FEATURE_NAMES,
            max_depth=3,
            eta=0.1,
            rounds=50,
        )
        probabilities = model.predict_proba(matrix)
        booster_path = config.model_path.with_suffix(".xgboost.json")
        booster_path.parent.mkdir(parents=True, exist_ok=True)
        model.save_model(str(booster_path))
        model_payload = {
            "model_version": model.model_version,
            "feature_names": list(model.feature_names),
            "booster_path": str(booster_path),
        }
    else:
        raise ValueError("model_type must be logreg or xgboost")
    config.model_path.parent.mkdir(parents=True, exist_ok=True)
    config.model_path.write_text(
        json.dumps(model_payload, allow_nan=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    config.predictions_path.parent.mkdir(parents=True, exist_ok=True)
    with config.predictions_path.open("w", encoding="utf-8") as handle:
        for row, probability in zip(rows, probabilities, strict=True):
            output = dict(row)
            output["p_finish_final"] = probability
            output["calibration_model_type"] = config.model_type
            handle.write(json.dumps(output, allow_nan=False, sort_keys=True) + "\n")
    return TrainCalibratorResult(
        model_type=config.model_type,
        rows_trained=len(rows),
        model_path=str(config.model_path),
        predictions_path=str(config.predictions_path),
    )
```

- [ ] **Step 6: Add train CLI**

In `src/polymarket_engine/cli.py`, add parser setup:

```python
    train_calibrator = subparsers.add_parser("train-calibrator")
    train_calibrator.add_argument("--input", type=Path, required=True)
    train_calibrator.add_argument("--model-type", choices=("logreg", "xgboost"), required=True)
    train_calibrator.add_argument("--model-out", type=Path, required=True)
    train_calibrator.add_argument("--predictions-out", type=Path, required=True)
```

Add dispatch:

```python
    if args.command == "train-calibrator":
        return _run_train_calibrator(args)
```

Add helper:

```python
def _run_train_calibrator(args: argparse.Namespace) -> int:
    from polymarket_engine.calibration.train import TrainCalibratorConfig
    from polymarket_engine.calibration.train import train_calibrator

    result = train_calibrator(
        TrainCalibratorConfig(
            input_path=args.input,
            model_path=args.model_out,
            predictions_path=args.predictions_out,
            model_type=args.model_type,
        )
    )
    print(json.dumps(result.to_json_dict(), sort_keys=True, separators=(",", ":")))
    return 0
```

- [ ] **Step 7: Run model tests to verify GREEN**

Run:

```bash
uv run pytest tests/calibration/test_features.py tests/calibration/test_logistic.py tests/calibration/test_xgboost_model.py tests/calibration/test_train.py tests/test_cli.py::test_parse_train_calibrator_args -q
```

Expected: PASS.

- [ ] **Step 8: Commit Task 8**

```bash
git add pyproject.toml uv.lock src/polymarket_engine/calibration src/polymarket_engine/cli.py tests/calibration tests/test_cli.py
git commit -m "feat(calibration): add logistic and xgboost calibrators"
```

---

### Task 9: Reports, Docs, And End-To-End Verification

**Files:**
- Modify: `src/polymarket_engine/cli.py`
- Modify: `src/polymarket_engine/calibration/reports.py`
- Modify: `tests/calibration/test_reports.py`
- Modify: `docs/observations_2.md`
- Modify: `README.md`

- [ ] **Step 1: Write calibration-report probability-field CLI test**

Add to `tests/test_cli.py`:

```python
def test_parse_calibration_report_probability_field_arg() -> None:
    args = parse_args(
        [
            "calibration-report",
            "--input",
            "data/research/calibration/logreg_predictions.jsonl",
            "--out",
            "data/research/calibration/logreg_report.json",
            "--probability-field",
            "p_finish_final",
        ]
    )

    assert args.command == "calibration-report"
    assert args.input == Path("data/research/calibration/logreg_predictions.jsonl")
    assert args.out == Path("data/research/calibration/logreg_report.json")
    assert args.probability_field == "p_finish_final"
```

Add to `tests/calibration/test_reports.py`:

```python
def test_build_calibration_report_accepts_calibrated_probability_field() -> None:
    rows = [
        _row(state_id="state-1", p_finish_mc=0.55, p_finish_final=0.80, final_label=1),
        _row(state_id="state-2", p_finish_mc=0.55, p_finish_final=0.20, final_label=0),
    ]

    report = build_calibration_report(rows, probability_field="p_finish_final")

    assert report.evaluated_row_count == 2
    assert report.brier_score is not None
    assert round(report.brier_score, 4) == 0.04
```

- [ ] **Step 2: Run report tests to verify RED**

Run:

```bash
uv run pytest tests/calibration/test_reports.py::test_build_calibration_report_accepts_calibrated_probability_field tests/test_cli.py::test_parse_calibration_report_probability_field_arg -q
```

Expected: CLI parser test FAILS because `--probability-field` is not exposed for `calibration-report`. The report unit test may already pass because `build_calibration_report(...)` accepts `probability_field`.

- [ ] **Step 3: Add `--probability-field` to calibration-report CLI**

In `src/polymarket_engine/cli.py`, add this parser argument to the existing `calibration-report` command:

```python
    calibration_report.add_argument("--probability-field", default="p_finish_mc")
```

Change `_run_calibration_report(...)`:

```python
    report = build_calibration_report(rows, probability_field=args.probability_field)
```

- [ ] **Step 4: Document the offline workflow**

Append this section to `README.md`:

```markdown
## Offline Backtest And Calibration

The backtest and ML calibration workflow is offline-only. It reads replay-safe
as-of rows from DuckDB, joins final outcomes only as labels, and writes research
artifacts under `data/research/`.

Example:

```bash
uv run polymarket-engine export-calibration-dataset \
  --duckdb-path data/db/polymarket.duckdb \
  --out data/research/calibration/asof_decision_states.jsonl \
  --limit 10000

uv run polymarket-engine calibration-report \
  --input data/research/calibration/asof_decision_states.jsonl \
  --out data/research/calibration/raw_mc_report.json \
  --probability-field p_finish_mc

uv run polymarket-engine run-backtest \
  --input data/research/calibration/asof_decision_states.jsonl \
  --out data/research/backtests/raw_mc.json \
  --probability-field p_finish_mc \
  --stake-usd 100 \
  --min-edge 0.02

uv run polymarket-engine train-calibrator \
  --input data/research/calibration/asof_decision_states.jsonl \
  --model-type logreg \
  --model-out data/research/models/logreg.json \
  --predictions-out data/research/calibration/logreg_predictions.jsonl

uv run polymarket-engine calibration-report \
  --input data/research/calibration/logreg_predictions.jsonl \
  --out data/research/calibration/logreg_report.json \
  --probability-field p_finish_final
```

Run XGBoost only after syncing research dependencies:

```bash
uv sync --group dev --group research
uv run polymarket-engine train-calibrator \
  --input data/research/calibration/asof_decision_states.jsonl \
  --model-type xgboost \
  --model-out data/research/models/xgboost.json \
  --predictions-out data/research/calibration/xgboost_predictions.jsonl
```

This workflow does not place trades. It is for replay, calibration, and
offline execution simulation.
```

In `docs/observations_2.md`, add this line under "Practical Implementation Order":

```markdown
Implementation status: the active build plan is `docs/superpowers/plans/2026-06-15-backtest-replay-xgboost-calibration.md`. The first shipped scope is replay-safe dataset export, offline backtest, logistic calibration, and XGBoost calibration. BART remains an offline benchmark after the simpler models have walk-forward evidence.
```

- [ ] **Step 5: Run focused test suite**

Run:

```bash
uv run pytest tests/calibration tests/backtest tests/test_cli.py -q
```

Expected: PASS.

- [ ] **Step 6: Run full verification**

Run:

```bash
uv run ruff check .
uv run mypy src tests
uv run pytest -q
```

Expected: all commands PASS.

- [ ] **Step 7: Run a local end-to-end smoke if a DuckDB file exists**

Run:

```bash
uv run polymarket-engine export-calibration-dataset \
  --duckdb-path data/db/polymarket.duckdb \
  --out data/research/calibration/asof_decision_states.jsonl \
  --limit 1000

uv run polymarket-engine calibration-report \
  --input data/research/calibration/asof_decision_states.jsonl \
  --out data/research/calibration/raw_mc_report.json \
  --probability-field p_finish_mc

uv run polymarket-engine run-backtest \
  --input data/research/calibration/asof_decision_states.jsonl \
  --out data/research/backtests/raw_mc.json \
  --probability-field p_finish_mc
```

Expected: commands exit 0. If the dataset has zero labeled rows, report that outcome and do not treat it as a code failure.

- [ ] **Step 8: Commit Task 9**

```bash
git add src/polymarket_engine/cli.py src/polymarket_engine/calibration/reports.py tests/calibration/test_reports.py tests/test_cli.py README.md docs/observations_2.md
git commit -m "docs: document offline calibration workflow"
```

---

## Execution Order

1. Task 1: dataset contract.
2. Task 2: DuckDB export.
3. Task 3: walk-forward splits.
4. Task 4: fill ledger.
5. Task 5: backtest runner and CLI.
6. Task 6: feature matrix.
7. Task 7: logistic baseline.
8. Task 8: XGBoost calibrator and training CLI.
9. Task 9: reporting/docs/verification.

Stop after Task 5 if the immediate need is the backtest engine only. Continue through Task 8 to start the XGBoost lane from `docs/observations_2.md`.

## Self-Review

Spec coverage:
- Backtest/replay engine: covered by Tasks 2, 4, and 5.
- Replay-safe dataset with labels: covered by Tasks 1 and 2.
- Calibration buckets and reports: existing report is retained, and Task 9 exposes calibrated probability fields.
- Logistic baseline before XGBoost: covered by Task 7.
- XGBoost start: covered by Task 8 with research dependencies and prediction output.
- Walk-forward validation foundation: covered by Task 3.
- BART: documented as outside this first shipping scope because the observation doc says to run it after logistic and XGBoost evidence.
- Live trading exclusion: stated in scope and no task adds order placement.

Placeholder scan:
- No task depends on undefined file paths.
- No step uses deferred placeholder implementation wording.
- Every command includes expected result.

Type consistency:
- Dataset uses `p_finish_mc` and calibrator predictions write `p_finish_final`.
- Backtest accepts any probability field, so raw MC and calibrated predictions use the same runner.
- CLI names are consistent across parser tests, implementation steps, and README examples.
