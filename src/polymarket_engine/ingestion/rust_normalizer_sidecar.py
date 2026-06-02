from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from polymarket_engine.features.rust_decision_snapshots import (
    CurrentDecisionStateReadCache,
    UnavailableDecisionState,
    build_current_decision_state_snapshots,
)
from polymarket_engine.health.normalized_status import write_normalized_health_status
from polymarket_engine.ingestion.rust_event_normalizer import (
    RUST_JSONL_STREAMS,
    STATE_SNAPSHOT_STREAMS,
    RustEventNormalizeResult,
    normalize_rust_event_file,
    normalize_rust_event_tree,
)
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


FULL_RAW_TREE_SCAN_INTERVAL_CYCLES = 240
IDLE_NORMALIZED_HEALTH_WRITE_INTERVAL_SECONDS = 5.0


@dataclass(frozen=True)
class RawTreeFileSignature:
    path: Path
    size_bytes: int
    mtime_ns: int


@dataclass(frozen=True)
class RustNormalizerCycleResult:
    files: int
    files_with_rows: int
    files_skipped: int
    bytes_read: int
    file_size_bytes: int
    rows_read: int
    price_ticks_written: int
    orderbooks_written: int
    contracts_upserted: int
    states_written: int
    state_skipped: bool
    unavailable: tuple[UnavailableDecisionState, ...]
    elapsed_ms: int
    normalize_ms: int
    state_ms: int
    health_ms: int
    health_skipped: bool

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "files_with_rows": self.files_with_rows,
            "files_skipped": self.files_skipped,
            "bytes_read": self.bytes_read,
            "file_size_bytes": self.file_size_bytes,
            "rows_read": self.rows_read,
            "price_ticks_written": self.price_ticks_written,
            "orderbooks_written": self.orderbooks_written,
            "contracts_upserted": self.contracts_upserted,
            "states_written": self.states_written,
            "state_skipped": self.state_skipped,
            "unavailable": [
                {
                    "contract_id": row.contract_id,
                    "token_id": row.token_id,
                    "reason": row.reason,
                }
                for row in self.unavailable
            ],
            "elapsed_ms": self.elapsed_ms,
            "normalize_ms": self.normalize_ms,
            "state_ms": self.state_ms,
            "health_ms": self.health_ms,
            "health_skipped": self.health_skipped,
        }


def run_rust_normalizer_cycle(
    *,
    raw_root: Path,
    db_path: Path,
    status_path: Path,
    normalized_health_path: Path,
    include_next: bool = False,
    reprocess_all: bool = False,
    apply_schema: bool = True,
) -> RustNormalizerCycleResult:
    with DuckDbIngestStore(db_path) as store:
        return _run_rust_normalizer_cycle_with_store(
            raw_root=raw_root,
            store=store,
            status_path=status_path,
            normalized_health_path=normalized_health_path,
            include_next=include_next,
            reprocess_all=reprocess_all,
            apply_schema=apply_schema,
        )


def _run_rust_normalizer_cycle_with_store(
    *,
    raw_root: Path,
    store: DuckDbIngestStore,
    status_path: Path,
    normalized_health_path: Path,
    include_next: bool,
    reprocess_all: bool,
    apply_schema: bool,
    previous_status_mtime_ns: int | None = None,
    status_mtime_ns: int | None = None,
    force_state_build: bool = True,
    state_read_cache: CurrentDecisionStateReadCache | None = None,
) -> RustNormalizerCycleResult:
    cycle_started = time.perf_counter()
    if apply_schema:
        store.apply_schema()

    results = normalize_rust_event_tree(
        raw_root=raw_root,
        store=store,
        reprocess_all=reprocess_all,
    )
    normalized_at = time.perf_counter()

    summary = _normalizer_summary(results)
    if state_read_cache is not None and summary["price_ticks_written"] > 0:
        state_read_cache.clear()
    if status_mtime_ns is None:
        status_mtime_ns = _file_mtime_ns(status_path)
    build_state = status_mtime_ns is not None and (
        force_state_build
        or reprocess_all
        or _observations_written(summary)
        or status_mtime_ns != previous_status_mtime_ns
    )

    contracts_upserted = 0
    states_written = 0
    unavailable: tuple[UnavailableDecisionState, ...] = ()
    if build_state:
        state_result = build_current_decision_state_snapshots(
            status_path=status_path,
            store=store,
            include_next=include_next,
            read_cache=state_read_cache,
        )
        contracts_upserted = state_result.contracts_upserted
        states_written = state_result.states_written
        unavailable = state_result.unavailable
    state_at = time.perf_counter()

    write_normalized_health_status(store=store, out_path=normalized_health_path)
    health_at = time.perf_counter()

    return RustNormalizerCycleResult(
        **summary,
        contracts_upserted=contracts_upserted,
        states_written=states_written,
        state_skipped=status_mtime_ns is not None and not build_state,
        unavailable=unavailable,
        elapsed_ms=_elapsed_ms(cycle_started, health_at),
        normalize_ms=_elapsed_ms(cycle_started, normalized_at),
        state_ms=_elapsed_ms(normalized_at, state_at),
        health_ms=_elapsed_ms(state_at, health_at),
        health_skipped=False,
    )


def run_rust_normalizer_loop(
    *,
    raw_root: Path,
    db_path: Path,
    status_path: Path,
    normalized_health_path: Path,
    interval_seconds: float = 1.0,
    include_next: bool = False,
    reprocess_all: bool = False,
    max_cycles: int | None = None,
) -> None:
    with DuckDbIngestStore(db_path) as store:
        store.apply_schema()
        cycles_run = 0
        previous_status_mtime_ns: int | None = None
        previous_raw_signature: tuple[RawTreeFileSignature, ...] | None = None
        raw_checkpoint_cache: dict[Path, int] = {}
        price_state_cache: dict[tuple[str, str], tuple[object, ...]] = {}
        orderbook_state_cache: dict[tuple[str, str], tuple[object, ...]] = {}
        state_read_cache = CurrentDecisionStateReadCache()
        last_health_write_monotonic: float | None = None
        while True:
            cycle_started = time.monotonic()
            status_mtime_ns = _file_mtime_ns(status_path)
            full_scan_due = (
                reprocess_all
                or previous_raw_signature is None
                or cycles_run % FULL_RAW_TREE_SCAN_INTERVAL_CYCLES == 0
            )
            if full_scan_due:
                raw_signature = _raw_tree_signature(
                    raw_root=raw_root,
                    include_state_snapshots=False,
                )
            else:
                raw_signature = _active_raw_tree_signature(raw_root=raw_root)
                if not raw_signature:
                    assert previous_raw_signature is not None
                    raw_signature = _known_raw_tree_signature(previous_raw_signature)

            raw_signature_changed = False
            if reprocess_all:
                result = _run_rust_normalizer_cycle_with_store(
                    raw_root=raw_root,
                    store=store,
                    status_path=status_path,
                    normalized_health_path=normalized_health_path,
                    include_next=include_next,
                    reprocess_all=reprocess_all,
                    apply_schema=False,
                    previous_status_mtime_ns=previous_status_mtime_ns,
                    status_mtime_ns=status_mtime_ns,
                    force_state_build=cycles_run == 0,
                    state_read_cache=state_read_cache,
                )
                last_health_write_monotonic = cycle_started
            elif previous_raw_signature is None:
                active_raw_signature = _active_raw_tree_signature(raw_root=raw_root)
                if active_raw_signature:
                    result = _run_changed_rust_normalizer_cycle_with_store(
                        changed_raw_signature=active_raw_signature,
                        store=store,
                        status_path=status_path,
                        normalized_health_path=normalized_health_path,
                        include_next=include_next,
                        previous_status_mtime_ns=previous_status_mtime_ns,
                        status_mtime_ns=status_mtime_ns,
                        write_health=True,
                        checkpoint_cache=raw_checkpoint_cache,
                        price_state_cache=price_state_cache,
                        orderbook_state_cache=orderbook_state_cache,
                        state_read_cache=state_read_cache,
                    )
                else:
                    result = _run_rust_normalizer_cycle_with_store(
                        raw_root=raw_root,
                        store=store,
                        status_path=status_path,
                        normalized_health_path=normalized_health_path,
                        include_next=include_next,
                        reprocess_all=reprocess_all,
                        apply_schema=False,
                        previous_status_mtime_ns=previous_status_mtime_ns,
                        status_mtime_ns=status_mtime_ns,
                        force_state_build=True,
                        state_read_cache=state_read_cache,
                    )
                last_health_write_monotonic = cycle_started
            else:
                changed_raw_signature = _changed_raw_signature(
                    previous=previous_raw_signature,
                    current=raw_signature,
                )
                raw_signature_changed = bool(changed_raw_signature)
                status_changed = (
                    status_mtime_ns is not None
                    and status_mtime_ns != previous_status_mtime_ns
                )
                if changed_raw_signature:
                    write_health = _idle_health_write_due(
                        last_health_write_monotonic=last_health_write_monotonic,
                        cycle_started=cycle_started,
                    ) or status_changed
                    result = _run_changed_rust_normalizer_cycle_with_store(
                        changed_raw_signature=changed_raw_signature,
                        store=store,
                        status_path=status_path,
                        normalized_health_path=normalized_health_path,
                        include_next=include_next,
                        previous_status_mtime_ns=previous_status_mtime_ns,
                        status_mtime_ns=status_mtime_ns,
                        write_health=write_health,
                        checkpoint_cache=raw_checkpoint_cache,
                        price_state_cache=price_state_cache,
                        orderbook_state_cache=orderbook_state_cache,
                        state_read_cache=state_read_cache,
                    )
                    if not result.health_skipped:
                        last_health_write_monotonic = cycle_started
                else:
                    write_health = _idle_health_write_due(
                        last_health_write_monotonic=last_health_write_monotonic,
                        cycle_started=cycle_started,
                    ) or status_changed
                    result = _run_idle_rust_normalizer_cycle_with_store(
                        raw_signature=previous_raw_signature,
                        store=store,
                        status_path=status_path,
                        normalized_health_path=normalized_health_path,
                        include_next=include_next,
                        reprocess_all=reprocess_all,
                        previous_status_mtime_ns=previous_status_mtime_ns,
                        status_mtime_ns=status_mtime_ns,
                        force_state_build=cycles_run == 0,
                        write_health=write_health,
                        state_read_cache=state_read_cache,
                    )
                    if not result.health_skipped:
                        last_health_write_monotonic = cycle_started
            print(_cycle_log_line(result), flush=True)
            previous_status_mtime_ns = status_mtime_ns
            if full_scan_due:
                previous_raw_signature = raw_signature
            else:
                assert previous_raw_signature is not None
                if raw_signature_changed:
                    previous_raw_signature = _merge_raw_signatures(
                        previous=previous_raw_signature,
                        current=raw_signature,
                    )
            cycles_run += 1
            if max_cycles is not None and cycles_run >= max_cycles:
                return
            time.sleep(
                _cadence_sleep_seconds(
                    cycle_started=cycle_started,
                    interval_seconds=interval_seconds,
                    now=time.monotonic(),
                )
            )


def _run_changed_rust_normalizer_cycle_with_store(
    *,
    changed_raw_signature: tuple[RawTreeFileSignature, ...],
    store: DuckDbIngestStore,
    status_path: Path,
    normalized_health_path: Path,
    include_next: bool,
    previous_status_mtime_ns: int | None = None,
    status_mtime_ns: int | None = None,
    write_health: bool = True,
    checkpoint_cache: dict[Path, int] | None = None,
    price_state_cache: dict[tuple[str, str], tuple[object, ...]] | None = None,
    orderbook_state_cache: dict[tuple[str, str], tuple[object, ...]] | None = None,
    state_read_cache: CurrentDecisionStateReadCache | None = None,
) -> RustNormalizerCycleResult:
    cycle_started = time.perf_counter()
    changed_paths = tuple(row.path for row in changed_raw_signature)
    if checkpoint_cache is None:
        checkpoints = store.raw_file_checkpoints(changed_paths)
    else:
        checkpoints = {
            path: checkpoint_cache[path]
            for path in changed_paths
            if path in checkpoint_cache
        }
        missing_paths = tuple(path for path in changed_paths if path not in checkpoints)
        if missing_paths:
            checkpoints.update(store.raw_file_checkpoints(missing_paths))
    result_rows: list[RustEventNormalizeResult] = []
    for row in changed_raw_signature:
        checkpoint = checkpoints.get(row.path)
        if checkpoint == row.size_bytes:
            result_rows.append(_cached_checkpoint_result(row, checkpoint))
            continue
        result_rows.append(
            normalize_rust_event_file(
                path=row.path,
                store=store,
                reprocess_all=False,
                checkpoint=checkpoint,
                checkpoint_loaded=True,
                last_price_state_by_symbol=price_state_cache,
                last_orderbook_state_by_token=orderbook_state_cache,
            )
        )
    results = tuple(result_rows)
    if checkpoint_cache is not None:
        for result in results:
            checkpoint_cache[result.path] = result.end_byte_offset
    normalized_at = time.perf_counter()

    summary = _normalizer_summary(results)
    if state_read_cache is not None and summary["price_ticks_written"] > 0:
        state_read_cache.clear()
    if status_mtime_ns is None:
        status_mtime_ns = _file_mtime_ns(status_path)
    build_state = status_mtime_ns is not None and (
        _observations_written(summary) or status_mtime_ns != previous_status_mtime_ns
    )

    contracts_upserted = 0
    states_written = 0
    unavailable: tuple[UnavailableDecisionState, ...] = ()
    if build_state:
        state_result = build_current_decision_state_snapshots(
            status_path=status_path,
            store=store,
            include_next=include_next,
            read_cache=state_read_cache,
        )
        contracts_upserted = state_result.contracts_upserted
        states_written = state_result.states_written
        unavailable = state_result.unavailable
    state_at = time.perf_counter()

    health_skipped = not write_health
    if write_health:
        write_normalized_health_status(store=store, out_path=normalized_health_path)
    health_at = time.perf_counter()

    return RustNormalizerCycleResult(
        **summary,
        contracts_upserted=contracts_upserted,
        states_written=states_written,
        state_skipped=status_mtime_ns is not None and not build_state,
        unavailable=unavailable,
        elapsed_ms=_elapsed_ms(cycle_started, health_at),
        normalize_ms=_elapsed_ms(cycle_started, normalized_at),
        state_ms=_elapsed_ms(normalized_at, state_at),
        health_ms=0 if health_skipped else _elapsed_ms(state_at, health_at),
        health_skipped=health_skipped,
    )


def _run_idle_rust_normalizer_cycle_with_store(
    *,
    raw_signature: tuple[RawTreeFileSignature, ...],
    store: DuckDbIngestStore,
    status_path: Path,
    normalized_health_path: Path,
    include_next: bool,
    reprocess_all: bool,
    previous_status_mtime_ns: int | None = None,
    status_mtime_ns: int | None = None,
    force_state_build: bool = False,
    write_health: bool = True,
    state_read_cache: CurrentDecisionStateReadCache | None = None,
) -> RustNormalizerCycleResult:
    cycle_started = time.perf_counter()
    if status_mtime_ns is None:
        status_mtime_ns = _file_mtime_ns(status_path)
    build_state = status_mtime_ns is not None and (
        force_state_build
        or reprocess_all
        or status_mtime_ns != previous_status_mtime_ns
    )

    contracts_upserted = 0
    states_written = 0
    unavailable: tuple[UnavailableDecisionState, ...] = ()
    if build_state:
        state_result = build_current_decision_state_snapshots(
            status_path=status_path,
            store=store,
            include_next=include_next,
            read_cache=state_read_cache,
        )
        contracts_upserted = state_result.contracts_upserted
        states_written = state_result.states_written
        unavailable = state_result.unavailable
    state_at = time.perf_counter()

    health_skipped = not write_health
    if write_health:
        write_normalized_health_status(store=store, out_path=normalized_health_path)
    health_at = time.perf_counter()

    return RustNormalizerCycleResult(
        **_idle_normalizer_summary(raw_signature),
        contracts_upserted=contracts_upserted,
        states_written=states_written,
        state_skipped=status_mtime_ns is not None and not build_state,
        unavailable=unavailable,
        elapsed_ms=_elapsed_ms(cycle_started, health_at),
        normalize_ms=0,
        state_ms=_elapsed_ms(cycle_started, state_at),
        health_ms=0 if health_skipped else _elapsed_ms(state_at, health_at),
        health_skipped=health_skipped,
    )


def _normalizer_summary(results: Iterable[RustEventNormalizeResult]) -> dict[str, int]:
    summary = {
        "files": 0,
        "files_with_rows": 0,
        "files_skipped": 0,
        "bytes_read": 0,
        "file_size_bytes": 0,
        "rows_read": 0,
        "price_ticks_written": 0,
        "orderbooks_written": 0,
    }
    for result in results:
        summary["files"] += 1
        summary["files_with_rows"] += int(result.rows_read > 0)
        summary["files_skipped"] += int(result.rows_read == 0)
        summary["bytes_read"] += result.end_byte_offset - result.start_byte_offset
        summary["file_size_bytes"] += result.file_size_bytes
        summary["rows_read"] += result.rows_read
        summary["price_ticks_written"] += result.price_ticks_written
        summary["orderbooks_written"] += result.orderbooks_written
    return summary


def _cached_checkpoint_result(
    row: RawTreeFileSignature,
    checkpoint: int,
) -> RustEventNormalizeResult:
    return RustEventNormalizeResult(
        path=row.path,
        file_id="",
        start_byte_offset=checkpoint,
        end_byte_offset=checkpoint,
        file_size_bytes=row.size_bytes,
        rows_read=0,
        price_ticks_written=0,
        orderbooks_written=0,
    )


def _observations_written(summary: dict[str, int]) -> bool:
    return summary["price_ticks_written"] > 0 or summary["orderbooks_written"] > 0


def _idle_normalizer_summary(
    raw_signature: tuple[RawTreeFileSignature, ...],
) -> dict[str, int]:
    return {
        "files": len(raw_signature),
        "files_with_rows": 0,
        "files_skipped": len(raw_signature),
        "bytes_read": 0,
        "file_size_bytes": sum(row.size_bytes for row in raw_signature),
        "rows_read": 0,
        "price_ticks_written": 0,
        "orderbooks_written": 0,
    }


def _raw_tree_signature(
    *,
    raw_root: Path,
    include_state_snapshots: bool,
) -> tuple[RawTreeFileSignature, ...]:
    streams = RUST_JSONL_STREAMS + (STATE_SNAPSHOT_STREAMS if include_state_snapshots else ())
    rows: list[RawTreeFileSignature] = []
    for source_key, stream_key in streams:
        stream_root = raw_root / source_key / stream_key
        if not stream_root.exists():
            continue
        for path in sorted(stream_root.rglob("*.jsonl")):
            if not path.is_file():
                continue
            rows.append(_raw_file_signature(path))
    return tuple(rows)


def _active_raw_tree_signature(
    *,
    raw_root: Path,
    now: datetime | None = None,
) -> tuple[RawTreeFileSignature, ...]:
    if now is None:
        now = datetime.now(timezone.utc)
    now = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    hours = (now - timedelta(hours=1), now)
    rows: list[RawTreeFileSignature] = []
    for source_key, stream_key in RUST_JSONL_STREAMS:
        for hour in hours:
            hour_root = (
                raw_root
                / source_key
                / stream_key
                / f"date={hour.date().isoformat()}"
                / f"hour={hour.hour:02d}"
            )
            if not hour_root.exists():
                continue
            for path in sorted(hour_root.glob("*.jsonl")):
                if path.is_file():
                    rows.append(_raw_file_signature(path))
    return tuple(rows)


def _raw_file_signature(path: Path) -> RawTreeFileSignature:
    stat = path.stat()
    return RawTreeFileSignature(
        path=path,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def _known_raw_tree_signature(
    previous: tuple[RawTreeFileSignature, ...],
) -> tuple[RawTreeFileSignature, ...]:
    rows: list[RawTreeFileSignature] = []
    for row in previous:
        try:
            rows.append(_raw_file_signature(row.path))
        except FileNotFoundError:
            continue
    return tuple(rows)


def _changed_raw_signature(
    *,
    previous: tuple[RawTreeFileSignature, ...],
    current: tuple[RawTreeFileSignature, ...],
) -> tuple[RawTreeFileSignature, ...]:
    previous_by_path = {row.path: row for row in previous}
    return tuple(
        row
        for row in current
        if (old := previous_by_path.get(row.path)) is None
        or old.size_bytes != row.size_bytes
    )


def _merge_raw_signatures(
    *,
    previous: tuple[RawTreeFileSignature, ...],
    current: tuple[RawTreeFileSignature, ...],
) -> tuple[RawTreeFileSignature, ...]:
    by_path = {row.path: row for row in previous}
    by_path.update((row.path, row) for row in current)
    return tuple(by_path[path] for path in sorted(by_path))


def _idle_health_write_due(
    *,
    last_health_write_monotonic: float | None,
    cycle_started: float,
    interval_seconds: float = IDLE_NORMALIZED_HEALTH_WRITE_INTERVAL_SECONDS,
) -> bool:
    return (
        last_health_write_monotonic is None
        or cycle_started - last_health_write_monotonic >= interval_seconds
    )


def _elapsed_ms(start: float, end: float) -> int:
    return max(0, int(round((end - start) * 1000)))


def _file_mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return None


def _cadence_sleep_seconds(
    *,
    cycle_started: float,
    interval_seconds: float,
    now: float,
) -> float:
    return max(0.0, interval_seconds - (now - cycle_started))


def _cycle_log_line(result: RustNormalizerCycleResult) -> str:
    return (
        "normalizer_cycle "
        f"elapsed_ms={result.elapsed_ms} "
        f"normalize_ms={result.normalize_ms} "
        f"state_ms={result.state_ms} "
        f"health_ms={result.health_ms} "
        f"health_skipped={str(result.health_skipped).lower()} "
        f"files={result.files} "
        f"rows_read={result.rows_read} "
        f"bytes_read={result.bytes_read} "
        f"state_skipped={str(result.state_skipped).lower()}"
    )
