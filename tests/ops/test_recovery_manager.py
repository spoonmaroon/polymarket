from __future__ import annotations

from datetime import UTC, datetime, timedelta

from polymarket_engine.ops.recovery_manager import RecoveryConfig
from polymarket_engine.ops.recovery_manager import RecoveryInputs
from polymarket_engine.ops.recovery_manager import RuntimePhase
from polymarket_engine.ops.recovery_manager import evaluate_recovery_state


BASE = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)


def healthy_inputs(**overrides: object) -> RecoveryInputs:
    values = {
        "boot_id": "boot-1",
        "startup_ts": BASE - timedelta(minutes=10),
        "now": BASE,
        "status_ok": True,
        "normalized_health_ok": True,
        "api_ok": True,
        "price_fresh": True,
        "orderbook_fresh": True,
        "probability_inputs_fresh": True,
        "volatility_fresh": True,
        "target_fresh": True,
        "sigma_valid": True,
        "k_stable": True,
        "duckdb_ok": True,
        "cpu_percent": 12.0,
        "memory_mb": 300,
        "queue_length": 0,
        "recent_api_blocked": False,
        "recent_decode_error": False,
        "consecutive_healthy_cycles": 5,
        "recovery_attempts": 0,
    }
    values.update(overrides)
    return RecoveryInputs(**values)


def test_recovery_state_ready_when_all_gates_pass() -> None:
    state = evaluate_recovery_state(healthy_inputs(), RecoveryConfig())

    assert state.runtime_phase == RuntimePhase.READY
    assert state.ready is True
    assert state.reasons == ()


def test_recovery_state_warming_during_startup_warmup() -> None:
    state = evaluate_recovery_state(
        healthy_inputs(startup_ts=BASE - timedelta(seconds=10)),
        RecoveryConfig(warmup_min_seconds=60),
    )

    assert state.runtime_phase == RuntimePhase.WARMING
    assert "warmup_active" in state.reasons


def test_recovery_state_blocked_for_k_mutation() -> None:
    state = evaluate_recovery_state(
        healthy_inputs(k_stable=False),
        RecoveryConfig(),
    )

    assert state.runtime_phase == RuntimePhase.BLOCKED
    assert "k_unstable" in state.reasons


def test_recovery_state_degraded_for_stale_orderbook() -> None:
    state = evaluate_recovery_state(
        healthy_inputs(orderbook_fresh=False),
        RecoveryConfig(),
    )

    assert state.runtime_phase == RuntimePhase.DEGRADED
    assert "orderbook_stale" in state.reasons
