from __future__ import annotations

import json
import os
import time
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import duckdb
import pytest

from polymarket_engine.ingestion import rust_normalizer_sidecar
from polymarket_engine.ingestion.rust_normalizer_sidecar import (
    _cadence_sleep_seconds,
    run_rust_normalizer_cycle,
    run_rust_normalizer_loop,
)
from polymarket_engine.storage import duckdb_store
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def test_sidecar_cycle_normalizes_builds_states_and_writes_health(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)

    result = run_rust_normalizer_cycle(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        include_next=False,
    )

    assert result.files == 2
    assert result.rows_read == 4
    assert result.bytes_read > 0
    assert result.price_ticks_written == 2
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
    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.price_ticks").fetchone() == (2,)
        assert conn.execute("select count(*) from core.orderbook_snapshots").fetchone() == (2,)
        assert conn.execute("select count(*) from features.asof_state_inputs").fetchone() == (2,)


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


def test_sidecar_loop_rebuilds_state_when_status_changes_without_raw_rows(
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

    assert build_calls == 2


def test_sidecar_loop_writes_health_when_status_changes_without_raw_rows(
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
        _write_status(
            status_path,
            start_ts=start_ts,
            asof_ts=asof_ts + timedelta(seconds=1),
        )
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


def _write_status(path: Path, *, start_ts: datetime, asof_ts: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    slug = f"btc-updown-5m-{int(start_ts.timestamp())}"
    payload = {
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
