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
            s.settlement_source_key,
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
    cursor = conn.execute(query, [*params, config.limit])
    columns = [column[0] for column in cursor.description]
    return tuple(dict(zip(columns, row, strict=True)) for row in cursor.fetchall())


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
        z_path=_float(
            payload.get("z_path"),
            _z_path(current_price, threshold, payload.get("sigma_tau"), side),
        ),
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
        settlement_price_at_expiry=_optional_float(payload.get("end_price")),
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
            where source_key = ? and symbol = ? and event_ts >= ? and event_ts <= ? and observed_ts <= ?
        )
        select count(*) from signed where prev_side is not null and prev_side != side
        """,
        [
            threshold,
            threshold,
            payload["settlement_source_key"],
            f"{payload['asset']}/USD",
            start_ts,
            asof_ts,
            asof_ts,
        ],
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
        where source_key = ? and symbol = ? and event_ts >= ? and event_ts <= ? and observed_ts <= ? and abs(price - ?) <= ?
        """,
        [
            payload["settlement_source_key"],
            f"{payload['asset']}/USD",
            asof_ts - timedelta(seconds=60),
            asof_ts,
            asof_ts,
            threshold,
            tolerance,
        ],
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
        where source_key = ? and symbol = ? and event_ts >= ? and event_ts <= ? and observed_ts <= ?
        """,
        [
            payload["settlement_source_key"],
            f"{payload['asset']}/USD",
            asof_ts - timedelta(seconds=60),
            asof_ts,
            asof_ts,
        ],
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
    fallback = default if default is not None else 0.0
    if value is None:
        return fallback if math.isfinite(fallback) else 0.0
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return fallback if math.isfinite(fallback) else 0.0
    numeric_value: str | int | float = value
    try:
        result = float(numeric_value)
    except (TypeError, ValueError):
        result = fallback
    return result if math.isfinite(result) else (fallback if math.isfinite(fallback) else 0.0)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _parse_ts(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
