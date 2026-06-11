from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from math import inf
from math import nan
from pathlib import Path
from typing import Any

import pytest

from polymarket_engine.ops.recovery_manager import RecoveryConfig
from polymarket_engine.ops.recovery_manager import RecoveryInputs
from polymarket_engine.ops.recovery_manager import RuntimePhase
from polymarket_engine.ops.recovery_manager import evaluate_recovery_state
from polymarket_engine.ops.recovery_manager import write_recovery_status


BASE = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)


def healthy_inputs(**overrides: Any) -> RecoveryInputs:
    values: dict[str, Any] = {
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


def test_write_recovery_status_writes_runtime_schema_atomically(tmp_path: Path) -> None:
    path = tmp_path / "live" / "recovery_status.json"
    state = evaluate_recovery_state(
        healthy_inputs(startup_ts=BASE - timedelta(seconds=10)),
        RecoveryConfig(warmup_min_seconds=60),
    )

    write_recovery_status(path, state, generated_at=BASE)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "boot_id": "boot-1",
        "consecutive_healthy_cycles": 5,
        "generated_at": "2026-06-11T12:00:00+00:00",
        "ready": False,
        "reasons": ["warmup_active"],
        "recovery_attempts": 0,
        "runtime_phase": "WARMING",
        "schema_version": "polymarket-recovery-runtime-v1",
        "uptime_seconds": 10.0,
    }
    assert path.read_text(encoding="utf-8") == json.dumps(
        payload,
        indent=2,
        sort_keys=True,
    ) + "\n"
    assert not path.with_suffix(".json.tmp").exists()


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


def test_hard_blocker_takes_precedence_over_warmup() -> None:
    state = evaluate_recovery_state(
        healthy_inputs(
            k_stable=False,
            startup_ts=BASE - timedelta(seconds=10),
        ),
        RecoveryConfig(warmup_min_seconds=60),
    )

    assert state.runtime_phase == RuntimePhase.BLOCKED
    assert "k_unstable" in state.reasons
    assert "warmup_active" in state.reasons


def test_recovery_attempts_reason_accumulates_with_hard_blocker() -> None:
    state = evaluate_recovery_state(
        healthy_inputs(k_stable=False, recovery_attempts=4),
        RecoveryConfig(max_recovery_attempts=3),
    )

    assert state.runtime_phase == RuntimePhase.BLOCKED
    assert "k_unstable" in state.reasons
    assert "recovery_attempts_exceeded" in state.reasons


def test_warmup_takes_precedence_over_degraded_reasons() -> None:
    state = evaluate_recovery_state(
        healthy_inputs(
            orderbook_fresh=False,
            startup_ts=BASE - timedelta(seconds=10),
        ),
        RecoveryConfig(warmup_min_seconds=60),
    )

    assert state.runtime_phase == RuntimePhase.WARMING
    assert "warmup_active" in state.reasons
    assert "orderbook_stale" in state.reasons


def test_recovery_attempts_exceeded_uses_greater_than_boundary() -> None:
    at_max = evaluate_recovery_state(
        healthy_inputs(recovery_attempts=3),
        RecoveryConfig(max_recovery_attempts=3),
    )
    above_max = evaluate_recovery_state(
        healthy_inputs(recovery_attempts=4),
        RecoveryConfig(max_recovery_attempts=3),
    )

    assert "recovery_attempts_exceeded" not in at_max.reasons
    assert at_max.runtime_phase == RuntimePhase.READY
    assert "recovery_attempts_exceeded" in above_max.reasons
    assert above_max.runtime_phase == RuntimePhase.BLOCKED


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("warmup_min_seconds", -1),
        ("warmup_min_seconds", inf),
        ("warmup_min_seconds", nan),
        ("required_healthy_cycles", -1),
        ("cpu_soft_max_percent", -1.0),
        ("cpu_soft_max_percent", inf),
        ("cpu_soft_max_percent", nan),
        ("memory_soft_max_mb", -1),
        ("queue_soft_max", -1),
        ("max_recovery_attempts", -1),
    ],
)
def test_recovery_config_rejects_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=field):
        kwargs: dict[str, Any] = {field: value}
        RecoveryConfig(**kwargs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("boot_id", ""),
        ("boot_id", "   "),
        ("startup_ts", datetime(2026, 6, 11, 12, 0)),
        ("now", datetime(2026, 6, 11, 12, 0)),
        ("cpu_percent", -1.0),
        ("cpu_percent", inf),
        ("cpu_percent", nan),
        ("memory_mb", -1),
        ("memory_mb", inf),
        ("memory_mb", nan),
        ("queue_length", -1),
        ("queue_length", inf),
        ("queue_length", nan),
        ("consecutive_healthy_cycles", -1),
        ("recovery_attempts", -1),
    ],
)
def test_recovery_inputs_reject_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValueError, match=field):
        healthy_inputs(**{field: value})
