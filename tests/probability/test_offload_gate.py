from __future__ import annotations

import pytest
from typing import Any
from typing import cast

from polymarket_engine.ops.recovery_manager import RuntimePhase
from polymarket_engine.probability.offload_gate import OffloadGateConfig
from polymarket_engine.probability.offload_gate import OffloadGateInputs
from polymarket_engine.probability.offload_gate import evaluate_offload_readiness


def base_inputs(**overrides: object) -> OffloadGateInputs:
    values: dict[str, Any] = {
        "runtime_phase": RuntimePhase.READY,
        "uptime_seconds": 600.0,
        "consecutive_healthy_cycles": 5,
        "price_age_ms": 200,
        "orderbook_age_ms": 200,
        "probability_input_age_ms": 200,
        "volatility_age_ms": 200,
        "target_status_age_ms": 200,
        "sigma_tau_valid": True,
        "sigma_tau_age_ms": 200,
        "k_stable": True,
        "api_status": "OK",
        "normalized_health_status": "OK",
        "duckdb_status": "OK",
        "websocket_status": "OK",
        "cpu_percent": 10.0,
        "memory_mb": 250,
        "queue_length": 0,
        "recent_api_blocked": False,
        "recent_decode_error": False,
        "configured_max_total_paths": 80_000,
        "min_total_paths": 4_000,
    }
    values.update(overrides)
    return OffloadGateInputs(**values)


def test_offload_allowed_when_ready_and_fresh() -> None:
    decision = evaluate_offload_readiness(base_inputs(), OffloadGateConfig())
    assert decision.offload_allowed is True
    assert decision.reason_codes == ()
    assert decision.recommended_worker_mode == "gpu_mc"
    assert decision.recommended_max_total_paths == 80_000


def test_offload_allows_connected_websocket_status() -> None:
    decision = evaluate_offload_readiness(
        base_inputs(websocket_status="CONNECTED"),
        OffloadGateConfig(),
    )
    assert decision.offload_allowed is True
    assert "websocket_unhealthy" not in decision.reason_codes


def test_offload_blocked_during_warming() -> None:
    decision = evaluate_offload_readiness(
        base_inputs(runtime_phase=RuntimePhase.WARMING),
        OffloadGateConfig(),
    )
    assert decision.offload_allowed is False
    assert "runtime_not_ready" in decision.reason_codes
    assert decision.recommended_worker_mode == "nowcast_only"
    assert decision.recommended_max_total_paths == 0


def test_offload_blocked_phase_recommends_disabled() -> None:
    decision = evaluate_offload_readiness(
        base_inputs(runtime_phase=RuntimePhase.BLOCKED),
        OffloadGateConfig(),
    )
    assert decision.offload_allowed is False
    assert "runtime_not_ready" in decision.reason_codes
    assert decision.recommended_worker_mode == "disabled"


def test_offload_degraded_phase_recommends_disabled() -> None:
    decision = evaluate_offload_readiness(
        base_inputs(runtime_phase=RuntimePhase.DEGRADED),
        OffloadGateConfig(),
    )
    assert decision.offload_allowed is False
    assert "runtime_not_ready" in decision.reason_codes
    assert decision.recommended_worker_mode == "disabled"


def test_offload_warming_with_sigma_invalid_recommends_disabled() -> None:
    decision = evaluate_offload_readiness(
        base_inputs(runtime_phase=RuntimePhase.WARMING, sigma_tau_valid=False),
        OffloadGateConfig(),
    )
    assert decision.offload_allowed is False
    assert "runtime_not_ready" in decision.reason_codes
    assert "sigma_invalid" in decision.reason_codes
    assert decision.recommended_worker_mode == "disabled"


def test_offload_warming_with_api_unhealthy_recommends_disabled() -> None:
    decision = evaluate_offload_readiness(
        base_inputs(runtime_phase=RuntimePhase.WARMING, api_status="ERROR"),
        OffloadGateConfig(),
    )
    assert decision.offload_allowed is False
    assert "api_unhealthy" in decision.reason_codes
    assert decision.recommended_worker_mode == "disabled"


def test_offload_warming_with_normalized_health_unhealthy_recommends_disabled() -> None:
    decision = evaluate_offload_readiness(
        base_inputs(
            runtime_phase=RuntimePhase.WARMING,
            normalized_health_status="ERROR",
        ),
        OffloadGateConfig(),
    )
    assert decision.offload_allowed is False
    assert "normalized_health_unhealthy" in decision.reason_codes
    assert decision.recommended_worker_mode == "disabled"


def test_offload_warming_with_websocket_unhealthy_recommends_disabled() -> None:
    decision = evaluate_offload_readiness(
        base_inputs(
            runtime_phase=RuntimePhase.WARMING,
            websocket_status="DISCONNECTED",
        ),
        OffloadGateConfig(),
    )
    assert decision.offload_allowed is False
    assert "websocket_unhealthy" in decision.reason_codes
    assert decision.recommended_worker_mode == "disabled"


def test_offload_blocked_when_sigma_invalid() -> None:
    decision = evaluate_offload_readiness(
        base_inputs(sigma_tau_valid=False),
        OffloadGateConfig(),
    )
    assert decision.offload_allowed is False
    assert "sigma_invalid" in decision.reason_codes


def test_offload_blocks_recent_decode_error() -> None:
    decision = evaluate_offload_readiness(
        base_inputs(recent_decode_error=True),
        OffloadGateConfig(),
    )
    assert decision.offload_allowed is False
    assert "decode_error_recent" in decision.reason_codes


def test_status_checks_are_case_insensitive() -> None:
    decision = evaluate_offload_readiness(
        base_inputs(
            api_status="ok",
            normalized_health_status="ok",
            duckdb_status="ok",
            websocket_status="connected",
        ),
        OffloadGateConfig(),
    )
    assert decision.offload_allowed is True
    assert decision.reason_codes == ()


def test_non_string_status_raises_value_error() -> None:
    with pytest.raises(ValueError, match="api_status"):
        base_inputs(api_status=cast(Any, 123))


def test_int_fields_reject_bool_values() -> None:
    with pytest.raises(ValueError, match="price_age_ms"):
        base_inputs(price_age_ms=cast(Any, True))


def test_zero_freshness_threshold_is_valid_and_strict() -> None:
    config = OffloadGateConfig(max_price_age_ms=0)
    decision = evaluate_offload_readiness(base_inputs(price_age_ms=1), config)
    assert decision.offload_allowed is False
    assert "price_stale" in decision.reason_codes


def test_path_budget_ramps_after_startup() -> None:
    config = OffloadGateConfig(warmup_min_seconds=30, normal_after_seconds=300)
    early = evaluate_offload_readiness(
        base_inputs(uptime_seconds=120, configured_max_total_paths=80_000),
        config,
    )
    assert early.offload_allowed is True
    assert early.recommended_worker_mode == "min_mc"
    assert early.recommended_max_total_paths == 20_000
