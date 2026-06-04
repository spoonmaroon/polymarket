from __future__ import annotations

import json
import os
import time
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import duckdb
import pytest

from polymarket_engine.ingestion import rust_normalizer_sidecar
from polymarket_engine.ingestion.rust_event_normalizer import RustEventNormalizeResult
from polymarket_engine.ingestion.rust_normalizer_sidecar import (
    _cadence_sleep_seconds,
    run_rust_normalizer_cycle,
    run_rust_normalizer_loop,
)
from polymarket_engine.domain.market_state import PriceObservation
from polymarket_engine.storage import duckdb_store
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def test_sidecar_cycle_normalizes_builds_states_and_writes_health(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    asof_ts = datetime.now(timezone.utc).replace(microsecond=0)
    start_ts = asof_ts - timedelta(minutes=2)
    _write_probability_ready_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)

    result = run_rust_normalizer_cycle(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        include_next=False,
        compute_probabilities=True,
    )

    assert result.files == 2
    assert result.rows_read == 7
    assert result.bytes_read > 0
    assert result.price_ticks_written == 5
    assert result.orderbooks_written == 2
    assert result.contracts_upserted == 2
    assert result.states_written == 2
    assert result.elapsed_ms >= 0
    assert result.normalize_ms >= 0
    assert result.state_ms >= 0
    assert result.health_ms >= 0
    assert health_path.exists()
    health_payload = json.loads(health_path.read_text(encoding="utf-8"))
    assert health_payload["schema_version"] == "polymarket-normalized-health-v1"
    probability_path = health_path.with_name("probabilities.json")
    probability_payload = json.loads(probability_path.read_text(encoding="utf-8"))
    assert probability_payload["schema_version"] == "polymarket-probability-runtime-v1"
    assert len(probability_payload["rows"]) == 2
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.price_ticks").fetchone() == (5,)
        assert conn.execute("select count(*) from core.orderbook_snapshots").fetchone() == (2,)
        assert conn.execute("select count(*) from features.probability_outputs").fetchone() == (2,)
        assert conn.execute("select count(*) from features.asof_state_inputs").fetchone() == (2,)


def test_sidecar_cycle_skips_probability_outputs_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    asof_ts = datetime.now(timezone.utc).replace(microsecond=0)
    start_ts = asof_ts - timedelta(minutes=2)
    _write_probability_ready_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)

    def fail_compute(*_: object, **__: object) -> int:
        raise AssertionError("normalizer should not compute probabilities by default")

    monkeypatch.setattr(
        rust_normalizer_sidecar,
        "_compute_probability_outputs",
        fail_compute,
    )

    result = run_rust_normalizer_cycle(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        include_next=False,
    )

    assert result.states_written == 2
    assert result.probability_outputs_written == 0


def test_sidecar_cycle_writes_health_when_status_is_missing(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)

    result = run_rust_normalizer_cycle(
        raw_root=raw_root,
        db_path=db_path,
        status_path=tmp_path / "live" / "missing-status.json",
        normalized_health_path=health_path,
        include_next=False,
    )

    assert result.rows_read == 4
    assert result.contracts_upserted == 0
    assert result.states_written == 0
    assert result.unavailable == ()
    assert health_path.exists()


def test_normalizer_writes_market_outcome_history(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=5, seconds=1)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)

    result = run_rust_normalizer_cycle(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        include_next=False,
    )

    assert result.market_outcomes_written == 1
    outcome_path = health_path.with_name("outcomes.json")
    outcome_payload = json.loads(outcome_path.read_text(encoding="utf-8"))
    assert outcome_payload["schema_version"] == "polymarket-outcome-runtime-v1"
    assert outcome_payload["rows"][0]["market"] == "BTC 5m"
    assert outcome_payload["rows"][0]["computed_winner"] is None
    assert outcome_payload["rows"][0]["official_winner"] is None
    assert outcome_payload["rows"][0]["official_resolution_status"] == "pending"
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute(
            "select computed_winner, official_winner, official_resolution_status "
            "from validation.market_outcome_history"
        ).fetchone() == (None, None, "pending")


def test_upsert_market_outcomes_limits_official_refresh_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_limits: list[int | None] = []

    def fake_upsert_official_market_outcomes(**kwargs: Any) -> int:
        captured_limits.append(kwargs["max_markets"])
        return 0

    monkeypatch.setenv("POLYMARKET_OFFICIAL_OUTCOME_REFRESH_LIMIT", "2")
    monkeypatch.setattr(
        rust_normalizer_sidecar,
        "upsert_official_market_outcomes",
        fake_upsert_official_market_outcomes,
    )
    monkeypatch.setattr(
        rust_normalizer_sidecar,
        "latest_market_outcome_rows_from_connection",
        lambda *, conn, limit: [],
    )
    monkeypatch.setattr(
        rust_normalizer_sidecar,
        "write_outcome_history_status",
        lambda *, out_path, rows: None,
    )

    rust_normalizer_sidecar._upsert_market_outcomes(
        store=cast(DuckDbIngestStore, _FakeConnectionStore()),
        out_path=tmp_path / "outcomes.json",
    )

    assert captured_limits == [2]


def test_upsert_market_outcomes_uses_pending_sweep_limit_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_limits: list[int | None] = []

    def fake_upsert_official_market_outcomes(**kwargs: Any) -> int:
        captured_limits.append(kwargs["pending_sweep_limit"])
        return 0

    monkeypatch.setenv("POLYMARKET_OFFICIAL_OUTCOME_PENDING_SWEEP_LIMIT", "7")
    monkeypatch.setattr(
        rust_normalizer_sidecar,
        "upsert_official_market_outcomes",
        fake_upsert_official_market_outcomes,
    )
    monkeypatch.setattr(
        rust_normalizer_sidecar,
        "latest_market_outcome_rows_from_connection",
        lambda *, conn, limit: [],
    )
    monkeypatch.setattr(
        rust_normalizer_sidecar,
        "write_outcome_history_status",
        lambda *, out_path, rows: None,
    )

    rust_normalizer_sidecar._upsert_market_outcomes(
        store=cast(DuckDbIngestStore, _FakeConnectionStore()),
        out_path=tmp_path / "outcomes.json",
    )

    assert captured_limits == [7]


def test_upsert_market_outcomes_uses_output_limit_from_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_limits: list[int] = []

    def fake_latest_market_outcome_rows_from_connection(**kwargs: Any) -> list[dict[str, Any]]:
        captured_limits.append(kwargs["limit"])
        return []

    monkeypatch.setenv("POLYMARKET_OUTCOME_OUTPUT_LIMIT", "1440")
    monkeypatch.setattr(
        rust_normalizer_sidecar,
        "upsert_official_market_outcomes",
        lambda **_kwargs: 0,
    )
    monkeypatch.setattr(
        rust_normalizer_sidecar,
        "latest_market_outcome_rows_from_connection",
        fake_latest_market_outcome_rows_from_connection,
    )
    monkeypatch.setattr(
        rust_normalizer_sidecar,
        "write_outcome_history_status",
        lambda *, out_path, rows: None,
    )

    rust_normalizer_sidecar._upsert_market_outcomes(
        store=cast(DuckDbIngestStore, _FakeConnectionStore()),
        out_path=tmp_path / "outcomes.json",
    )

    assert captured_limits == [1440]


def test_write_target_cache_status_uses_asof_chainlink_start_reference(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    target_path = tmp_path / "live" / "targets.json"
    start_ts = datetime(2026, 6, 4, 20, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    store.insert_price_ticks(
        (
            PriceObservation(
                source_key="polymarket_rtds_chainlink",
                symbol="BTC/USD",
                event_ts=start_ts,
                observed_ts=start_ts + timedelta(seconds=3),
                price=63_500.12,
            ),
            PriceObservation(
                source_key="polymarket_rtds_chainlink",
                symbol="BTC/USD",
                event_ts=start_ts + timedelta(seconds=1),
                observed_ts=start_ts + timedelta(seconds=4),
                price=63_510.0,
            ),
        )
    )
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)

    rust_normalizer_sidecar._write_target_cache_status(
        store=store,
        status_path=status_path,
        out_path=target_path,
        asof_ts=asof_ts,
    )

    payload = json.loads(target_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "polymarket-target-cache-v1"
    assert payload["rows"] == [
        {
            "asset": "BTC",
            "interval": "5m",
            "market_slug": "btc-updown-5m-1780603200",
            "start_ts": "2026-06-04T20:00:00+00:00",
            "expiry_ts": "2026-06-04T20:05:00+00:00",
            "threshold_price": 63_500.12,
            "threshold_event_ts": "2026-06-04T20:00:00+00:00",
            "threshold_observed_ts": "2026-06-04T20:00:03+00:00",
        }
    ]


def test_sidecar_loop_writes_target_cache_for_active_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    monkeypatch.setattr(
        rust_normalizer_sidecar,
        "_upsert_market_outcomes",
        lambda *, store, out_path: 0,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        lambda _: None,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=1,
    )

    payload = json.loads(health_path.with_name("targets.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "polymarket-target-cache-v1"
    assert [
        {
            "market_slug": row["market_slug"],
            "threshold_price": row["threshold_price"],
            "threshold_event_ts": row["threshold_event_ts"],
            "threshold_observed_ts": row["threshold_observed_ts"],
        }
        for row in payload["rows"]
    ] == [
        {
            "market_slug": "btc-updown-5m-1780380000",
            "threshold_price": 70_000.0,
            "threshold_event_ts": "2026-06-02T06:00:00+00:00",
            "threshold_observed_ts": "2026-06-02T06:00:00+00:00",
        }
    ]


def test_cadence_sleep_subtracts_cycle_elapsed_time() -> None:
    assert _cadence_sleep_seconds(
        cycle_started=10.0,
        interval_seconds=0.25,
        now=10.04,
    ) == pytest.approx(0.21)
    assert _cadence_sleep_seconds(
        cycle_started=10.0,
        interval_seconds=0.25,
        now=10.40,
    ) == 0.0


def test_sidecar_loop_reuses_process_and_sleeps_between_cycles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    sleeps: list[float] = []
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        sleeps.append,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=1.25,
        include_next=False,
        max_cycles=2,
    )

    lines = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("normalizer_cycle ")
    ]
    assert len(lines) == 2
    assert _log_values(lines[0])["rows_read"] == "4"
    assert _log_values(lines[1])["rows_read"] == "0"
    assert len(sleeps) == 1
    assert 0.0 < sleeps[0] <= 1.25


def test_sidecar_loop_throttles_market_outcome_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    outcome_refreshes: list[Path] = []

    def fake_upsert_market_outcomes(*, store: DuckDbIngestStore, out_path: Path) -> int:
        outcome_refreshes.append(out_path)
        return 0

    monkeypatch.setattr(
        rust_normalizer_sidecar,
        "_upsert_market_outcomes",
        fake_upsert_market_outcomes,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        lambda _: None,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=3,
    )

    assert outcome_refreshes == [health_path.with_name("outcomes.json")]


def test_sidecar_loop_retries_pending_outcomes_every_five_seconds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    outcome_refreshes: list[Path] = []
    monotonic_values = iter((0.0, 0.0, 4.9, 4.9, 5.0))

    def fake_upsert_market_outcomes(*, store: DuckDbIngestStore, out_path: Path) -> int:
        outcome_refreshes.append(out_path)
        return 0

    monkeypatch.setattr(
        rust_normalizer_sidecar,
        "_upsert_market_outcomes",
        fake_upsert_market_outcomes,
    )
    monkeypatch.setattr(
        rust_normalizer_sidecar,
        "_has_expired_pending_official_outcomes",
        lambda *, store: True,
        raising=False,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        lambda _: None,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.monotonic",
        lambda: next(monotonic_values),
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=3,
    )

    assert outcome_refreshes == [
        health_path.with_name("outcomes.json"),
        health_path.with_name("outcomes.json"),
    ]


def test_sidecar_loop_initial_cycle_normalizes_only_active_raw_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    old_ts = datetime(2026, 6, 2, 4, 0, tzinfo=timezone.utc)
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    old_path = (
        raw_root
        / "polymarket_rtds_chainlink"
        / "price_update"
        / "date=2026-06-02"
        / "hour=04"
        / "events.jsonl"
    )
    active_path = (
        raw_root
        / "polymarket_rtds_chainlink"
        / "price_update"
        / "date=2026-06-02"
        / "hour=06"
        / "events.jsonl"
    )
    _write_jsonl(old_path, _chainlink_row("ETH/USD", old_ts, old_ts, 3_700.0))
    _write_jsonl(active_path, _chainlink_row("BTC/USD", asof_ts, asof_ts, 70_125.0))
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    full_signature = (
        rust_normalizer_sidecar._raw_file_signature(old_path),
        rust_normalizer_sidecar._raw_file_signature(active_path),
    )
    active_signature = (full_signature[1],)
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar._raw_tree_signature",
        lambda **_: full_signature,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar._active_raw_tree_signature",
        lambda **_: active_signature,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        lambda _: None,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=1,
    )

    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute(
            "select symbol from core.price_ticks order by symbol"
        ).fetchall() == [("BTC/USD",)]


def test_sidecar_loop_skips_state_build_when_raw_and_status_are_idle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        lambda _: None,
    )
    real_build = getattr(
        rust_normalizer_sidecar,
        "build_current_decision_state_snapshots",
    )
    build_calls = 0

    def counting_build(*args: Any, **kwargs: Any) -> Any:
        nonlocal build_calls
        build_calls += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar."
        "build_current_decision_state_snapshots",
        counting_build,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert build_calls == 1


def test_sidecar_loop_skips_state_build_for_cross_cycle_duplicate_raw_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_current_hour_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    changed_path = (
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / f"date={asof_ts.date().isoformat()}"
        / f"hour={asof_ts.hour:02d}"
        / "events.jsonl"
    )
    real_build = getattr(
        rust_normalizer_sidecar,
        "build_current_decision_state_snapshots",
    )
    build_calls = 0

    def counting_build(*args: Any, **kwargs: Any) -> Any:
        nonlocal build_calls
        build_calls += 1
        return real_build(*args, **kwargs)

    def append_duplicate_raw_row(_: float) -> None:
        with changed_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    _orderbook_row(
                        "up-token",
                        asof_ts - timedelta(seconds=2),
                        asof_ts - timedelta(seconds=1),
                        0.61,
                        0.64,
                    ),
                    separators=(",", ":"),
                )
                + "\n"
            )

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar."
        "build_current_decision_state_snapshots",
        counting_build,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        append_duplicate_raw_row,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    lines = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("normalizer_cycle ")
    ]
    assert len(lines) == 2
    assert _log_values(lines[1])["rows_read"] == "1"
    assert _log_values(lines[1])["state_skipped"] == "true"
    assert build_calls == 1


def test_sidecar_loop_skips_state_build_for_raw_append_when_status_is_idle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_current_hour_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    changed_path = (
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / f"date={asof_ts.date().isoformat()}"
        / f"hour={asof_ts.hour:02d}"
        / "events.jsonl"
    )
    real_build = getattr(
        rust_normalizer_sidecar,
        "build_current_decision_state_snapshots",
    )
    build_calls = 0

    def counting_build(*args: Any, **kwargs: Any) -> Any:
        nonlocal build_calls
        build_calls += 1
        return real_build(*args, **kwargs)

    def append_new_raw_row(_: float) -> None:
        with changed_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    _orderbook_row(
                        "up-token",
                        asof_ts + timedelta(seconds=1),
                        asof_ts + timedelta(seconds=1),
                        0.63,
                        0.66,
                    ),
                    separators=(",", ":"),
                )
                + "\n"
            )

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar."
        "build_current_decision_state_snapshots",
        counting_build,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        append_new_raw_row,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    lines = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("normalizer_cycle ")
    ]
    assert len(lines) == 2
    assert _log_values(lines[1])["rows_read"] == "1"
    assert _log_values(lines[1])["state_skipped"] == "true"
    assert build_calls == 1


def test_sidecar_loop_skips_normalize_when_raw_tree_is_idle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        lambda _: None,
    )
    real_normalize = getattr(rust_normalizer_sidecar, "normalize_rust_event_tree")
    normalize_calls = 0

    def counting_normalize(*args: Any, **kwargs: Any) -> Any:
        nonlocal normalize_calls
        normalize_calls += 1
        return real_normalize(*args, **kwargs)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.normalize_rust_event_tree",
        counting_normalize,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert normalize_calls == 1


def test_sidecar_loop_skips_signature_merge_when_raw_tree_is_idle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    active_signature = rust_normalizer_sidecar._raw_tree_signature(
        raw_root=raw_root,
        include_state_snapshots=False,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        lambda _: None,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar._active_raw_tree_signature",
        lambda *, raw_root: active_signature,
    )
    real_merge = getattr(rust_normalizer_sidecar, "_merge_raw_signatures")
    merge_calls = 0

    def counting_merge(*args: Any, **kwargs: Any) -> Any:
        nonlocal merge_calls
        merge_calls += 1
        return real_merge(*args, **kwargs)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar._merge_raw_signatures",
        counting_merge,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert merge_calls == 0


def test_changed_raw_signature_ignores_mtime_only_touch_for_append_only_files(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text('{"event":"old"}\n', encoding="utf-8")
    previous = (
        rust_normalizer_sidecar.RawTreeFileSignature(
            path=path,
            size_bytes=path.stat().st_size,
            mtime_ns=100,
        ),
    )
    current = (
        rust_normalizer_sidecar.RawTreeFileSignature(
            path=path,
            size_bytes=path.stat().st_size,
            mtime_ns=200,
        ),
    )

    changed = rust_normalizer_sidecar._changed_raw_signature(
        previous=previous,
        current=current,
    )

    assert changed == ()


def test_sidecar_loop_throttles_idle_normalized_health_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        lambda _: None,
    )
    real_write_health = getattr(rust_normalizer_sidecar, "write_normalized_health_status")
    health_writes = 0

    def counting_write_health(*args: Any, **kwargs: Any) -> Any:
        nonlocal health_writes
        health_writes += 1
        return real_write_health(*args, **kwargs)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.write_normalized_health_status",
        counting_write_health,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=3,
    )

    assert health_writes == 1


def test_sidecar_loop_normalizes_only_changed_raw_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    changed_path = (
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / "date=2026-06-02"
        / "hour=06"
        / "events.jsonl"
    )
    real_normalize_tree = getattr(rust_normalizer_sidecar, "normalize_rust_event_tree")
    tree_calls = 0

    def counting_normalize_tree(*args: Any, **kwargs: Any) -> Any:
        nonlocal tree_calls
        tree_calls += 1
        return real_normalize_tree(*args, **kwargs)

    def append_raw_row(_: float) -> None:
        with changed_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    _orderbook_row(
                        "up-token",
                        asof_ts,
                        asof_ts,
                        0.62,
                        0.65,
                    ),
                    separators=(",", ":"),
                )
                + "\n"
            )

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.normalize_rust_event_tree",
        counting_normalize_tree,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        append_raw_row,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert tree_calls == 1
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.orderbook_snapshots").fetchone() == (3,)


def test_changed_sidecar_cycle_batches_raw_checkpoint_reads(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    store = _CountingCheckpointStore(db_path)
    store.apply_schema()
    changed_signature = rust_normalizer_sidecar._raw_tree_signature(
        raw_root=raw_root,
        include_state_snapshots=False,
    )

    result = rust_normalizer_sidecar._run_changed_rust_normalizer_cycle_with_store(
        changed_raw_signature=changed_signature,
        store=store,
        status_path=status_path,
        normalized_health_path=health_path,
        include_next=False,
    )

    assert result.files == 2
    assert result.rows_read == 4
    assert store.raw_file_checkpoints_calls == 1
    assert store.raw_file_checkpoint_calls == 0


def test_changed_sidecar_cycle_reuses_cached_raw_checkpoints(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    changed_path = (
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / "date=2026-06-02"
        / "hour=06"
        / "events.jsonl"
    )
    store = _CountingCheckpointStore(db_path)
    store.apply_schema()
    checkpoint_cache: dict[Path, int] = {}

    first_signature = rust_normalizer_sidecar._raw_tree_signature(
        raw_root=raw_root,
        include_state_snapshots=False,
    )
    first = rust_normalizer_sidecar._run_changed_rust_normalizer_cycle_with_store(
        changed_raw_signature=first_signature,
        store=store,
        status_path=status_path,
        normalized_health_path=health_path,
        include_next=False,
        checkpoint_cache=checkpoint_cache,
    )
    with changed_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                _orderbook_row(
                    "up-token",
                    asof_ts + timedelta(seconds=1),
                    asof_ts + timedelta(seconds=1),
                    0.62,
                    0.65,
                ),
                separators=(",", ":"),
            )
            + "\n"
        )
    second_signature = (rust_normalizer_sidecar._raw_file_signature(changed_path),)
    second = rust_normalizer_sidecar._run_changed_rust_normalizer_cycle_with_store(
        changed_raw_signature=second_signature,
        store=store,
        status_path=status_path,
        normalized_health_path=health_path,
        include_next=False,
        checkpoint_cache=checkpoint_cache,
    )

    assert first.rows_read == 4
    assert second.rows_read == 1
    assert store.raw_file_checkpoints_calls == 1
    assert store.raw_file_checkpoint_calls == 0


def test_changed_sidecar_cycle_skips_path_at_cached_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    changed_path = (
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / "date=2026-06-02"
        / "hour=06"
        / "events.jsonl"
    )
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    changed_signature = (rust_normalizer_sidecar._raw_file_signature(changed_path),)
    checkpoint_cache = {changed_path: changed_path.stat().st_size}
    real_normalize = getattr(rust_normalizer_sidecar, "normalize_rust_event_file")
    normalize_calls = 0

    def counting_normalize(*args: Any, **kwargs: Any) -> Any:
        nonlocal normalize_calls
        normalize_calls += 1
        return real_normalize(*args, **kwargs)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.normalize_rust_event_file",
        counting_normalize,
    )
    status_mtime = status_path.stat().st_mtime_ns

    result = rust_normalizer_sidecar._run_changed_rust_normalizer_cycle_with_store(
        changed_raw_signature=changed_signature,
        store=store,
        status_path=status_path,
        normalized_health_path=health_path,
        include_next=False,
        previous_status_mtime_ns=status_mtime,
        status_mtime_ns=status_mtime,
        write_health=False,
        checkpoint_cache=checkpoint_cache,
    )

    assert normalize_calls == 0
    assert result.files == 1
    assert result.files_skipped == 1
    assert result.bytes_read == 0
    assert result.rows_read == 0
    assert checkpoint_cache[changed_path] == changed_path.stat().st_size


def test_normalizer_summary_scans_cycle_results_once() -> None:
    rows = (
        RustEventNormalizeResult(
            path=Path("first.jsonl"),
            file_id="sha256:first",
            start_byte_offset=0,
            end_byte_offset=10,
            file_size_bytes=20,
            rows_read=1,
            price_ticks_written=1,
            orderbooks_written=0,
        ),
        RustEventNormalizeResult(
            path=Path("second.jsonl"),
            file_id="sha256:second",
            start_byte_offset=5,
            end_byte_offset=5,
            file_size_bytes=8,
            rows_read=0,
            price_ticks_written=0,
            orderbooks_written=0,
        ),
    )

    class SinglePassResults:
        def __init__(self, items: Sequence[RustEventNormalizeResult]) -> None:
            self._items = items
            self.iterations = 0

        def __len__(self) -> int:
            return len(self._items)

        def __iter__(self) -> Iterator[RustEventNormalizeResult]:
            self.iterations += 1
            if self.iterations > 1:
                raise AssertionError("normalizer summary rescanned cycle results")
            return iter(self._items)

    results = SinglePassResults(rows)

    summary = rust_normalizer_sidecar._normalizer_summary(results)

    assert summary == {
        "files": 2,
        "files_with_rows": 1,
        "files_skipped": 1,
        "bytes_read": 10,
        "file_size_bytes": 28,
        "rows_read": 1,
        "price_ticks_written": 1,
        "orderbooks_written": 0,
    }
    assert results.iterations == 1


def test_idle_normalizer_summary_uses_precomputed_raw_summary() -> None:
    raw_summary = rust_normalizer_sidecar.RawTreeIdleSummary(
        files=2,
        file_size_bytes=28,
    )

    summary = rust_normalizer_sidecar._idle_normalizer_summary(raw_summary=raw_summary)

    assert summary == {
        "files": 2,
        "files_with_rows": 0,
        "files_skipped": 2,
        "bytes_read": 0,
        "file_size_bytes": 28,
        "rows_read": 0,
        "price_ticks_written": 0,
        "orderbooks_written": 0,
    }


def test_sidecar_loop_throttles_changed_cycle_normalized_health_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    changed_path = (
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / "date=2026-06-02"
        / "hour=06"
        / "events.jsonl"
    )
    real_write_health = getattr(rust_normalizer_sidecar, "write_normalized_health_status")
    health_writes = 0

    def counting_write_health(*args: Any, **kwargs: Any) -> Any:
        nonlocal health_writes
        health_writes += 1
        return real_write_health(*args, **kwargs)

    def append_raw_row(_: float) -> None:
        with changed_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    _orderbook_row(
                        "up-token",
                        asof_ts,
                        asof_ts,
                        0.62,
                        0.65,
                    ),
                    separators=(",", ":"),
                )
                + "\n"
            )

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.write_normalized_health_status",
        counting_write_health,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        append_raw_row,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert health_writes == 1
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.orderbook_snapshots").fetchone() == (3,)


def test_sidecar_loop_uses_active_signature_between_full_scans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_current_hour_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    changed_path = (
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / f"date={asof_ts.date().isoformat()}"
        / f"hour={asof_ts.hour:02d}"
        / "events.jsonl"
    )
    real_raw_tree_signature = getattr(rust_normalizer_sidecar, "_raw_tree_signature")
    full_signature_calls = 0

    def counting_raw_tree_signature(*args: Any, **kwargs: Any) -> Any:
        nonlocal full_signature_calls
        full_signature_calls += 1
        return real_raw_tree_signature(*args, **kwargs)

    def append_raw_row(_: float) -> None:
        with changed_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    _orderbook_row(
                        "up-token",
                        asof_ts,
                        asof_ts,
                        0.62,
                        0.65,
                    ),
                    separators=(",", ":"),
                )
                + "\n"
            )

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar._raw_tree_signature",
        counting_raw_tree_signature,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        append_raw_row,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert full_signature_calls == 1
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.orderbook_snapshots").fetchone() == (3,)


def test_sidecar_loop_does_not_full_scan_when_active_signature_is_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    real_raw_tree_signature = getattr(rust_normalizer_sidecar, "_raw_tree_signature")
    full_signature_calls = 0

    def counting_raw_tree_signature(*args: Any, **kwargs: Any) -> Any:
        nonlocal full_signature_calls
        full_signature_calls += 1
        return real_raw_tree_signature(*args, **kwargs)

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar._raw_tree_signature",
        counting_raw_tree_signature,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar._active_raw_tree_signature",
        lambda *, raw_root: (),
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        lambda _: None,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert full_signature_calls == 1


def test_sidecar_loop_skips_state_build_for_ops_only_status_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(
        status_path,
        start_ts=start_ts,
        asof_ts=asof_ts,
        monitor_counter=1,
    )
    real_build = getattr(
        rust_normalizer_sidecar,
        "build_current_decision_state_snapshots",
    )
    build_calls = 0

    def counting_build(*args: Any, **kwargs: Any) -> Any:
        nonlocal build_calls
        build_calls += 1
        return real_build(*args, **kwargs)

    def change_status(_: float) -> None:
        _write_status(
            status_path,
            start_ts=start_ts,
            asof_ts=asof_ts,
            monitor_counter=2,
        )
        next_mtime = time.time() + 1
        os.utime(status_path, (next_mtime, next_mtime))

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar."
        "build_current_decision_state_snapshots",
        counting_build,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        change_status,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert build_calls == 1


def test_sidecar_loop_skips_state_build_when_only_generated_at_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    build_calls = 0

    def counting_build(*args: Any, **kwargs: Any) -> Any:
        nonlocal build_calls
        build_calls += 1
        return SimpleNamespace(contracts_upserted=0, states_written=0, unavailable=())

    def change_generated_at(_: float) -> None:
        _write_status(
            status_path,
            start_ts=start_ts,
            asof_ts=asof_ts + timedelta(seconds=1),
        )
        next_mtime = time.time() + 1
        os.utime(status_path, (next_mtime, next_mtime))

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar."
        "build_current_decision_state_snapshots",
        counting_build,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        change_generated_at,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert build_calls == 1


def test_sidecar_loop_rebuilds_state_when_status_inputs_change_without_raw_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    build_calls = 0

    def counting_build(*args: Any, **kwargs: Any) -> Any:
        nonlocal build_calls
        build_calls += 1
        return SimpleNamespace(contracts_upserted=0, states_written=0, unavailable=())

    def change_status_inputs(_: float) -> None:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        payload["current"][0]["up"]["token_id"] = "new-up-token"
        payload["generated_at"] = (asof_ts + timedelta(seconds=1)).isoformat()
        status_path.write_text(json.dumps(payload), encoding="utf-8")
        next_mtime = time.time() + 1
        os.utime(status_path, (next_mtime, next_mtime))

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar."
        "build_current_decision_state_snapshots",
        counting_build,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        change_status_inputs,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert build_calls == 2


def test_sidecar_loop_reuses_state_read_cache_across_status_input_builds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    read_caches: list[object] = []

    def counting_build(*args: Any, **kwargs: Any) -> Any:
        read_caches.append(kwargs.get("read_cache"))
        return SimpleNamespace(contracts_upserted=0, states_written=0, unavailable=())

    def change_status(_: float) -> None:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        payload["current"][0]["up"]["token_id"] = "new-up-token"
        payload["generated_at"] = (asof_ts + timedelta(seconds=1)).isoformat()
        status_path.write_text(json.dumps(payload), encoding="utf-8")
        next_mtime = time.time() + 1
        os.utime(status_path, (next_mtime, next_mtime))

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar."
        "build_current_decision_state_snapshots",
        counting_build,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        change_status,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert len(read_caches) == 2
    assert read_caches[0] is not None
    assert read_caches[0] is read_caches[1]


def test_sidecar_loop_writes_health_when_status_inputs_change_without_raw_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    real_write_health = getattr(rust_normalizer_sidecar, "write_normalized_health_status")
    health_writes = 0

    def counting_write_health(*args: Any, **kwargs: Any) -> Any:
        nonlocal health_writes
        health_writes += 1
        return real_write_health(*args, **kwargs)

    def change_status(_: float) -> None:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        payload["current"][0]["up"]["token_id"] = "new-up-token"
        payload["generated_at"] = (asof_ts + timedelta(seconds=1)).isoformat()
        status_path.write_text(json.dumps(payload), encoding="utf-8")
        next_mtime = time.time() + 1
        os.utime(status_path, (next_mtime, next_mtime))

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.write_normalized_health_status",
        counting_write_health,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        change_status,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert health_writes == 2


def test_sidecar_loop_reuses_one_duckdb_connection_across_cycles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        lambda _: None,
    )
    duckdb_module = getattr(duckdb_store, "duckdb")
    real_connect = duckdb_module.connect
    connect_count = 0

    def counting_connect(*args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        nonlocal connect_count
        connect_count += 1
        return cast(duckdb.DuckDBPyConnection, real_connect(*args, **kwargs))

    monkeypatch.setattr(duckdb_module, "connect", counting_connect)

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert connect_count == 1


def _write_raw_tree(*, raw_root: Path, start_ts: datetime, asof_ts: datetime) -> None:
    raw_root.mkdir(parents=True, exist_ok=True)
    (raw_root / ".polymarket_archive_root").write_text("", encoding="utf-8")
    _write_jsonl(
        raw_root
        / "polymarket_rtds_chainlink"
        / "price_update"
        / "date=2026-06-02"
        / "hour=06"
        / "events.jsonl",
        _chainlink_row("BTC/USD", start_ts, start_ts, 70_000.0),
        _chainlink_row("BTC/USD", asof_ts - timedelta(seconds=2), asof_ts - timedelta(seconds=1), 70_125.0),
    )
    _write_jsonl(
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / "date=2026-06-02"
        / "hour=06"
        / "events.jsonl",
        _orderbook_row("up-token", asof_ts - timedelta(seconds=2), asof_ts - timedelta(seconds=1), 0.61, 0.64),
        _orderbook_row("down-token", asof_ts - timedelta(seconds=2), asof_ts - timedelta(seconds=1), 0.36, 0.39),
    )


def _write_probability_ready_raw_tree(
    *,
    raw_root: Path,
    start_ts: datetime,
    asof_ts: datetime,
) -> None:
    raw_root.mkdir(parents=True, exist_ok=True)
    (raw_root / ".polymarket_archive_root").write_text("", encoding="utf-8")
    _write_jsonl(
        raw_root
        / "polymarket_rtds_chainlink"
        / "price_update"
        / "date=2026-06-02"
        / "hour=06"
        / "events.jsonl",
        _chainlink_row("BTC/USD", start_ts, start_ts, 70_000.0),
        _chainlink_row("BTC/USD", asof_ts - timedelta(seconds=4), asof_ts - timedelta(seconds=4), 70_000.0),
        _chainlink_row("BTC/USD", asof_ts - timedelta(seconds=3), asof_ts - timedelta(seconds=3), 70_050.0),
        _chainlink_row("BTC/USD", asof_ts - timedelta(seconds=2), asof_ts - timedelta(seconds=2), 70_125.0),
        _chainlink_row("BTC/USD", asof_ts - timedelta(seconds=1), asof_ts - timedelta(seconds=1), 70_150.0),
    )
    _write_jsonl(
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / "date=2026-06-02"
        / "hour=06"
        / "events.jsonl",
        _orderbook_row("up-token", asof_ts - timedelta(seconds=2), asof_ts - timedelta(seconds=1), 0.61, 0.64),
        _orderbook_row("down-token", asof_ts - timedelta(seconds=2), asof_ts - timedelta(seconds=1), 0.36, 0.39),
    )


def _write_current_hour_raw_tree(*, raw_root: Path, start_ts: datetime, asof_ts: datetime) -> None:
    raw_root.mkdir(parents=True, exist_ok=True)
    (raw_root / ".polymarket_archive_root").write_text("", encoding="utf-8")
    _write_jsonl(
        raw_root
        / "polymarket_rtds_chainlink"
        / "price_update"
        / f"date={asof_ts.date().isoformat()}"
        / f"hour={asof_ts.hour:02d}"
        / "events.jsonl",
        _chainlink_row("BTC/USD", start_ts, start_ts, 70_000.0),
        _chainlink_row("BTC/USD", asof_ts - timedelta(seconds=2), asof_ts - timedelta(seconds=1), 70_125.0),
    )
    _write_jsonl(
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / f"date={asof_ts.date().isoformat()}"
        / f"hour={asof_ts.hour:02d}"
        / "events.jsonl",
        _orderbook_row("up-token", asof_ts - timedelta(seconds=2), asof_ts - timedelta(seconds=1), 0.61, 0.64),
        _orderbook_row("down-token", asof_ts - timedelta(seconds=2), asof_ts - timedelta(seconds=1), 0.36, 0.39),
    )


def _write_status(
    path: Path,
    *,
    start_ts: datetime,
    asof_ts: datetime,
    monitor_counter: int | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slug = f"btc-updown-5m-{int(start_ts.timestamp())}"
    payload: dict[str, Any] = {
        "schema_version": "rust-live-probe-state-manager-v1",
        "mode": "state-manager",
        "generated_at": asof_ts.isoformat(),
        "current": [
            {
                "window": {
                    "asset": "BTC",
                    "interval": "5m",
                    "start_ts": start_ts.isoformat(),
                    "end_ts": (start_ts + timedelta(minutes=5)).isoformat(),
                },
                "up": {"asset": "BTC", "side": "Up", "token_id": "up-token"},
                "down": {"asset": "BTC", "side": "Down", "token_id": "down-token"},
            }
        ],
        "orderbooks": [
            {
                "market_slug": slug,
                "contract_id": "0xcondition",
                "token_id": "up-token",
            },
            {
                "market_slug": slug,
                "contract_id": "0xcondition",
                "token_id": "down-token",
            },
        ],
    }
    if monitor_counter is not None:
        payload["monitor"] = {
            "cycles": monitor_counter,
            "last_check_at": asof_ts.isoformat(),
        }
        payload["websockets"] = {
            "events_seen": monitor_counter,
            "reconnects": 0,
        }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _chainlink_row(symbol: str, event_ts: datetime, observed_ts: datetime, value: float) -> dict[str, object]:
    return {
        "source_key": "polymarket_rtds_chainlink",
        "stream_key": "price_update",
        "symbol": symbol,
        "event_type": "chainlink_price",
        "event_ts": event_ts.isoformat(),
        "observed_ts": observed_ts.isoformat(),
        "payload": {"value": str(value)},
    }


def _orderbook_row(
    token_id: str,
    event_ts: datetime,
    observed_ts: datetime,
    best_bid: float,
    best_ask: float,
) -> dict[str, object]:
    return {
        "source_key": "polymarket_clob_market_ws",
        "stream_key": "best_bid_ask",
        "symbol": token_id,
        "event_type": "best_bid_ask",
        "event_ts": event_ts.isoformat(),
        "observed_ts": observed_ts.isoformat(),
        "payload": {
            "contract_id": "0xcondition",
            "token_id": token_id,
            "best_bid": str(best_bid),
            "best_ask": str(best_ask),
            "spread": str(best_ask - best_bid),
        },
    }


def _write_jsonl(path: Path, *rows: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _log_values(line: str) -> dict[str, str]:
    return dict(part.split("=", maxsplit=1) for part in line.split()[1:])


class _FakeConnectionContext:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *_: object) -> None:
        return None


class _FakeConnectionStore:
    def _connection(self) -> _FakeConnectionContext:
        return _FakeConnectionContext()


class _CountingCheckpointStore(DuckDbIngestStore):
    def __init__(self, db_path: Path) -> None:
        super().__init__(db_path)
        self.raw_file_checkpoint_calls = 0
        self.raw_file_checkpoints_calls = 0

    def raw_file_checkpoint(self, path: Path) -> int | None:
        self.raw_file_checkpoint_calls += 1
        return super().raw_file_checkpoint(path)

    def raw_file_checkpoints(self, paths: Sequence[Path]) -> dict[Path, int]:
        self.raw_file_checkpoints_calls += 1
        return super().raw_file_checkpoints(paths)
