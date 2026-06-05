from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, cast

import duckdb

from polymarket_engine.probability.schema import ProbabilityInput
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


DEFAULT_EVENT_FLAG = "none"
DEFAULT_SOURCE_RISK_FLAG = "normal"
DEFAULT_GENERATOR_VERSION = "offline-lognormal-chainlink-sigma-v1"
DEFAULT_MODEL_VERSION = "cached-grid-v1"


@dataclass(frozen=True)
class ProbabilityGridKey:
    cache_key: str
    asset: str
    side: str
    market_slug: str
    start_ts: datetime
    expiry_ts: datetime
    horizon_seconds: int
    seconds_left_bucket: str
    z_path_bucket: str
    sigma_bucket: str
    volatility_regime: str
    event_flag: str
    source_risk_flag: str
    generator_version: str


@dataclass(frozen=True)
class ProbabilityGridEntry:
    cache_key: str
    asset: str
    side: str
    market_slug: str
    start_ts: datetime
    expiry_ts: datetime
    asof_ts: datetime
    horizon_seconds: int
    seconds_left_bucket: str
    z_path_bucket: str
    sigma_bucket: str
    volatility_regime: str
    event_flag: str
    source_risk_flag: str
    generator_version: str
    model_version: str
    p_finish: float
    p_no_touch: float
    u_gen: float
    path_count: int
    seed: int | None
    training_cutoff_ts: datetime
    max_event_ts: datetime
    max_observed_ts: datetime
    generated_at: datetime
    valid_from: datetime
    valid_until: datetime
    diagnostics: Mapping[str, Any]

    def __post_init__(self) -> None:
        _require_non_empty(self.cache_key, "cache_key")
        _require_supported(self.asset, "asset", {"BTC", "ETH"})
        _require_supported(self.side, "side", {"UP", "DOWN"})
        _require_non_empty(self.market_slug, "market_slug")
        _require_positive_int(self.horizon_seconds, "horizon_seconds")
        _require_non_empty(self.seconds_left_bucket, "seconds_left_bucket")
        _require_non_empty(self.z_path_bucket, "z_path_bucket")
        _require_non_empty(self.sigma_bucket, "sigma_bucket")
        _require_non_empty(self.volatility_regime, "volatility_regime")
        _require_non_empty(self.event_flag, "event_flag")
        _require_non_empty(self.source_risk_flag, "source_risk_flag")
        _require_non_empty(self.generator_version, "generator_version")
        _require_non_empty(self.model_version, "model_version")
        _require_probability(self.p_finish, "p_finish")
        _require_probability(self.p_no_touch, "p_no_touch")
        _require_nonnegative(self.u_gen, "u_gen")
        _require_positive_int(self.path_count, "path_count")
        if self.seed is not None and (isinstance(self.seed, bool) or not isinstance(self.seed, int)):
            raise ValueError("seed must be int or None")
        for field_name in (
            "start_ts",
            "expiry_ts",
            "asof_ts",
            "training_cutoff_ts",
            "max_event_ts",
            "max_observed_ts",
            "generated_at",
            "valid_from",
            "valid_until",
        ):
            _require_timezone_aware(getattr(self, field_name), field_name)
        if self.valid_until < self.valid_from:
            raise ValueError("valid_until must not be before valid_from")
        if not isinstance(self.diagnostics, Mapping):
            raise ValueError("diagnostics must be a JSON object")
        _strict_json(self.diagnostics)


@dataclass(frozen=True)
class ProbabilityGridHit:
    entry: ProbabilityGridEntry
    cache_status: str = "HIT"


def probability_grid_key(
    probability_input: ProbabilityInput,
    *,
    market_slug: str,
    start_ts: datetime,
    expiry_ts: datetime,
    volatility_regime: str | None,
    event_flag: str = DEFAULT_EVENT_FLAG,
    source_risk_flag: str = DEFAULT_SOURCE_RISK_FLAG,
    generator_version: str = DEFAULT_GENERATOR_VERSION,
) -> ProbabilityGridKey:
    _require_non_empty(generator_version, "generator_version")
    _require_non_empty(market_slug, "market_slug")
    safe_start_ts = _to_utc(start_ts, "start_ts")
    safe_expiry_ts = _to_utc(expiry_ts, "expiry_ts")
    volatility_regime_value = _normalized_dimension(volatility_regime, "unknown")
    event_flag_value = _normalized_dimension(event_flag, DEFAULT_EVENT_FLAG)
    source_risk_flag_value = _normalized_dimension(source_risk_flag, DEFAULT_SOURCE_RISK_FLAG)
    horizon_seconds = horizon_bucket_seconds(probability_input.seconds_left)
    seconds_left_bucket = seconds_left_bucket_label(probability_input.seconds_left)
    z_path_bucket = z_path_bucket_label(probability_input.z_path)
    sigma_bucket = sigma_bucket_label(probability_input.sigma_tau)
    cache_key = (
        f"{probability_input.asset}|{probability_input.side}|"
        f"market{market_slug}|start{int(safe_start_ts.timestamp())}|"
        f"expiry{int(safe_expiry_ts.timestamp())}|h{horizon_seconds}|"
        f"t{seconds_left_bucket}|z{z_path_bucket}|sigma{sigma_bucket}|"
        f"vol{volatility_regime_value}|event{event_flag_value}|risk{source_risk_flag_value}|"
        f"gen{generator_version}"
    )
    return ProbabilityGridKey(
        cache_key=cache_key,
        asset=probability_input.asset,
        side=probability_input.side,
        market_slug=market_slug,
        start_ts=safe_start_ts,
        expiry_ts=safe_expiry_ts,
        horizon_seconds=horizon_seconds,
        seconds_left_bucket=seconds_left_bucket,
        z_path_bucket=z_path_bucket,
        sigma_bucket=sigma_bucket,
        volatility_regime=volatility_regime_value,
        event_flag=event_flag_value,
        source_risk_flag=source_risk_flag_value,
        generator_version=generator_version,
    )


def grid_entry_from_probability_input(
    probability_input: ProbabilityInput,
    *,
    market_slug: str,
    start_ts: datetime,
    expiry_ts: datetime,
    p_finish: float,
    p_no_touch: float,
    u_gen: float,
    path_count: int,
    seed: int | None,
    volatility_regime: str | None,
    event_flag: str = DEFAULT_EVENT_FLAG,
    source_risk_flag: str = DEFAULT_SOURCE_RISK_FLAG,
    generator_version: str = DEFAULT_GENERATOR_VERSION,
    model_version: str = DEFAULT_MODEL_VERSION,
    training_cutoff_ts: datetime,
    max_event_ts: datetime,
    max_observed_ts: datetime,
    generated_at: datetime,
    valid_from: datetime,
    valid_until: datetime,
    diagnostics: Mapping[str, Any],
) -> ProbabilityGridEntry:
    key = probability_grid_key(
        probability_input,
        market_slug=market_slug,
        start_ts=start_ts,
        expiry_ts=expiry_ts,
        volatility_regime=volatility_regime,
        event_flag=event_flag,
        source_risk_flag=source_risk_flag,
        generator_version=generator_version,
    )
    return ProbabilityGridEntry(
        cache_key=key.cache_key,
        asset=key.asset,
        side=key.side,
        market_slug=market_slug,
        start_ts=_to_utc(start_ts, "start_ts"),
        expiry_ts=_to_utc(expiry_ts, "expiry_ts"),
        asof_ts=_to_utc(probability_input.asof_ts, "asof_ts"),
        horizon_seconds=key.horizon_seconds,
        seconds_left_bucket=key.seconds_left_bucket,
        z_path_bucket=key.z_path_bucket,
        sigma_bucket=key.sigma_bucket,
        volatility_regime=key.volatility_regime,
        event_flag=key.event_flag,
        source_risk_flag=key.source_risk_flag,
        generator_version=key.generator_version,
        model_version=model_version,
        p_finish=p_finish,
        p_no_touch=p_no_touch,
        u_gen=u_gen,
        path_count=path_count,
        seed=seed,
        training_cutoff_ts=_to_utc(training_cutoff_ts, "training_cutoff_ts"),
        max_event_ts=_to_utc(max_event_ts, "max_event_ts"),
        max_observed_ts=_to_utc(max_observed_ts, "max_observed_ts"),
        generated_at=_to_utc(generated_at, "generated_at"),
        valid_from=_to_utc(valid_from, "valid_from"),
        valid_until=_to_utc(valid_until, "valid_until"),
        diagnostics=dict(diagnostics),
    )


def upsert_probability_grid_entry(
    store: DuckDbIngestStore,
    entry: ProbabilityGridEntry,
) -> None:
    diagnostics_json = json.dumps(entry.diagnostics, sort_keys=True, separators=(",", ":"), allow_nan=False)
    with store._connection() as conn:
        conn.execute(
            """
            insert or replace into features.probability_grid_cache
            (
                cache_key,
                asset,
                side,
                market_slug,
                start_ts,
                expiry_ts,
                asof_ts,
                horizon_seconds,
                seconds_left_bucket,
                z_path_bucket,
                sigma_bucket,
                volatility_regime,
                event_flag,
                source_risk_flag,
                generator_version,
                model_version,
                p_finish,
                p_no_touch,
                u_gen,
                path_count,
                seed,
                training_cutoff_ts,
                max_event_ts,
                max_observed_ts,
                generated_at,
                valid_from,
                valid_until,
                diagnostics_json
            )
            values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                entry.cache_key,
                entry.asset,
                entry.side,
                entry.market_slug,
                entry.start_ts,
                entry.expiry_ts,
                entry.asof_ts,
                entry.horizon_seconds,
                entry.seconds_left_bucket,
                entry.z_path_bucket,
                entry.sigma_bucket,
                entry.volatility_regime,
                entry.event_flag,
                entry.source_risk_flag,
                entry.generator_version,
                entry.model_version,
                entry.p_finish,
                entry.p_no_touch,
                entry.u_gen,
                entry.path_count,
                entry.seed,
                entry.training_cutoff_ts,
                entry.max_event_ts,
                entry.max_observed_ts,
                entry.generated_at,
                entry.valid_from,
                entry.valid_until,
                diagnostics_json,
            ],
        )


def lookup_probability_grid_entry(
    conn: duckdb.DuckDBPyConnection,
    probability_input: ProbabilityInput,
    *,
    market_slug: str,
    start_ts: datetime,
    expiry_ts: datetime,
    volatility_regime: str | None,
    asof_ts: datetime,
    runtime_ts: datetime,
    event_flag: str = DEFAULT_EVENT_FLAG,
    source_risk_flag: str = DEFAULT_SOURCE_RISK_FLAG,
    generator_version: str = DEFAULT_GENERATOR_VERSION,
) -> ProbabilityGridHit | None:
    safe_asof_ts = _to_utc(asof_ts, "asof_ts")
    safe_runtime_ts = _to_utc(runtime_ts, "runtime_ts")
    safe_start_ts = _to_utc(start_ts, "start_ts")
    safe_expiry_ts = _to_utc(expiry_ts, "expiry_ts")
    key = probability_grid_key(
        probability_input,
        market_slug=market_slug,
        start_ts=safe_start_ts,
        expiry_ts=safe_expiry_ts,
        volatility_regime=volatility_regime,
        event_flag=event_flag,
        source_risk_flag=source_risk_flag,
        generator_version=generator_version,
    )
    row = conn.execute(
        """
        select
            cache_key,
            asset,
            side,
            market_slug,
            cast(start_ts as varchar),
            cast(expiry_ts as varchar),
            cast(asof_ts as varchar),
            horizon_seconds,
            seconds_left_bucket,
            z_path_bucket,
            sigma_bucket,
            volatility_regime,
            event_flag,
            source_risk_flag,
            generator_version,
            model_version,
            p_finish,
            p_no_touch,
            u_gen,
            path_count,
            seed,
            cast(training_cutoff_ts as varchar),
            cast(max_event_ts as varchar),
            cast(max_observed_ts as varchar),
            cast(generated_at as varchar),
            cast(valid_from as varchar),
            cast(valid_until as varchar),
            diagnostics_json
        from features.probability_grid_cache
        where cache_key = ?
          and market_slug = ?
          and start_ts = ?
          and expiry_ts = ?
          and valid_from <= ?
          and valid_until >= ?
          and generated_at <= ?
          and asof_ts <= ?
          and training_cutoff_ts <= ?
          and max_event_ts <= ?
          and max_observed_ts <= ?
        """,
        [
            key.cache_key,
            market_slug,
            safe_start_ts,
            safe_expiry_ts,
            safe_runtime_ts,
            safe_runtime_ts,
            safe_runtime_ts,
            safe_asof_ts,
            safe_asof_ts,
            safe_asof_ts,
            safe_asof_ts,
        ],
    ).fetchone()
    if row is None:
        return None
    return ProbabilityGridHit(_entry_from_row(row))


def grid_runtime_row(
    *,
    probability_input: ProbabilityInput,
    contract: str,
    contract_id: str,
    market_slug: str | None,
    start_ts: datetime,
    expiry_ts: datetime,
    hit: ProbabilityGridHit,
    now: datetime,
) -> dict[str, Any]:
    entry = hit.entry
    safe_now = _to_utc(now, "now")
    start_utc = _to_utc(start_ts, "start_ts")
    expiry_utc = _to_utc(expiry_ts, "expiry_ts")
    age_ms = max(0, int((safe_now - probability_input.asof_ts).total_seconds() * 1000))
    grid_cache = {
        "cache_key": entry.cache_key,
        "cache_status": hit.cache_status,
        "market_slug": entry.market_slug,
        "start_ts": entry.start_ts.isoformat(),
        "expiry_ts": entry.expiry_ts.isoformat(),
        "asof_ts": entry.asof_ts.isoformat(),
        "generated_at": entry.generated_at.isoformat(),
        "valid_from": entry.valid_from.isoformat(),
        "valid_until": entry.valid_until.isoformat(),
        "time_bucket": entry.seconds_left_bucket,
        "z_path_bucket": entry.z_path_bucket,
        "sigma_bucket": entry.sigma_bucket,
        "volatility_regime": entry.volatility_regime,
        "path_count": entry.path_count,
    }
    return {
        "contract": contract,
        "contract_id": contract_id,
        "market_slug": market_slug,
        "asset": probability_input.asset,
        "side": probability_input.side,
        "start_ts": start_utc.isoformat(),
        "expiry_ts": expiry_utc.isoformat(),
        "asof_ts": probability_input.asof_ts.isoformat(),
        "p_finish": entry.p_finish,
        "p_no_touch": entry.p_no_touch,
        "z_path": probability_input.z_path,
        "sigma_tau": probability_input.sigma_tau,
        "u_gen": entry.u_gen,
        "age_ms": age_ms,
        "flags": ["OK"],
        "model_version": entry.model_version,
        "seed": entry.seed,
        "cache_key": entry.cache_key,
        "cache_status": hit.cache_status,
        "cache_market_slug": entry.market_slug,
        "cache_start_ts": entry.start_ts.isoformat(),
        "cache_expiry_ts": entry.expiry_ts.isoformat(),
        "cache_asof_ts": entry.asof_ts.isoformat(),
        "generated_at": entry.generated_at.isoformat(),
        "valid_from": entry.valid_from.isoformat(),
        "valid_until": entry.valid_until.isoformat(),
        "time_bucket": entry.seconds_left_bucket,
        "z_path_bucket": entry.z_path_bucket,
        "sigma_bucket": entry.sigma_bucket,
        "volatility_regime": entry.volatility_regime,
        "generator_version": entry.generator_version,
        "path_count": entry.path_count,
        "grid_cache": grid_cache,
        "generator_metadata": {
            "cache_key": entry.cache_key,
            "cache_status": hit.cache_status,
            "cache_market_slug": entry.market_slug,
            "cache_start_ts": entry.start_ts.isoformat(),
            "cache_expiry_ts": entry.expiry_ts.isoformat(),
            "cache_asof_ts": entry.asof_ts.isoformat(),
            "generated_at": entry.generated_at.isoformat(),
            "valid_from": entry.valid_from.isoformat(),
            "valid_until": entry.valid_until.isoformat(),
            "time_bucket": entry.seconds_left_bucket,
            "z_path_bucket": entry.z_path_bucket,
            "sigma_bucket": entry.sigma_bucket,
            "volatility_regime": entry.volatility_regime,
            "path_count": entry.path_count,
        },
    }


def horizon_bucket_seconds(seconds_left: float) -> int:
    _require_nonnegative(seconds_left, "seconds_left")
    for horizon in (300, 600, 900, 1800):
        if seconds_left <= horizon:
            return horizon
    return int(math.ceil(seconds_left / 300.0) * 300)


def seconds_left_bucket_label(seconds_left: float) -> str:
    _require_nonnegative(seconds_left, "seconds_left")
    bucket_start = int(math.floor(seconds_left / 30.0) * 30)
    bucket_end = bucket_start + 30
    return f"{bucket_start}-{bucket_end}"


def z_path_bucket_label(z_path: float) -> str:
    _require_finite(z_path, "z_path")
    bucket_start = math.floor(z_path / 0.25) * 0.25
    bucket_end = bucket_start + 0.25
    return f"{bucket_start:.2f}-{bucket_end:.2f}"


def sigma_bucket_label(sigma_tau: float) -> str:
    _require_positive(sigma_tau, "sigma_tau")
    bucket_start = math.floor(sigma_tau / 0.005) * 0.005
    bucket_end = bucket_start + 0.005
    return f"{bucket_start:.3f}-{bucket_end:.3f}"


def _entry_from_row(row: tuple[Any, ...]) -> ProbabilityGridEntry:
    return ProbabilityGridEntry(
        cache_key=str(row[0]),
        asset=str(row[1]),
        side=str(row[2]),
        market_slug=str(row[3]),
        start_ts=_parse_datetime(row[4]),
        expiry_ts=_parse_datetime(row[5]),
        asof_ts=_parse_datetime(row[6]),
        horizon_seconds=int(row[7]),
        seconds_left_bucket=str(row[8]),
        z_path_bucket=str(row[9]),
        sigma_bucket=str(row[10]),
        volatility_regime=str(row[11]),
        event_flag=str(row[12]),
        source_risk_flag=str(row[13]),
        generator_version=str(row[14]),
        model_version=str(row[15]),
        p_finish=_float(row[16], "p_finish"),
        p_no_touch=_float(row[17], "p_no_touch"),
        u_gen=_float(row[18], "u_gen"),
        path_count=int(row[19]),
        seed=None if row[20] is None else int(row[20]),
        training_cutoff_ts=_parse_datetime(row[21]),
        max_event_ts=_parse_datetime(row[22]),
        max_observed_ts=_parse_datetime(row[23]),
        generated_at=_parse_datetime(row[24]),
        valid_from=_parse_datetime(row[25]),
        valid_until=_parse_datetime(row[26]),
        diagnostics=cast(Mapping[str, Any], json.loads(str(row[27]))),
    )


def _normalized_dimension(value: str | None, fallback: str) -> str:
    normalized = (value or fallback).strip().lower().replace(" ", "_")
    return normalized or fallback


def _strict_json(value: Mapping[str, Any]) -> None:
    json.dumps(value, sort_keys=True, allow_nan=False)


def _parse_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _to_utc(parsed, "timestamp")


def _to_utc(value: datetime, field_name: str) -> datetime:
    _require_timezone_aware(value, field_name)
    return value.astimezone(timezone.utc)


def _require_timezone_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _require_supported(value: str, field_name: str, supported: set[str]) -> None:
    if not isinstance(value, str) or value not in supported:
        raise ValueError(f"{field_name} must be one of {', '.join(sorted(supported))}")


def _require_non_empty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be non-empty")


def _require_probability(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or not 0 <= value <= 1:
        raise ValueError(f"{field_name} must be finite and between 0 and 1")


def _require_nonnegative(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or value < 0:
        raise ValueError(f"{field_name} must be nonnegative and finite")


def _require_positive(value: float, field_name: str) -> None:
    if not _is_finite_number(value) or value <= 0:
        raise ValueError(f"{field_name} must be positive and finite")


def _require_finite(value: float, field_name: str) -> None:
    if not _is_finite_number(value):
        raise ValueError(f"{field_name} must be finite")


def _require_positive_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} must be a positive integer")


def _float(value: object, field_name: str) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field_name} is required")
    number = float(cast(Any, value))
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


def _is_finite_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
