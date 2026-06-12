from __future__ import annotations

import json
import math
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence, cast

from polymarket_engine.domain.market_state import DecisionState
from polymarket_engine.features.volatility import is_volatility_failure_regime
from polymarket_engine.probability.runtime_inputs import (
    ProbabilityRuntimeInput,
    ProbabilityState,
    ThresholdDiagnostics,
    contract_label,
)
from polymarket_engine.probability.schema import ProbabilityInput
from polymarket_engine.storage.atomic import durable_replace

HOT_PROBABILITY_INPUTS_SCHEMA_VERSION = "polymarket-hot-probability-inputs-v1"
MAX_FUTURE_GENERATED_AT_SECONDS = 5.0
THRESHOLD_MUTATION_ERROR = "THRESHOLD_MUTATION_ERROR"
SIGMA_INVALID_BLOCK_REASON = "sigma_invalid"
DIAGNOSTIC_SIGMA_TAU_FLOOR = 0.00005
VOLATILITY_QUALITY_FLAGS = frozenset({"missing_volatility"})


@dataclass(frozen=True)
class HotProbabilityInputPayload:
    schema_version: str
    generated_at: datetime
    inputs: tuple[ProbabilityRuntimeInput, ...]
    skipped: int


@dataclass(frozen=True)
class _ThresholdAssignment:
    threshold: float
    rule_hash: str


@dataclass(frozen=True)
class _SigmaDiagnostics:
    sigma_tau: float | None
    sigma_valid: bool
    sigma_age_ms: int
    last_sigma_update_ts: datetime | None
    short_vol: float | None
    medium_vol: float | None
    long_vol: float | None
    volatility_floor_applied: bool
    regime_multiplier_applied: bool
    failure_reason: str | None
    input_sample_count: int


def write_hot_probability_inputs(
    *,
    out_path: Path,
    states: Sequence[DecisionState],
    generated_at: datetime,
) -> None:
    generated_at_utc = _require_aware_datetime(generated_at, "generated_at")
    rows: list[dict[str, Any]] = []
    skipped = 0
    threshold_assignments = _previous_threshold_assignments(out_path)

    for state in states:
        if _should_skip_for_quality_flags(state):
            skipped += 1
            continue
        sigma_diagnostics = _sigma_diagnostics(state)
        probability_source_state = state
        if not sigma_diagnostics.sigma_valid:
            probability_source_state = replace(
                state,
                sigma_tau=DIAGNOSTIC_SIGMA_TAU_FLOOR,
                data_quality_flags=(),
            )
        probability_input = ProbabilityInput.from_decision_state(probability_source_state)
        previous_assignment = threshold_assignments.get(state.contract.contract_id)
        threshold_diagnostics = _threshold_diagnostics(
            state=state,
            previous_assignment=previous_assignment,
        )
        probability_state: ProbabilityState = "READY"
        offload_allowed = True
        k_stable = True
        flags = ("OK",)
        block_reasons: tuple[str, ...] = ()
        threshold_mutated_without_rule_change = (
            previous_assignment is not None
            and previous_assignment.rule_hash == state.contract.rule_hash
            and previous_assignment.threshold != state.threshold
        )
        if not sigma_diagnostics.sigma_valid:
            probability_state = "BLOCKED_OR_STALE"
            offload_allowed = False
            block_reasons = (SIGMA_INVALID_BLOCK_REASON,)
        elif threshold_mutated_without_rule_change:
            probability_state = "BLOCKED"
            k_stable = False
            flags = (THRESHOLD_MUTATION_ERROR,)
        else:
            threshold_assignments[state.contract.contract_id] = _ThresholdAssignment(
                threshold=state.threshold,
                rule_hash=state.contract.rule_hash,
            )
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
            flags=flags,
            market_slug=state.contract.slug,
            volatility_regime=state.volatility_regime,
            probability_state=probability_state,
            k_stable=k_stable,
            threshold_diagnostics=threshold_diagnostics,
            sigma_tau=sigma_diagnostics.sigma_tau,
            sigma_valid=sigma_diagnostics.sigma_valid,
            sigma_age_ms=sigma_diagnostics.sigma_age_ms,
            last_sigma_update_ts=sigma_diagnostics.last_sigma_update_ts,
            short_vol=sigma_diagnostics.short_vol,
            medium_vol=sigma_diagnostics.medium_vol,
            long_vol=sigma_diagnostics.long_vol,
            volatility_floor_applied=sigma_diagnostics.volatility_floor_applied,
            regime_multiplier_applied=sigma_diagnostics.regime_multiplier_applied,
            failure_reason=sigma_diagnostics.failure_reason,
            input_sample_count=sigma_diagnostics.input_sample_count,
            offload_allowed=offload_allowed,
            block_reasons=block_reasons,
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
    row = {
        "contract": runtime_input.contract,
        "contract_id": runtime_input.contract_id,
        "expiry_ts": runtime_input.expiry_ts.isoformat(),
        "flags": list(runtime_input.flags),
        "k_stable": runtime_input.k_stable,
        "block_reasons": list(runtime_input.block_reasons),
        "failure_reason": runtime_input.failure_reason,
        "input_sample_count": runtime_input.input_sample_count,
        "last_sigma_update_ts": (
            None
            if runtime_input.last_sigma_update_ts is None
            else runtime_input.last_sigma_update_ts.isoformat()
        ),
        "long_vol": runtime_input.long_vol,
        "medium_vol": runtime_input.medium_vol,
        "offload_allowed": runtime_input.offload_allowed,
        "probability_state": runtime_input.probability_state,
        "probability_input": runtime_input.probability_input.to_json_dict(),
        "regime_multiplier_applied": runtime_input.regime_multiplier_applied,
        "short_vol": runtime_input.short_vol,
        "sigma_age_ms": runtime_input.sigma_age_ms,
        "sigma_tau": runtime_input.sigma_tau,
        "sigma_valid": runtime_input.sigma_valid,
        "start_ts": runtime_input.start_ts.isoformat(),
        "volatility_floor_applied": runtime_input.volatility_floor_applied,
    }
    if runtime_input.market_slug:
        row["market_slug"] = runtime_input.market_slug
    if runtime_input.volatility_regime:
        row["volatility_regime"] = runtime_input.volatility_regime
    if runtime_input.threshold_diagnostics is not None:
        row["threshold_diagnostics"] = _threshold_diagnostics_to_json_dict(
            runtime_input.threshold_diagnostics
        )
    return row


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
        market_slug = _optional_str(value.get("market_slug"), "market_slug") or ""
        volatility_regime = _optional_str(
            value.get("volatility_regime"),
            "volatility_regime",
        )
        probability_state = _probability_state(value.get("probability_state"))
        k_stable = _optional_bool(value.get("k_stable"), "k_stable")
        threshold_diagnostics = _optional_threshold_diagnostics(
            value.get("threshold_diagnostics")
        )
        sigma_tau = _optional_float(value.get("sigma_tau"), "sigma_tau")
        sigma_valid = _optional_bool(value.get("sigma_valid"), "sigma_valid")
        sigma_age_ms = _optional_int(value.get("sigma_age_ms"), "sigma_age_ms")
        last_sigma_update_ts = _optional_datetime(
            value.get("last_sigma_update_ts"),
            "last_sigma_update_ts",
        )
        short_vol = _optional_float(value.get("short_vol"), "short_vol")
        medium_vol = _optional_float(value.get("medium_vol"), "medium_vol")
        long_vol = _optional_float(value.get("long_vol"), "long_vol")
        volatility_floor_applied = _optional_bool(
            value.get("volatility_floor_applied"),
            "volatility_floor_applied",
        )
        regime_multiplier_applied = _optional_bool(
            value.get("regime_multiplier_applied"),
            "regime_multiplier_applied",
        )
        failure_reason = _optional_str(value.get("failure_reason"), "failure_reason")
        input_sample_count = _optional_int(
            value.get("input_sample_count"),
            "input_sample_count",
        )
        offload_allowed = _optional_bool(value.get("offload_allowed"), "offload_allowed")
        block_reasons = _string_tuple(value.get("block_reasons"), "block_reasons")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid input row: {exc}") from exc
    return ProbabilityRuntimeInput(
        probability_input=probability_input,
        contract_id=contract_id,
        contract=contract,
        start_ts=start_ts,
        expiry_ts=expiry_ts,
        flags=flags,
        market_slug=market_slug,
        volatility_regime=volatility_regime,
        probability_state=probability_state,
        k_stable=True if k_stable is None else k_stable,
        threshold_diagnostics=threshold_diagnostics,
        sigma_tau=sigma_tau,
        sigma_valid=True if sigma_valid is None else sigma_valid,
        sigma_age_ms=0 if sigma_age_ms is None else sigma_age_ms,
        last_sigma_update_ts=last_sigma_update_ts,
        short_vol=short_vol,
        medium_vol=medium_vol,
        long_vol=long_vol,
        volatility_floor_applied=(
            False if volatility_floor_applied is None else volatility_floor_applied
        ),
        regime_multiplier_applied=(
            False if regime_multiplier_applied is None else regime_multiplier_applied
        ),
        failure_reason=failure_reason,
        input_sample_count=0 if input_sample_count is None else input_sample_count,
        offload_allowed=True if offload_allowed is None else offload_allowed,
        block_reasons=block_reasons,
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


def _sigma_diagnostics(state: DecisionState) -> _SigmaDiagnostics:
    raw_sigma = state.sigma_tau
    sigma_tau = _finite_optional_float(raw_sigma)
    failure_reason = _sigma_failure_reason(
        sigma_tau=raw_sigma,
        volatility_regime=state.volatility_regime,
    )
    last_sigma_update_ts = (
        state.asof_ts
        if state.realized_returns and not is_volatility_failure_regime(state.volatility_regime)
        else None
    )
    return _SigmaDiagnostics(
        sigma_tau=sigma_tau,
        sigma_valid=failure_reason is None,
        sigma_age_ms=0,
        last_sigma_update_ts=last_sigma_update_ts,
        short_vol=_finite_optional_float(state.short_realized_vol),
        medium_vol=_finite_optional_float(state.medium_realized_vol),
        long_vol=_finite_optional_float(state.long_realized_vol),
        volatility_floor_applied=False,
        regime_multiplier_applied=state.volatility_regime in {"expanding", "contracting"},
        failure_reason=failure_reason,
        input_sample_count=len(state.realized_returns),
    )


def _sigma_failure_reason(
    *,
    sigma_tau: float | None,
    volatility_regime: str | None,
) -> str | None:
    if sigma_tau is None:
        return "sigma_missing"
    if not math.isfinite(sigma_tau):
        return "sigma_nonfinite"
    if sigma_tau <= 0:
        return "sigma_non_positive"
    if is_volatility_failure_regime(volatility_regime):
        return volatility_regime
    return None


def _finite_optional_float(value: float | None) -> float | None:
    if value is None:
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _should_skip_for_quality_flags(state: DecisionState) -> bool:
    if not state.data_quality_flags:
        return False
    flags = set(state.data_quality_flags)
    return not flags.issubset(VOLATILITY_QUALITY_FLAGS)


def _require_aware_datetime(value: datetime, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _threshold_diagnostics(
    *,
    state: DecisionState,
    previous_assignment: _ThresholdAssignment | None,
) -> ThresholdDiagnostics:
    reason = "initial_assignment"
    previous_threshold = None
    if previous_assignment is not None:
        previous_threshold = previous_assignment.threshold
        if previous_assignment.rule_hash != state.contract.rule_hash:
            reason = "rule_hash_changed"
        elif previous_assignment.threshold == state.threshold:
            reason = "unchanged"
        else:
            reason = "threshold_changed_without_rule_hash_change"
    return ThresholdDiagnostics(
        contract_id=state.contract.contract_id,
        market_slug=state.contract.slug,
        asset=state.contract.asset,
        side=state.contract.side,
        K=state.threshold,
        K_source=state.threshold_source_key,
        rule_hash=state.contract.rule_hash,
        timestamp=(
            state.threshold_observed_ts
            or state.threshold_event_ts
            or state.asof_ts
        ),
        previous_K=previous_threshold,
        new_K=state.threshold,
        reason_for_change=reason,
    )


def _threshold_diagnostics_to_json_dict(
    diagnostics: ThresholdDiagnostics,
) -> dict[str, Any]:
    return {
        "contract_id": diagnostics.contract_id,
        "market_slug": diagnostics.market_slug,
        "asset": diagnostics.asset,
        "side": diagnostics.side,
        "K": diagnostics.K,
        "K_source": diagnostics.K_source,
        "rule_hash": diagnostics.rule_hash,
        "timestamp": diagnostics.timestamp.isoformat(),
        "previous_K": diagnostics.previous_K,
        "new_K": diagnostics.new_K,
        "reason_for_change": diagnostics.reason_for_change,
    }


def _previous_threshold_assignments(out_path: Path) -> dict[str, _ThresholdAssignment]:
    try:
        raw = json.loads(
            out_path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    if raw.get("schema_version") != HOT_PROBABILITY_INPUTS_SCHEMA_VERSION:
        return {}
    rows = raw.get("inputs")
    if not isinstance(rows, list):
        return {}

    assignments: dict[str, _ThresholdAssignment] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        diagnostics = row.get("threshold_diagnostics")
        if not isinstance(diagnostics, dict):
            continue
        try:
            contract_id = _required_str(diagnostics, "contract_id")
            threshold = _assignment_threshold_from_diagnostics(diagnostics)
            rule_hash = _required_str(diagnostics, "rule_hash")
        except ValueError:
            continue
        assignments[contract_id] = _ThresholdAssignment(
            threshold=threshold,
            rule_hash=rule_hash,
        )
    return assignments


def _assignment_threshold_from_diagnostics(diagnostics: dict[str, Any]) -> float:
    reason = _optional_str(diagnostics.get("reason_for_change"), "reason_for_change")
    previous_threshold = _optional_float(diagnostics.get("previous_K"), "previous_K")
    if (
        reason == "threshold_changed_without_rule_hash_change"
        and previous_threshold is not None
    ):
        return previous_threshold
    return _required_float(diagnostics, "new_K")


def _optional_threshold_diagnostics(value: object) -> ThresholdDiagnostics | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("threshold_diagnostics must be a JSON object when set")
    return ThresholdDiagnostics(
        contract_id=_required_str(value, "contract_id"),
        market_slug=_required_str(value, "market_slug"),
        asset=_required_str(value, "asset"),
        side=_required_str(value, "side"),
        K=_required_float(value, "K"),
        K_source=_optional_str(value.get("K_source"), "K_source"),
        rule_hash=_required_str(value, "rule_hash"),
        timestamp=_parse_datetime(value.get("timestamp"), "timestamp"),
        previous_K=_optional_float(value.get("previous_K"), "previous_K"),
        new_K=_required_float(value, "new_K"),
        reason_for_change=_required_str(value, "reason_for_change"),
    )


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


def _optional_datetime(value: object, field_name: str) -> datetime | None:
    if value is None:
        return None
    return _parse_datetime(value, field_name)


def _required_str(value: dict[str, Any], field_name: str) -> str:
    item = value.get(field_name)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{field_name} must be a non-empty string")
    return item


def _probability_state(value: object) -> ProbabilityState:
    if value is None:
        return "READY"
    if value not in {"READY", "BLOCKED", "BLOCKED_OR_STALE"}:
        raise ValueError("probability_state must be READY, BLOCKED, or BLOCKED_OR_STALE")
    return cast(ProbabilityState, value)


def _optional_bool(value: object, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean when set")
    return value


def _optional_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a nonnegative integer when set")
    return value


def _optional_str(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty string when set")
    return value


def _required_float(value: dict[str, Any], field_name: str) -> float:
    item = value.get(field_name)
    if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
        raise ValueError(f"{field_name} must be finite")
    return float(item)


def _optional_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite when set")
    return float(value)


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


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array when set")
    items = tuple(value)
    if not all(isinstance(item, str) and item for item in items):
        raise ValueError(f"{field_name} must contain non-empty strings")
    return items
