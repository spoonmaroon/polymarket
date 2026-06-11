from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class RuntimePhase(StrEnum):
    BOOTING = "BOOTING"
    WARMING = "WARMING"
    RECOVERING = "RECOVERING"
    DEGRADED = "DEGRADED"
    READY = "READY"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class RecoveryConfig:
    warmup_min_seconds: int = 60
    required_healthy_cycles: int = 3
    cpu_soft_max_percent: float = 20.0
    memory_soft_max_mb: int = 512
    queue_soft_max: int = 100
    max_recovery_attempts: int = 3


@dataclass(frozen=True)
class RecoveryInputs:
    boot_id: str
    startup_ts: datetime
    now: datetime
    status_ok: bool
    normalized_health_ok: bool
    api_ok: bool
    price_fresh: bool
    orderbook_fresh: bool
    probability_inputs_fresh: bool
    volatility_fresh: bool
    target_fresh: bool
    sigma_valid: bool
    k_stable: bool
    duckdb_ok: bool
    cpu_percent: float | None
    memory_mb: int | None
    queue_length: int | None
    recent_api_blocked: bool
    recent_decode_error: bool
    consecutive_healthy_cycles: int
    recovery_attempts: int


@dataclass(frozen=True)
class RecoveryState:
    runtime_phase: RuntimePhase
    ready: bool
    reasons: tuple[str, ...]
    boot_id: str
    uptime_seconds: float
    consecutive_healthy_cycles: int
    recovery_attempts: int


def evaluate_recovery_state(
    inputs: RecoveryInputs,
    config: RecoveryConfig,
) -> RecoveryState:
    uptime = max(0.0, (inputs.now - inputs.startup_ts).total_seconds())
    reasons: list[str] = []

    if uptime < config.warmup_min_seconds:
        reasons.append("warmup_active")
    if inputs.consecutive_healthy_cycles < config.required_healthy_cycles:
        reasons.append("insufficient_healthy_cycles")
    if not inputs.status_ok:
        reasons.append("status_unhealthy")
    if not inputs.normalized_health_ok:
        reasons.append("normalized_health_unhealthy")
    if not inputs.api_ok:
        reasons.append("api_unhealthy")
    if not inputs.price_fresh:
        reasons.append("price_stale")
    if not inputs.orderbook_fresh:
        reasons.append("orderbook_stale")
    if not inputs.probability_inputs_fresh:
        reasons.append("probability_inputs_stale")
    if not inputs.volatility_fresh:
        reasons.append("volatility_stale")
    if not inputs.target_fresh:
        reasons.append("target_stale")
    if not inputs.sigma_valid:
        reasons.append("sigma_invalid")
    if not inputs.k_stable:
        reasons.append("k_unstable")
    if not inputs.duckdb_ok:
        reasons.append("duckdb_unhealthy")
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

    hard_blockers = {"k_unstable", "sigma_invalid", "duckdb_unhealthy"}
    if hard_blockers.intersection(reasons):
        phase = RuntimePhase.BLOCKED
    elif inputs.recovery_attempts > config.max_recovery_attempts:
        phase = RuntimePhase.BLOCKED
        reasons.append("recovery_attempts_exceeded")
    elif "warmup_active" in reasons or "insufficient_healthy_cycles" in reasons:
        phase = RuntimePhase.WARMING
    elif reasons:
        phase = RuntimePhase.DEGRADED
    else:
        phase = RuntimePhase.READY

    return RecoveryState(
        runtime_phase=phase,
        ready=phase == RuntimePhase.READY,
        reasons=tuple(reasons),
        boot_id=inputs.boot_id,
        uptime_seconds=uptime,
        consecutive_healthy_cycles=inputs.consecutive_healthy_cycles,
        recovery_attempts=inputs.recovery_attempts,
    )
