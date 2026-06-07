from __future__ import annotations

import json
import math
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, cast

from polymarket_engine.probability.schema import ProbabilityInput
from polymarket_engine.storage.atomic import durable_replace

GENERATOR_FRAGMENTS_SCHEMA_VERSION = "polymarket-probability-fragments-v1"
MAX_FUTURE_GENERATED_AT_SECONDS = 5.0
DEFAULT_RETAINED_FRAGMENT_AGE_SECONDS = 15 * 60


@dataclass(frozen=True)
class GeneratorFragment:
    fragment_id: str
    asset: str
    asof_ts: datetime
    prices: tuple[float, ...]
    horizon_seconds: int
    source_key: str | None = None
    z_path_bucket: str | None = None
    quality_bucket: str | None = None
    metadata: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.fragment_id == "":
            raise ValueError("fragment_id must not be empty")
        if self.asset not in {"BTC", "ETH"}:
            raise ValueError("asset must be BTC or ETH")
        _require_aware_datetime(self.asof_ts, "asof_ts")
        if self.horizon_seconds <= 0:
            raise ValueError("horizon_seconds must be positive")
        if self.horizon_seconds > 86400:
            raise ValueError("horizon_seconds must be at most one day")
        if len(self.prices) < 2:
            raise ValueError("prices must contain at least two points")
        for value in self.prices:
            _require_positive_price(value, "prices")
        if self.metadata is not None and not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be a mapping if provided")

    @property
    def start_price(self) -> float:
        return self.prices[0]

    @property
    def end_price(self) -> float:
        return self.prices[-1]

    @property
    def step_count(self) -> int:
        return len(self.prices) - 1

    @property
    def scaled_factor(self) -> float:
        return self.end_price / self.start_price

    @property
    def log_returns(self) -> tuple[float, ...]:
        return _log_returns(self.prices)

    @property
    def max_drawdown(self) -> float:
        if not self.prices:
            return 0.0
        peak = self.prices[0]
        worst = 0.0
        for point in self.prices[1:]:
            peak = max(peak, point)
            drawdown = 1.0 - (point / peak)
            if drawdown > worst:
                worst = drawdown
        return max(0.0, min(1.0, worst))


@dataclass(frozen=True)
class ProbabilityFragmentsPayload:
    schema_version: str
    generated_at: datetime
    fragments: tuple[GeneratorFragment, ...]


@dataclass(frozen=True)
class FragmentSelection:
    fragments: tuple[GeneratorFragment, ...]
    sparse: bool
    reason: str


def write_probability_fragments(
    out_path: Path,
    fragments: Sequence[GeneratorFragment],
    generated_at: datetime,
    *,
    retain_existing: bool = False,
    max_retained_fragments: int | None = None,
    max_retained_age_seconds: float = DEFAULT_RETAINED_FRAGMENT_AGE_SECONDS,
) -> None:
    generated_at_utc = _require_aware_datetime_utc(generated_at, "generated_at")
    retained_fragments = _retained_fragments(
        out_path=out_path,
        fragments=fragments,
        generated_at=generated_at_utc,
        retain_existing=retain_existing,
        max_retained_fragments=max_retained_fragments,
        max_retained_age_seconds=max_retained_age_seconds,
    )
    rows = [_fragment_to_json_dict(fragment) for fragment in retained_fragments]
    payload = {
        "schema_version": GENERATOR_FRAGMENTS_SCHEMA_VERSION,
        "generated_at": generated_at_utc.isoformat(),
        "fragments": rows,
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=out_path.parent,
            prefix=f".{out_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(text)
        durable_replace(tmp_path, out_path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def _retained_fragments(
    *,
    out_path: Path,
    fragments: Sequence[GeneratorFragment],
    generated_at: datetime,
    retain_existing: bool,
    max_retained_fragments: int | None,
    max_retained_age_seconds: float,
) -> tuple[GeneratorFragment, ...]:
    if max_retained_fragments is not None:
        if isinstance(max_retained_fragments, bool) or max_retained_fragments <= 0:
            raise ValueError("max_retained_fragments must be positive when set")
    if (
        isinstance(max_retained_age_seconds, bool)
        or not isinstance(max_retained_age_seconds, (int, float))
        or not math.isfinite(max_retained_age_seconds)
        or max_retained_age_seconds < 0
    ):
        raise ValueError("max_retained_age_seconds must be finite and nonnegative")

    new_fragments = tuple(fragments)
    if not retain_existing:
        return new_fragments

    cutoff = generated_at - timedelta(seconds=float(max_retained_age_seconds))
    latest_allowed = generated_at + timedelta(seconds=MAX_FUTURE_GENERATED_AT_SECONDS)
    retained = [
        fragment
        for fragment in _read_existing_fragments(out_path)
        if cutoff <= fragment.asof_ts <= latest_allowed
    ]
    merged = sorted(
        (*new_fragments, *retained),
        key=lambda fragment: fragment.asof_ts,
        reverse=True,
    )
    deduped = _dedupe_fragments_by_id(merged)
    if max_retained_fragments is not None:
        return deduped[:max_retained_fragments]
    return deduped


def _read_existing_fragments(out_path: Path) -> tuple[GeneratorFragment, ...]:
    try:
        raw = json.loads(
            out_path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return ()
    if not isinstance(raw, dict):
        return ()
    raw_fragments = raw.get("fragments")
    if not isinstance(raw_fragments, list):
        return ()
    fragments: list[GeneratorFragment] = []
    for row in raw_fragments:
        try:
            fragments.append(_fragment_from_json_dict(row))
        except (TypeError, ValueError):
            continue
    return tuple(fragments)


def _dedupe_fragments_by_id(
    fragments: Iterable[GeneratorFragment],
) -> tuple[GeneratorFragment, ...]:
    selected: list[GeneratorFragment] = []
    seen: set[str] = set()
    for fragment in fragments:
        if fragment.fragment_id in seen:
            continue
        seen.add(fragment.fragment_id)
        selected.append(fragment)
    return tuple(selected)


def read_probability_fragments(
    out_path: Path,
    max_age_seconds: float,
) -> ProbabilityFragmentsPayload:
    if not isinstance(max_age_seconds, (int, float)) or not math.isfinite(max_age_seconds):
        raise ValueError("max_age_seconds must be finite")
    if max_age_seconds < 0:
        raise ValueError("max_age_seconds must be nonnegative")

    try:
        raw = json.loads(
            out_path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed probability fragments JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("probability fragments payload must be a JSON object")
    schema_version = raw.get("schema_version")
    if schema_version != GENERATOR_FRAGMENTS_SCHEMA_VERSION:
        raise ValueError("probability fragments schema version mismatch")

    generated_at = _parse_datetime(raw.get("generated_at"), "generated_at")
    age_seconds = (datetime.now(timezone.utc) - generated_at).total_seconds()
    if age_seconds < -MAX_FUTURE_GENERATED_AT_SECONDS:
        raise ValueError(f"future probability fragments snapshot: age_seconds={age_seconds:.3f}")
    if age_seconds > max_age_seconds:
        raise ValueError(f"stale probability fragments snapshot: age_seconds={age_seconds:.3f}")

    raw_fragments = raw.get("fragments")
    if not isinstance(raw_fragments, list):
        raise ValueError("fragments must be a JSON array")
    try:
        fragments = tuple(_fragment_from_json_dict(row) for row in raw_fragments)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid fragment row: {exc}") from exc

    return ProbabilityFragmentsPayload(
        schema_version=cast(str, schema_version),
        generated_at=generated_at,
        fragments=fragments,
    )


def select_fragments_for_input(
    fragments: Sequence[GeneratorFragment],
    probability_input: ProbabilityInput,
    min_fragment_count: int,
    max_fragment_count: int,
) -> FragmentSelection:
    if isinstance(min_fragment_count, bool) or not isinstance(min_fragment_count, int):
        raise ValueError("min_fragment_count must be an integer")
    if isinstance(max_fragment_count, bool) or not isinstance(max_fragment_count, int):
        raise ValueError("max_fragment_count must be an integer")
    if min_fragment_count < 0:
        raise ValueError("min_fragment_count must be nonnegative")
    if max_fragment_count <= 0:
        raise ValueError("max_fragment_count must be positive")

    target_bucket = _z_path_bucket(probability_input.z_path)
    exact = _dedupe_fragments(
        fragment
        for fragment in fragments
        if _eligible_for_input(fragment, probability_input)
        and fragment.z_path_bucket == target_bucket
    )
    if exact:
        selected = exact[:max_fragment_count]
        return FragmentSelection(
            fragments=selected,
            sparse=len(selected) < min_fragment_count,
            reason="exact",
        )

    coarse = _dedupe_fragments(
        fragment for fragment in fragments if _eligible_for_input(fragment, probability_input)
    )
    if coarse:
        selected = coarse[:max_fragment_count]
        return FragmentSelection(
            fragments=selected,
            sparse=len(selected) < min_fragment_count,
            reason="coarse",
        )

    return FragmentSelection(fragments=(), sparse=True, reason="missing")


def _eligible_for_input(
    fragment: GeneratorFragment,
    probability_input: ProbabilityInput,
) -> bool:
    return (
        fragment.asset == probability_input.asset
        and fragment.asof_ts <= probability_input.asof_ts
        and fragment.horizon_seconds >= probability_input.seconds_left
        and fragment.quality_bucket == "OK"
    )


def _dedupe_fragments(
    fragments: Iterable[GeneratorFragment],
) -> tuple[GeneratorFragment, ...]:
    selected: list[GeneratorFragment] = []
    seen: set[tuple[object, ...]] = set()
    for fragment in fragments:
        key = (
            fragment.asset,
            fragment.source_key,
            fragment.asof_ts,
            fragment.horizon_seconds,
            fragment.prices,
        )
        if key in seen:
            continue
        seen.add(key)
        selected.append(fragment)
    return tuple(selected)


def _z_path_bucket(z_path: float) -> str:
    if z_path < -1:
        return "deep_down"
    if z_path > 1:
        return "deep_up"
    return "near"


def _fragment_to_json_dict(fragment: GeneratorFragment) -> dict[str, Any]:
    row: dict[str, Any] = {
        "asset": fragment.asset,
        "asof_ts": _require_aware_datetime_utc(fragment.asof_ts, "asof_ts").isoformat(),
        "fragment_id": fragment.fragment_id,
        "horizon_seconds": fragment.horizon_seconds,
        "prices": list(fragment.prices),
        "quality_bucket": fragment.quality_bucket,
        "source_key": fragment.source_key,
        "z_path_bucket": fragment.z_path_bucket,
    }
    if fragment.metadata is not None:
        row["metadata"] = _json_ready(fragment.metadata)
    return row


def _fragment_from_json_dict(value: object) -> GeneratorFragment:
    if not isinstance(value, dict):
        raise ValueError("row must be a JSON object")
    return GeneratorFragment(
        fragment_id=_required_str(value, "fragment_id"),
        asset=_required_str(value, "asset"),
        asof_ts=_parse_datetime(value.get("asof_ts"), "asof_ts"),
        prices=fragment_prices_from_mapping(value),
        horizon_seconds=_required_int(value, "horizon_seconds"),
        source_key=_optional_str(value.get("source_key"), "source_key"),
        z_path_bucket=_optional_str(value.get("z_path_bucket"), "z_path_bucket"),
        quality_bucket=_optional_str(value.get("quality_bucket"), "quality_bucket"),
        metadata=_normalize_metadata(value.get("metadata")),
    )


def _require_aware_datetime_utc(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO datetime string") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"nonfinite JSON constant is not allowed: {value}")


def _required_str(value: Mapping[str, object], field_name: str) -> str:
    item = value.get(field_name)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field_name} must be a non-empty string")
    return item


def _optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string when set")
    return value


def _required_int(value: Mapping[str, object], field_name: str) -> int:
    item = value.get(field_name)
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError(f"{field_name} must be an integer")
    return item


def _json_ready(value: object) -> object:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float")
        return value
    if isinstance(value, datetime):
        return _require_aware_datetime_utc(value, "datetime").isoformat()
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, Mapping):
        parsed: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("object keys must be strings")
            parsed[key] = _json_ready(item)
        return parsed
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _require_positive_price(value: float, field_name: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{field_name} values must be finite numbers")
    if value <= 0:
        raise ValueError(f"{field_name} values must be positive")


def _require_aware_datetime(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def fragment_prices_from_mapping(payload: Mapping[str, object]) -> tuple[float, ...]:
    raw_prices = payload.get("prices")
    if not isinstance(raw_prices, Sequence) or isinstance(raw_prices, (str, bytes)):
        raise ValueError("fragment payload must include iterable prices")
    return _coerce_prices(tuple(raw_prices))


def parse_generator_fragment(payload: Mapping[str, object]) -> GeneratorFragment:
    fragment_id = payload.get("fragment_id")
    if not isinstance(fragment_id, str) or not fragment_id:
        raise ValueError("fragment_id must be a non-empty string")

    asset = payload.get("asset")
    if not isinstance(asset, str) or asset not in {"BTC", "ETH"}:
        raise ValueError("asset must be BTC or ETH")

    asof_ts = payload.get("asof_ts")
    if not isinstance(asof_ts, datetime):
        raise ValueError("asof_ts must be datetime")

    horizon_seconds = payload.get("horizon_seconds")
    if not isinstance(horizon_seconds, int) or horizon_seconds <= 0:
        raise ValueError("horizon_seconds must be a positive integer")

    prices = fragment_prices_from_mapping(payload)
    source_key = payload.get("source_key")
    z_path_bucket = payload.get("z_path_bucket")
    quality_bucket = payload.get("quality_bucket")
    return GeneratorFragment(
        fragment_id=fragment_id,
        asset=asset,
        asof_ts=asof_ts,
        prices=prices,
        horizon_seconds=horizon_seconds,
        source_key=source_key if isinstance(source_key, str) else None,
        z_path_bucket=z_path_bucket if isinstance(z_path_bucket, str) else None,
        quality_bucket=quality_bucket if isinstance(quality_bucket, str) else None,
        metadata=_normalize_metadata(payload.get("metadata")),
    )


def normalize_fragment_prices(
    prices: Sequence[float],
    *,
    settlement_price: float,
    target_len: int,
) -> tuple[float, ...]:
    values = _coerce_prices(tuple(prices))
    if settlement_price <= 0 or not math.isfinite(settlement_price):
        raise ValueError("settlement_price must be positive and finite")
    if target_len <= 1:
        raise ValueError("target_len must be > 1")

    scale = settlement_price / values[0]
    scaled = tuple(value * scale for value in values)
    if target_len == len(scaled):
        return scaled
    if target_len == 1:
        raise ValueError("target_len must be > 1")

    mapped: list[float] = []
    for index in range(target_len):
        source_index = round(index * (len(scaled) - 1) / (target_len - 1))
        mapped.append(scaled[source_index])
    return tuple(mapped)


def select_fragments(
    fragments: Sequence[GeneratorFragment],
    *,
    asset: str | None = None,
    min_horizon_seconds: int | None = None,
    max_horizon_seconds: int | None = None,
    asof_cutoff: datetime | None = None,
    z_path_bucket: str | None = None,
) -> tuple[GeneratorFragment, ...]:
    selected: list[GeneratorFragment] = []
    for fragment in fragments:
        if asset is not None and fragment.asset != asset:
            continue
        if min_horizon_seconds is not None and fragment.horizon_seconds < min_horizon_seconds:
            continue
        if max_horizon_seconds is not None and fragment.horizon_seconds > max_horizon_seconds:
            continue
        if asof_cutoff is not None and fragment.asof_ts > asof_cutoff:
            continue
        if z_path_bucket is not None and fragment.z_path_bucket != z_path_bucket:
            continue
        selected.append(fragment)
    return tuple(selected)


def _coerce_prices(values: Sequence[object]) -> tuple[float, ...]:
    if not values:
        raise ValueError("prices must not be empty")
    parsed: list[float] = []
    for value in values:
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
            raise ValueError("prices must be finite numbers")
        float_value = float(value)
        if float_value <= 0:
            raise ValueError("prices must be strictly positive")
        parsed.append(float_value)
    return tuple(parsed)


def _log_returns(values: tuple[float, ...]) -> tuple[float, ...]:
    return tuple(
        math.log(current / previous)
        for previous, current in zip(values, values[1:])
        if previous > 0 and current > 0
    )


def _normalize_metadata(value: object) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("metadata must be mapping if provided")
    return value
