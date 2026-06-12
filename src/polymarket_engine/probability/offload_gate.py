from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal

from polymarket_engine.ops.recovery_manager import RuntimePhase


WorkerMode = Literal["disabled", "nowcast_only", "min_mc", "normal_mc", "gpu_mc"]

DEFAULT_MAX_PROBABILITY_INPUT_AGE_MS = 25_000

_NOWCAST_SAFE_PHASES = {RuntimePhase.WARMING, RuntimePhase.RECOVERING}
_HARD_OR_DATA_INTEGRITY_BLOCKERS = {
    "sigma_invalid",
    "k_unstable",
    "duckdb_unhealthy",
    "price_stale",
    "orderbook_stale",
    "probability_inputs_stale",
    "volatility_stale",
    "target_stale",
    "sigma_stale",
    "api_unhealthy",
    "normalized_health_unhealthy",
    "websocket_unhealthy",
    "api_blocked_recent",
    "decode_error_recent",
}


@dataclass(frozen=True)
class OffloadGateConfig:
    warmup_min_seconds: int = 60
    required_healthy_cycles: int = 3
    max_price_age_ms: int = 2_000
    max_orderbook_age_ms: int = 1_000
    max_probability_input_age_ms: int = DEFAULT_MAX_PROBABILITY_INPUT_AGE_MS
    max_volatility_age_ms: int = 12_000
    max_target_status_age_ms: int = 1_000
    max_sigma_tau_age_ms: int = 12_000
    cpu_soft_max_percent: float = 80.0
    memory_soft_max_mb: int = 2_048
    queue_soft_max: int = 100
    normal_after_seconds: int = 300

    def __post_init__(self) -> None:
        _validate_nonnegative_int("warmup_min_seconds", self.warmup_min_seconds)
        _validate_nonnegative_int(
            "required_healthy_cycles",
            self.required_healthy_cycles,
        )
        _validate_nonnegative_int("max_price_age_ms", self.max_price_age_ms)
        _validate_nonnegative_int("max_orderbook_age_ms", self.max_orderbook_age_ms)
        _validate_nonnegative_int(
            "max_probability_input_age_ms",
            self.max_probability_input_age_ms,
        )
        _validate_nonnegative_int("max_volatility_age_ms", self.max_volatility_age_ms)
        _validate_nonnegative_int(
            "max_target_status_age_ms",
            self.max_target_status_age_ms,
        )
        _validate_nonnegative_int("max_sigma_tau_age_ms", self.max_sigma_tau_age_ms)
        _validate_nonnegative_float("cpu_soft_max_percent", self.cpu_soft_max_percent)
        _validate_nonnegative_int("memory_soft_max_mb", self.memory_soft_max_mb)
        _validate_nonnegative_int("queue_soft_max", self.queue_soft_max)
        _validate_nonnegative_int("normal_after_seconds", self.normal_after_seconds)
        if self.normal_after_seconds < self.warmup_min_seconds:
            raise ValueError("normal_after_seconds must be >= warmup_min_seconds")


@dataclass(frozen=True)
class OffloadGateInputs:
    runtime_phase: RuntimePhase
    uptime_seconds: float
    consecutive_healthy_cycles: int
    price_age_ms: int
    orderbook_age_ms: int
    probability_input_age_ms: int
    volatility_age_ms: int
    target_status_age_ms: int
    sigma_tau_valid: bool
    sigma_tau_age_ms: int
    k_stable: bool
    api_status: str
    normalized_health_status: str
    duckdb_status: str
    websocket_status: str
    cpu_percent: float | None
    memory_mb: int | None
    queue_length: int | None
    recent_api_blocked: bool
    recent_decode_error: bool
    configured_max_total_paths: int
    min_total_paths: int

    def __post_init__(self) -> None:
        _validate_nonnegative_float("uptime_seconds", self.uptime_seconds)
        _validate_nonnegative_int(
            "consecutive_healthy_cycles",
            self.consecutive_healthy_cycles,
        )
        _validate_nonnegative_int("price_age_ms", self.price_age_ms)
        _validate_nonnegative_int("orderbook_age_ms", self.orderbook_age_ms)
        _validate_nonnegative_int(
            "probability_input_age_ms",
            self.probability_input_age_ms,
        )
        _validate_nonnegative_int("volatility_age_ms", self.volatility_age_ms)
        _validate_nonnegative_int("target_status_age_ms", self.target_status_age_ms)
        _validate_nonnegative_int("sigma_tau_age_ms", self.sigma_tau_age_ms)
        _validate_nonempty_string("api_status", self.api_status)
        _validate_nonempty_string(
            "normalized_health_status",
            self.normalized_health_status,
        )
        _validate_nonempty_string("duckdb_status", self.duckdb_status)
        _validate_nonempty_string("websocket_status", self.websocket_status)
        _validate_optional_nonnegative_float("cpu_percent", self.cpu_percent)
        _validate_optional_nonnegative_int("memory_mb", self.memory_mb)
        _validate_optional_nonnegative_int("queue_length", self.queue_length)
        _validate_positive_int(
            "configured_max_total_paths",
            self.configured_max_total_paths,
        )
        _validate_positive_int("min_total_paths", self.min_total_paths)
        if self.min_total_paths > self.configured_max_total_paths:
            raise ValueError("min_total_paths must be <= configured_max_total_paths")


@dataclass(frozen=True)
class OffloadDecision:
    offload_allowed: bool
    reason_codes: tuple[str, ...]
    recommended_worker_mode: WorkerMode
    recommended_max_total_paths: int


def _validate_nonnegative_int(field: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an int")
    if not isfinite(value) or value < 0:
        raise ValueError(f"{field} must be finite and >= 0")


def _validate_positive_int(field: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} must be an int")
    if not isfinite(value) or value <= 0:
        raise ValueError(f"{field} must be finite and > 0")


def _validate_nonnegative_float(field: str, value: float) -> None:
    if not isfinite(value) or value < 0.0:
        raise ValueError(f"{field} must be finite and >= 0.0")


def _validate_optional_nonnegative_int(field: str, value: int | None) -> None:
    if value is not None:
        _validate_nonnegative_int(field, value)


def _validate_optional_nonnegative_float(field: str, value: float | None) -> None:
    if value is not None:
        _validate_nonnegative_float(field, value)


def _validate_nonempty_string(field: str, value: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if not value.strip():
        raise ValueError(f"{field} must be non-empty")


def _normalized_status(value: str) -> str:
    return value.strip().upper()


def evaluate_offload_readiness(
    inputs: OffloadGateInputs,
    config: OffloadGateConfig,
) -> OffloadDecision:
    reasons: list[str] = []

    if inputs.runtime_phase != RuntimePhase.READY:
        reasons.append("runtime_not_ready")
    if inputs.uptime_seconds < config.warmup_min_seconds:
        reasons.append("warmup_active")
    if inputs.consecutive_healthy_cycles < config.required_healthy_cycles:
        reasons.append("insufficient_healthy_cycles")
    if inputs.price_age_ms > config.max_price_age_ms:
        reasons.append("price_stale")
    if inputs.orderbook_age_ms > config.max_orderbook_age_ms:
        reasons.append("orderbook_stale")
    if inputs.probability_input_age_ms > config.max_probability_input_age_ms:
        reasons.append("probability_inputs_stale")
    if inputs.volatility_age_ms > config.max_volatility_age_ms:
        reasons.append("volatility_stale")
    if inputs.target_status_age_ms > config.max_target_status_age_ms:
        reasons.append("target_stale")
    if not inputs.sigma_tau_valid:
        reasons.append("sigma_invalid")
    if inputs.sigma_tau_age_ms > config.max_sigma_tau_age_ms:
        reasons.append("sigma_stale")
    if not inputs.k_stable:
        reasons.append("k_unstable")
    if _normalized_status(inputs.api_status) != "OK":
        reasons.append("api_unhealthy")
    if _normalized_status(inputs.normalized_health_status) != "OK":
        reasons.append("normalized_health_unhealthy")
    if _normalized_status(inputs.duckdb_status) != "OK":
        reasons.append("duckdb_unhealthy")
    if _normalized_status(inputs.websocket_status) not in {"OK", "CONNECTED"}:
        reasons.append("websocket_unhealthy")
    if inputs.recent_api_blocked:
        reasons.append("api_blocked_recent")
    if inputs.recent_decode_error:
        reasons.append("decode_error_recent")
    if inputs.cpu_percent is not None and inputs.cpu_percent > config.cpu_soft_max_percent:
        reasons.append("cpu_above_soft_max")
    if inputs.memory_mb is not None and inputs.memory_mb > config.memory_soft_max_mb:
        reasons.append("memory_above_soft_max")
    if inputs.queue_length is not None and inputs.queue_length > config.queue_soft_max:
        reasons.append("queue_above_soft_max")

    if reasons:
        return OffloadDecision(
            offload_allowed=False,
            reason_codes=tuple(reasons),
            recommended_worker_mode=_blocked_worker_mode(inputs.runtime_phase, reasons),
            recommended_max_total_paths=0,
        )

    worker_mode, max_total_paths = _allowed_worker_budget(inputs, config)
    return OffloadDecision(
        offload_allowed=True,
        reason_codes=(),
        recommended_worker_mode=worker_mode,
        recommended_max_total_paths=max_total_paths,
    )


def _blocked_worker_mode(
    runtime_phase: RuntimePhase,
    reasons: list[str],
) -> WorkerMode:
    if (
        runtime_phase in _NOWCAST_SAFE_PHASES
        and not _HARD_OR_DATA_INTEGRITY_BLOCKERS.intersection(reasons)
    ):
        return "nowcast_only"
    return "disabled"


def _allowed_worker_budget(
    inputs: OffloadGateInputs,
    config: OffloadGateConfig,
) -> tuple[WorkerMode, int]:
    if inputs.uptime_seconds >= config.normal_after_seconds:
        return "gpu_mc", inputs.configured_max_total_paths
    if inputs.uptime_seconds < 180:
        return (
            "min_mc",
            max(
                inputs.min_total_paths,
                int(inputs.configured_max_total_paths * 0.25),
            ),
        )
    return (
        "min_mc",
        max(
            inputs.min_total_paths,
            int(inputs.configured_max_total_paths * 0.50),
        ),
    )
