from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from polymarket_engine.probability.grid_cache import ProbabilityGridEntry
from polymarket_engine.probability.grid_cache import ProbabilityGridHit
from polymarket_engine.probability.grid_cache import grid_entry_from_probability_input
from polymarket_engine.probability.grid_cache import grid_runtime_row
from polymarket_engine.probability.grid_cache import lookup_probability_grid_entry
from polymarket_engine.probability.grid_cache import probability_grid_key
from polymarket_engine.probability.grid_cache import upsert_probability_grid_entry
from polymarket_engine.probability.schema import ProbabilityInput
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def _probability_input(
    *,
    state_id: str = "state-btc-up",
    asof_ts: datetime = datetime(2026, 6, 5, 14, 3, 20, tzinfo=timezone.utc),
    asset: str = "BTC",
    side: str = "UP",
    seconds_left: float = 98.0,
    sigma_tau: float = 0.0118,
    z_path: float = 0.82,
) -> ProbabilityInput:
    return ProbabilityInput(
        state_id=state_id,
        asof_ts=asof_ts,
        asset=asset,
        side=side,
        comparison_operator=">=" if side == "UP" else "<=",
        seconds_left=seconds_left,
        settlement_price=104_000.0,
        threshold=103_950.0,
        sigma_tau=sigma_tau,
        executable_price=0.64,
        source_age_ms=300,
        book_age_ms=420,
        z_path=z_path,
    )


def _entry(
    probability_input: ProbabilityInput,
    *,
    diagnostics: dict[str, object] | None = None,
) -> ProbabilityGridEntry:
    return grid_entry_from_probability_input(
        probability_input,
        market_slug="btc-updown-5m-1780668000",
        start_ts=datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc),
        expiry_ts=datetime(2026, 6, 5, 14, 5, tzinfo=timezone.utc),
        p_finish=0.674,
        p_no_touch=0.718,
        u_gen=0.046,
        path_count=10_000,
        seed=20260605,
        volatility_regime="normal",
        event_flag="none",
        source_risk_flag="normal",
        generator_version="offline-lognormal-chainlink-sigma-v1",
        model_version="cached-grid-v1",
        training_cutoff_ts=datetime(2026, 6, 5, 13, 55, tzinfo=timezone.utc),
        max_event_ts=probability_input.asof_ts - timedelta(milliseconds=50),
        max_observed_ts=probability_input.asof_ts - timedelta(milliseconds=20),
        generated_at=datetime(2026, 6, 5, 14, 3, tzinfo=timezone.utc),
        valid_from=datetime(2026, 6, 5, 14, 3, tzinfo=timezone.utc),
        valid_until=datetime(2026, 6, 5, 14, 3, 30, tzinfo=timezone.utc),
        diagnostics=diagnostics or {"ensemble": {"path_diagnosis": ["NEAR_THRESHOLD"]}},
    )


def test_probability_grid_key_buckets_time_asset_side_and_generator_version() -> None:
    probability_input = _probability_input()
    start_ts = datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc)
    expiry_ts = datetime(2026, 6, 5, 14, 5, tzinfo=timezone.utc)

    key = probability_grid_key(
        probability_input,
        market_slug="btc-updown-5m-1780668000",
        start_ts=start_ts,
        expiry_ts=expiry_ts,
        volatility_regime="normal",
        event_flag="none",
        source_risk_flag="normal",
        generator_version="offline-lognormal-chainlink-sigma-v1",
    )

    assert key.asset == "BTC"
    assert key.side == "UP"
    assert key.market_slug == "btc-updown-5m-1780668000"
    assert key.start_ts == start_ts
    assert key.expiry_ts == expiry_ts
    assert key.horizon_seconds == 300
    assert key.seconds_left_bucket == "90-120"
    assert key.z_path_bucket == "0.75-1.00"
    assert key.sigma_bucket == "0.010-0.015"
    assert key.volatility_regime == "normal"
    assert key.cache_key == (
        "BTC|UP|marketbtc-updown-5m-1780668000|start1780668000|expiry1780668300|"
        "h300|t90-120|z0.75-1.00|sigma0.010-0.015|volnormal|"
        "eventnone|risknormal|genoffline-lognormal-chainlink-sigma-v1"
    )


def test_probability_grid_entry_round_trips_and_returns_runtime_metadata(tmp_path: Path) -> None:
    db_path = tmp_path / "grid.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    probability_input = _probability_input()
    entry = _entry(probability_input)

    upsert_probability_grid_entry(store, entry)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        hit = lookup_probability_grid_entry(
            conn,
            probability_input,
            market_slug="btc-updown-5m-1780668000",
            start_ts=datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc),
            expiry_ts=datetime(2026, 6, 5, 14, 5, tzinfo=timezone.utc),
            volatility_regime="normal",
            asof_ts=probability_input.asof_ts,
            runtime_ts=datetime(2026, 6, 5, 14, 3, 21, tzinfo=timezone.utc),
        )

    assert hit is not None
    assert hit.entry == entry
    assert hit.cache_status == "HIT"

    runtime_row = grid_runtime_row(
        probability_input=probability_input,
        contract="BTC 5m UP",
        contract_id="btc-5m-up",
        market_slug="btc-updown-5m-1780668000",
        start_ts=datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc),
        expiry_ts=datetime(2026, 6, 5, 14, 5, tzinfo=timezone.utc),
        hit=hit,
        now=datetime(2026, 6, 5, 14, 3, 21, tzinfo=timezone.utc),
    )

    assert runtime_row["market_slug"] == "btc-updown-5m-1780668000"
    assert runtime_row["start_ts"] == "2026-06-05T14:00:00+00:00"
    assert runtime_row["expiry_ts"] == "2026-06-05T14:05:00+00:00"
    assert runtime_row["asof_ts"] == "2026-06-05T14:03:20+00:00"
    assert runtime_row["cache_key"] == entry.cache_key
    assert runtime_row["cache_status"] == "HIT"
    assert runtime_row["cache_market_slug"] == "btc-updown-5m-1780668000"
    assert runtime_row["cache_start_ts"] == "2026-06-05T14:00:00+00:00"
    assert runtime_row["cache_expiry_ts"] == "2026-06-05T14:05:00+00:00"
    assert runtime_row["cache_asof_ts"] == "2026-06-05T14:03:20+00:00"
    assert runtime_row["generated_at"] == "2026-06-05T14:03:00+00:00"
    assert runtime_row["valid_from"] == "2026-06-05T14:03:00+00:00"
    assert runtime_row["valid_until"] == "2026-06-05T14:03:30+00:00"
    assert runtime_row["grid_cache"] == {
        "cache_key": entry.cache_key,
        "cache_status": "HIT",
        "market_slug": "btc-updown-5m-1780668000",
        "start_ts": "2026-06-05T14:00:00+00:00",
        "expiry_ts": "2026-06-05T14:05:00+00:00",
        "asof_ts": "2026-06-05T14:03:20+00:00",
        "generated_at": "2026-06-05T14:03:00+00:00",
        "valid_from": "2026-06-05T14:03:00+00:00",
        "valid_until": "2026-06-05T14:03:30+00:00",
        "time_bucket": "90-120",
        "z_path_bucket": "0.75-1.00",
        "sigma_bucket": "0.010-0.015",
        "volatility_regime": "normal",
        "path_count": 10_000,
    }
    assert runtime_row["time_bucket"] == "90-120"
    assert runtime_row["z_path_bucket"] == "0.75-1.00"
    assert runtime_row["sigma_bucket"] == "0.010-0.015"
    assert runtime_row["volatility_regime"] == "normal"
    assert runtime_row["path_count"] == 10_000
    assert runtime_row["p_hat"] == pytest.approx(runtime_row["p_finish"])
    assert runtime_row["age_ms"] == 1000


def test_grid_runtime_row_extracts_confidence_and_sensitivity_diagnostics() -> None:
    probability_input = _probability_input()
    sensitivity = [
        {
            "dimension": "prior_price_quantile",
            "time_fraction": 0.5,
            "quantile_low": 0.5,
            "quantile_high": 0.75,
            "sample_count": 200,
            "price_quantile": 70_100.0,
            "log_return_quantile": 0.001,
            "p_hat": 0.674,
            "source_seed_count": 3,
        }
    ]
    entry = _entry(
        probability_input,
        diagnostics={
            "p_hat": 0.674,
            "p_hat_std": 0.012,
            "p_hat_ci_low": 0.650,
            "p_hat_ci_high": 0.698,
            "paths_per_seed": 10_000,
            "seed_count": 3,
            "prior_sensitivity": sensitivity,
        },
    )

    runtime_row = grid_runtime_row(
        probability_input=probability_input,
        contract="BTC 5m UP",
        contract_id="btc-5m-up",
        market_slug="btc-updown-5m-1780668000",
        start_ts=datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc),
        expiry_ts=datetime(2026, 6, 5, 14, 5, tzinfo=timezone.utc),
        hit=ProbabilityGridHit(entry=entry),
        now=datetime(2026, 6, 5, 14, 3, 21, tzinfo=timezone.utc),
    )

    assert runtime_row["p_hat"] == pytest.approx(0.674)
    assert runtime_row["p_hat_std"] == pytest.approx(0.012)
    assert runtime_row["p_hat_ci_low"] == pytest.approx(0.650)
    assert runtime_row["p_hat_ci_high"] == pytest.approx(0.698)
    assert runtime_row["paths_per_seed"] == 10_000
    assert runtime_row["seed_count"] == 3
    assert runtime_row["prior_sensitivity"] == sensitivity


def test_grid_runtime_row_rejects_bool_probability_diagnostics() -> None:
    probability_input = _probability_input()
    entry = _entry(probability_input, diagnostics={"p_hat": False})

    with pytest.raises(ValueError, match="optional float must be finite"):
        grid_runtime_row(
            probability_input=probability_input,
            contract="BTC 5m UP",
            contract_id="btc-5m-up",
            market_slug="btc-updown-5m-1780668000",
            start_ts=datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc),
            expiry_ts=datetime(2026, 6, 5, 14, 5, tzinfo=timezone.utc),
            hit=ProbabilityGridHit(entry=entry),
            now=datetime(2026, 6, 5, 14, 3, 21, tzinfo=timezone.utc),
        )


def test_probability_grid_lookup_rejects_wall_clock_stale_or_future_leaking_entries(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "grid.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    probability_input = _probability_input()
    stale = _entry(probability_input)

    upsert_probability_grid_entry(store, stale)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        hit = lookup_probability_grid_entry(
            conn,
            probability_input,
            market_slug="btc-updown-5m-1780668000",
            start_ts=datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc),
            expiry_ts=datetime(2026, 6, 5, 14, 5, tzinfo=timezone.utc),
            volatility_regime="normal",
            asof_ts=probability_input.asof_ts,
            runtime_ts=stale.valid_until + timedelta(milliseconds=1),
        )

    assert hit is None

    future_observed = replace(
        _entry(probability_input),
        max_observed_ts=probability_input.asof_ts + timedelta(milliseconds=1),
    )
    upsert_probability_grid_entry(store, future_observed)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        hit = lookup_probability_grid_entry(
            conn,
            probability_input,
            market_slug="btc-updown-5m-1780668000",
            start_ts=datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc),
            expiry_ts=datetime(2026, 6, 5, 14, 5, tzinfo=timezone.utc),
            volatility_regime="normal",
            asof_ts=probability_input.asof_ts,
            runtime_ts=future_observed.valid_from + timedelta(seconds=1),
        )

    assert hit is None


def test_probability_grid_lookup_rejects_future_generated_or_wrong_market_window(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "grid.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    probability_input = _probability_input()
    future_generated = replace(
        _entry(probability_input),
        generated_at=datetime(2026, 6, 5, 14, 4, tzinfo=timezone.utc),
        valid_from=datetime(2026, 6, 5, 14, 3, tzinfo=timezone.utc),
        valid_until=datetime(2026, 6, 5, 14, 5, tzinfo=timezone.utc),
    )
    upsert_probability_grid_entry(store, future_generated)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        hit = lookup_probability_grid_entry(
            conn,
            probability_input,
            market_slug="btc-updown-5m-1780668000",
            start_ts=datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc),
            expiry_ts=datetime(2026, 6, 5, 14, 5, tzinfo=timezone.utc),
            volatility_regime="normal",
            asof_ts=probability_input.asof_ts,
            runtime_ts=datetime(2026, 6, 5, 14, 3, 21, tzinfo=timezone.utc),
        )
        wrong_market = lookup_probability_grid_entry(
            conn,
            probability_input,
            market_slug="btc-updown-5m-1780668300",
            start_ts=datetime(2026, 6, 5, 14, 5, tzinfo=timezone.utc),
            expiry_ts=datetime(2026, 6, 5, 14, 10, tzinfo=timezone.utc),
            volatility_regime="normal",
            asof_ts=probability_input.asof_ts,
            runtime_ts=datetime(2026, 6, 5, 14, 3, 21, tzinfo=timezone.utc),
        )

    assert hit is None
    assert wrong_market is None


def test_probability_grid_lookup_returns_none_for_missing_bucket(tmp_path: Path) -> None:
    db_path = tmp_path / "grid.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    upsert_probability_grid_entry(store, _entry(_probability_input(asset="BTC")))

    with duckdb.connect(str(db_path), read_only=True) as conn:
        hit = lookup_probability_grid_entry(
            conn,
            _probability_input(asset="ETH", state_id="state-eth-up"),
            market_slug="eth-updown-5m-1780668000",
            start_ts=datetime(2026, 6, 5, 14, 0, tzinfo=timezone.utc),
            expiry_ts=datetime(2026, 6, 5, 14, 5, tzinfo=timezone.utc),
            volatility_regime="normal",
            asof_ts=datetime(2026, 6, 5, 14, 3, 20, tzinfo=timezone.utc),
            runtime_ts=datetime(2026, 6, 5, 14, 3, 21, tzinfo=timezone.utc),
        )

    assert hit is None
