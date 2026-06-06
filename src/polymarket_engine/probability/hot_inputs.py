from __future__ import annotations

import json
import math
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence, cast

from polymarket_engine.domain.market_state import DecisionState
from polymarket_engine.probability.runtime_inputs import ProbabilityRuntimeInput, contract_label
from polymarket_engine.probability.schema import ProbabilityInput
from polymarket_engine.storage.atomic import durable_replace

HOT_PROBABILITY_INPUTS_SCHEMA_VERSION = "polymarket-hot-probability-inputs-v1"
MAX_FUTURE_GENERATED_AT_SECONDS = 5.0


@dataclass(frozen=True)
class HotProbabilityInputPayload:
    schema_version: str
    generated_at: datetime
    inputs: tuple[ProbabilityRuntimeInput, ...]
    skipped: int


def write_hot_probability_inputs(
    *,
    out_path: Path,
    states: Sequence[DecisionState],
    generated_at: datetime,
) -> None:
    generated_at_utc = _require_aware_datetime(generated_at, "generated_at")
    rows: list[dict[str, Any]] = []
    skipped = 0

    for state in states:
        if state.data_quality_flags:
            skipped += 1
            continue
        probability_input = ProbabilityInput.from_decision_state(state)
        runtime_input = ProbabilityRuntimeInput(
            probability_input=probability_input,
            contract_id=state.contract.contract_id,
            contract=contract_label(
                asset=probability_input.asset,
                side=probability_input.side,
                start_ts=state.contract.start_ts,
                expiry_ts=state.contract.expiry_ts,
            ),
            start_ts=state.contract.start_ts,
            expiry_ts=state.contract.expiry_ts,
            flags=("OK",),
        )
        rows.append(_runtime_input_to_json_dict(runtime_input))

    payload = {
        "schema_version": HOT_PROBABILITY_INPUTS_SCHEMA_VERSION,
        "generated_at": generated_at_utc.isoformat(),
        "inputs": rows,
        "skipped": skipped,
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


def read_hot_probability_inputs(
    *,
    out_path: Path,
    limit: int,
    max_age_seconds: float,
) -> HotProbabilityInputPayload:
    if limit <= 0:
        raise ValueError("limit must be positive")
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
        raise ValueError(f"malformed hot probability inputs JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("hot probability inputs payload must be a JSON object")
    schema_version = raw.get("schema_version")
    if schema_version != HOT_PROBABILITY_INPUTS_SCHEMA_VERSION:
        raise ValueError("hot probability inputs schema version mismatch")

    generated_at = _parse_datetime(raw.get("generated_at"), "generated_at")
    age_seconds = (datetime.now(timezone.utc) - generated_at).total_seconds()
    if age_seconds < -MAX_FUTURE_GENERATED_AT_SECONDS:
        raise ValueError(
            f"future hot probability inputs snapshot: age_seconds={age_seconds:.3f}"
        )
    if age_seconds > max_age_seconds:
        raise ValueError(
            f"stale hot probability inputs snapshot: age_seconds={age_seconds:.3f}"
        )

    raw_skipped = raw.get("skipped")
    if isinstance(raw_skipped, bool) or not isinstance(raw_skipped, int) or raw_skipped < 0:
        raise ValueError("skipped must be a nonnegative integer")

    raw_inputs = raw.get("inputs")
    if not isinstance(raw_inputs, list):
        raise ValueError("inputs must be a JSON array")
    all_inputs = tuple(_runtime_input_from_json_dict(row) for row in raw_inputs)
    return HotProbabilityInputPayload(
        schema_version=cast(str, schema_version),
        generated_at=generated_at,
        inputs=all_inputs[:limit],
        skipped=raw_skipped,
    )


def _runtime_input_to_json_dict(runtime_input: ProbabilityRuntimeInput) -> dict[str, Any]:
    return {
        "contract": runtime_input.contract,
        "contract_id": runtime_input.contract_id,
        "expiry_ts": runtime_input.expiry_ts.isoformat(),
        "flags": list(runtime_input.flags),
        "probability_input": runtime_input.probability_input.to_json_dict(),
        "start_ts": runtime_input.start_ts.isoformat(),
    }


def _runtime_input_from_json_dict(value: object) -> ProbabilityRuntimeInput:
    if not isinstance(value, dict):
        raise ValueError("invalid input row: row must be a JSON object")
    try:
        probability_input = _probability_input_from_json_dict(value.get("probability_input"))
        contract_id = _required_str(value, "contract_id")
        contract = _required_str(value, "contract")
        start_ts = _parse_datetime(value.get("start_ts"), "start_ts")
        expiry_ts = _parse_datetime(value.get("expiry_ts"), "expiry_ts")
        flags = _flags(value.get("flags"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid input row: {exc}") from exc
    return ProbabilityRuntimeInput(
        probability_input=probability_input,
        contract_id=contract_id,
        contract=contract,
        start_ts=start_ts,
        expiry_ts=expiry_ts,
        flags=flags,
    )


def _probability_input_from_json_dict(value: object) -> ProbabilityInput:
    if not isinstance(value, dict):
        raise ValueError("probability_input must be a JSON object")
    return ProbabilityInput(
        state_id=_required_str(value, "state_id"),
        asof_ts=_parse_datetime(value.get("asof_ts"), "asof_ts"),
        asset=_required_str(value, "asset"),
        side=_required_str(value, "side"),
        comparison_operator=_required_str(value, "comparison_operator"),
        seconds_left=_required_float(value, "seconds_left"),
        settlement_price=_required_float(value, "settlement_price"),
        threshold=_required_float(value, "threshold"),
        sigma_tau=_required_float(value, "sigma_tau"),
        executable_price=_required_float(value, "executable_price"),
        source_age_ms=_required_int(value, "source_age_ms"),
        book_age_ms=_required_int(value, "book_age_ms"),
        z_path=_required_float(value, "z_path"),
    )


def _require_aware_datetime(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"nonfinite JSON constant is not allowed: {value}")


def _parse_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO datetime string") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _required_str(value: dict[str, Any], field_name: str) -> str:
    item = value.get(field_name)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field_name} must be a non-empty string")
    return item


def _required_float(value: dict[str, Any], field_name: str) -> float:
    item = value.get(field_name)
    if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
        raise ValueError(f"{field_name} must be finite")
    return float(item)


def _required_int(value: dict[str, Any], field_name: str) -> int:
    item = value.get(field_name)
    if isinstance(item, bool) or not isinstance(item, int):
        raise ValueError(f"{field_name} must be an integer")
    return item


def _flags(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("flags must be a non-empty array")
    flags = tuple(value)
    if not all(isinstance(flag, str) and flag for flag in flags):
        raise ValueError("flags must contain non-empty strings")
    return flags
