from __future__ import annotations

import hashlib
import json
import os
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
from polymarket_engine.probability.runtime import compute_and_persist_probability_outputs
from polymarket_engine.probability.runtime import latest_probability_output_rows_from_connection
from polymarket_engine.storage.atomic import durable_replace
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore
from polymarket_engine.validation.outcomes import PolymarketClobMarketPayloadSource
from polymarket_engine.validation.outcomes import latest_market_outcome_rows_from_connection
from polymarket_engine.validation.outcomes import upsert_official_market_outcomes
from polymarket_engine.validation.outcomes import write_outcome_history_status


FULL_RAW_TREE_SCAN_INTERVAL_CYCLES = 240
IDLE_NORMALIZED_HEALTH_WRITE_INTERVAL_SECONDS = 5.0
PROBABILITY_OUTPUT_LIMIT = 8
PROBABILITY_MAX_STATE_AGE_SECONDS = 600.0
OUTCOME_OUTPUT_LIMIT = 5000
OUTCOME_REFRESH_INTERVAL_SECONDS = 30.0
OUTCOME_PENDING_REFRESH_INTERVAL_SECONDS = 5.0
OUTCOME_REFRESH_MARKET_LIMIT = 4
OUTCOME_PENDING_SWEEP_LIMIT = 20
OUTCOME_OUTPUT_LIMIT_ENV = "POLYMARKET_OUTCOME_OUTPUT_LIMIT"
OFFICIAL_OUTCOME_SOURCE_ENV = "POLYMARKET_OFFICIAL_OUTCOME_SOURCE"
OFFICIAL_OUTCOME_REFRESH_LIMIT_ENV = "POLYMARKET_OFFICIAL_OUTCOME_REFRESH_LIMIT"
OFFICIAL_OUTCOME_PENDING_SWEEP_LIMIT_ENV = (
    "POLYMARKET_OFFICIAL_OUTCOME_PENDING_SWEEP_LIMIT"
)


@dataclass(frozen=True)
class RawTreeFileSignature:
    path: Path
    size_bytes: int
    mtime_ns: int


@dataclass(frozen=True)
class RawTreeIdleSummary:
    files: int
    file_size_bytes: int


@dataclass(frozen=True)
class StatusStateSignature:
    mtime_ns: int
    semantic_hash: str


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
    probability_outputs_written: int
    market_outcomes_written: int
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
            "probability_outputs_written": self.probability_outputs_written,
            "market_outcomes_written": self.market_outcomes_written,
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
    probability_status_path: Path | None = None,
    outcome_status_path: Path | None = None,
    target_status_path: Path | None = None,
    include_next: bool = False,
    compute_probabilities: bool = False,
    reprocess_all: bool = False,
    apply_schema: bool = True,
) -> RustNormalizerCycleResult:
    probability_status_path = probability_status_path or normalized_health_path.with_name(
        "probabilities.json"
    )
    outcome_status_path = outcome_status_path or normalized_health_path.with_name(
        "outcomes.json"
    )
    target_status_path = target_status_path or normalized_health_path.with_name("targets.json")
    with DuckDbIngestStore(db_path) as store:
        return _run_rust_normalizer_cycle_with_store(
            raw_root=raw_root,
            store=store,
            status_path=status_path,
            normalized_health_path=normalized_health_path,
            probability_status_path=probability_status_path,
            outcome_status_path=outcome_status_path,
            target_status_path=target_status_path,
            include_next=include_next,
            compute_probabilities=compute_probabilities,
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
    refresh_outcomes: bool = True,
    compute_probabilities: bool = False,
    state_read_cache: CurrentDecisionStateReadCache | None = None,
    probability_status_path: Path | None = None,
    outcome_status_path: Path | None = None,
    target_status_path: Path | None = None,
) -> RustNormalizerCycleResult:
    cycle_started = time.perf_counter()
    probability_status_path = probability_status_path or normalized_health_path.with_name(
        "probabilities.json"
    )
    outcome_status_path = outcome_status_path or normalized_health_path.with_name(
        "outcomes.json"
    )
    target_status_path = target_status_path or normalized_health_path.with_name("targets.json")
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
    probability_outputs_written = 0
    unavailable: tuple[UnavailableDecisionState, ...] = ()
    if build_state:
        try:
            state_result = build_current_decision_state_snapshots(
                status_path=status_path,
                store=store,
                include_next=include_next,
                read_cache=state_read_cache,
            )
        except ValueError as exc:
            unavailable = (_state_build_unavailable(exc),)
        else:
            contracts_upserted = state_result.contracts_upserted
            states_written = state_result.states_written
            unavailable = state_result.unavailable
            if compute_probabilities:
                probability_outputs_written = _compute_probability_outputs(
                    store=store,
                    out_path=probability_status_path,
                )
    market_outcomes_written = (
        _upsert_market_outcomes(
            store=store,
            out_path=outcome_status_path,
        )
        if refresh_outcomes
        else 0
    )
    _write_target_cache_status(
        store=store,
        status_path=status_path,
        out_path=target_status_path,
        asof_ts=datetime.now(timezone.utc),
    )
    state_at = time.perf_counter()

    write_normalized_health_status(store=store, out_path=normalized_health_path)
    health_at = time.perf_counter()

    return RustNormalizerCycleResult(
        **summary,
        contracts_upserted=contracts_upserted,
        states_written=states_written,
        probability_outputs_written=probability_outputs_written,
        market_outcomes_written=market_outcomes_written,
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
    probability_status_path: Path | None = None,
    outcome_status_path: Path | None = None,
    target_status_path: Path | None = None,
    interval_seconds: float = 1.0,
    include_next: bool = False,
    compute_probabilities: bool = False,
    reprocess_all: bool = False,
    max_cycles: int | None = None,
) -> None:
    probability_status_path = probability_status_path or normalized_health_path.with_name(
        "probabilities.json"
    )
    outcome_status_path = outcome_status_path or normalized_health_path.with_name(
        "outcomes.json"
    )
    target_status_path = target_status_path or normalized_health_path.with_name("targets.json")
    with DuckDbIngestStore(db_path) as store:
        store.apply_schema()
        cycles_run = 0
        previous_status_mtime_ns: int | None = None
        previous_status_signature: StatusStateSignature | None = None
        previous_raw_signature: tuple[RawTreeFileSignature, ...] | None = None
        previous_raw_summary: RawTreeIdleSummary | None = None
        raw_checkpoint_cache: dict[Path, int] = {}
        price_state_cache: dict[tuple[str, str], tuple[object, ...]] = {}
        orderbook_state_cache: dict[tuple[str, str], tuple[object, ...]] = {}
        state_read_cache = CurrentDecisionStateReadCache()
        last_health_write_monotonic: float | None = None
        last_outcome_refresh_monotonic: float | None = None
        last_outcome_refresh_had_pending = False
        while True:
            cycle_started = time.monotonic()
            refresh_outcomes = _outcome_refresh_due(
                last_outcome_refresh_monotonic=last_outcome_refresh_monotonic,
                cycle_started=cycle_started,
                had_pending_outcomes=last_outcome_refresh_had_pending,
            )
            status_signature = _status_state_signature(status_path)
            status_mtime_ns = (
                status_signature.mtime_ns if status_signature is not None else None
            )
            status_changed = _status_signature_changed(
                previous_status_signature,
                status_signature,
            )
            effective_previous_status_mtime_ns = (
                previous_status_mtime_ns if status_changed else status_mtime_ns
            )
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
                    probability_status_path=probability_status_path,
                    outcome_status_path=outcome_status_path,
                    target_status_path=target_status_path,
                    include_next=include_next,
                    compute_probabilities=compute_probabilities,
                    reprocess_all=reprocess_all,
                    apply_schema=False,
                    previous_status_mtime_ns=effective_previous_status_mtime_ns,
                    status_mtime_ns=status_mtime_ns,
                    force_state_build=cycles_run == 0,
                    refresh_outcomes=refresh_outcomes,
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
                        probability_status_path=probability_status_path,
                        outcome_status_path=outcome_status_path,
                        target_status_path=target_status_path,
                        include_next=include_next,
                        compute_probabilities=compute_probabilities,
                        previous_status_mtime_ns=effective_previous_status_mtime_ns,
                        status_mtime_ns=status_mtime_ns,
                        write_health=True,
                        checkpoint_cache=raw_checkpoint_cache,
                        price_state_cache=price_state_cache,
                        orderbook_state_cache=orderbook_state_cache,
                        refresh_outcomes=refresh_outcomes,
                        state_read_cache=state_read_cache,
                    )
                else:
                    result = _run_rust_normalizer_cycle_with_store(
                        raw_root=raw_root,
                        store=store,
                        status_path=status_path,
                        normalized_health_path=normalized_health_path,
                        probability_status_path=probability_status_path,
                        outcome_status_path=outcome_status_path,
                        target_status_path=target_status_path,
                        include_next=include_next,
                        compute_probabilities=compute_probabilities,
                        reprocess_all=reprocess_all,
                        apply_schema=False,
                        previous_status_mtime_ns=effective_previous_status_mtime_ns,
                        status_mtime_ns=status_mtime_ns,
                        force_state_build=True,
                        refresh_outcomes=refresh_outcomes,
                        state_read_cache=state_read_cache,
                    )
                last_health_write_monotonic = cycle_started
            else:
                changed_raw_signature = _changed_raw_signature(
                    previous=previous_raw_signature,
                    current=raw_signature,
                )
                raw_signature_changed = bool(changed_raw_signature)
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
                        probability_status_path=probability_status_path,
                        outcome_status_path=outcome_status_path,
                        target_status_path=target_status_path,
                        include_next=include_next,
                        compute_probabilities=compute_probabilities,
                        previous_status_mtime_ns=effective_previous_status_mtime_ns,
                        status_mtime_ns=status_mtime_ns,
                        write_health=write_health,
                        checkpoint_cache=raw_checkpoint_cache,
                        price_state_cache=price_state_cache,
                        orderbook_state_cache=orderbook_state_cache,
                        refresh_outcomes=refresh_outcomes,
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
                        raw_summary=previous_raw_summary,
                        store=store,
                        status_path=status_path,
                        normalized_health_path=normalized_health_path,
                        probability_status_path=probability_status_path,
                        outcome_status_path=outcome_status_path,
                        target_status_path=target_status_path,
                        include_next=include_next,
                        compute_probabilities=compute_probabilities,
                        reprocess_all=reprocess_all,
                        previous_status_mtime_ns=effective_previous_status_mtime_ns,
                        status_mtime_ns=status_mtime_ns,
                        force_state_build=cycles_run == 0,
                        write_health=write_health,
                        refresh_outcomes=refresh_outcomes,
                        state_read_cache=state_read_cache,
                    )
                    if not result.health_skipped:
                        last_health_write_monotonic = cycle_started
            print(_cycle_log_line(result), flush=True)
            if refresh_outcomes:
                last_outcome_refresh_monotonic = cycle_started
                last_outcome_refresh_had_pending = _has_expired_pending_official_outcomes(
                    store=store,
                )
            previous_status_mtime_ns = status_mtime_ns
            previous_status_signature = status_signature
            if full_scan_due:
                previous_raw_signature = raw_signature
                previous_raw_summary = _raw_tree_idle_summary(previous_raw_signature)
            else:
                assert previous_raw_signature is not None
                if raw_signature_changed:
                    previous_raw_signature = _merge_raw_signatures(
                        previous=previous_raw_signature,
                        current=raw_signature,
                    )
                    previous_raw_summary = _raw_tree_idle_summary(previous_raw_signature)
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
    refresh_outcomes: bool = True,
    compute_probabilities: bool = False,
    state_read_cache: CurrentDecisionStateReadCache | None = None,
    probability_status_path: Path | None = None,
    outcome_status_path: Path | None = None,
    target_status_path: Path | None = None,
) -> RustNormalizerCycleResult:
    cycle_started = time.perf_counter()
    probability_status_path = probability_status_path or normalized_health_path.with_name(
        "probabilities.json"
    )
    outcome_status_path = outcome_status_path or normalized_health_path.with_name(
        "outcomes.json"
    )
    target_status_path = target_status_path or normalized_health_path.with_name("targets.json")
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
        status_mtime_ns != previous_status_mtime_ns
    )

    contracts_upserted = 0
    states_written = 0
    probability_outputs_written = 0
    unavailable: tuple[UnavailableDecisionState, ...] = ()
    if build_state:
        try:
            state_result = build_current_decision_state_snapshots(
                status_path=status_path,
                store=store,
                include_next=include_next,
                read_cache=state_read_cache,
            )
        except ValueError as exc:
            unavailable = (_state_build_unavailable(exc),)
        else:
            contracts_upserted = state_result.contracts_upserted
            states_written = state_result.states_written
            unavailable = state_result.unavailable
            if compute_probabilities:
                probability_outputs_written = _compute_probability_outputs(
                    store=store,
                    out_path=probability_status_path,
                )
    market_outcomes_written = (
        _upsert_market_outcomes(
            store=store,
            out_path=outcome_status_path,
        )
        if refresh_outcomes
        else 0
    )
    _write_target_cache_status(
        store=store,
        status_path=status_path,
        out_path=target_status_path,
        asof_ts=datetime.now(timezone.utc),
    )
    state_at = time.perf_counter()

    health_skipped = not write_health
    if write_health:
        write_normalized_health_status(store=store, out_path=normalized_health_path)
    health_at = time.perf_counter()

    return RustNormalizerCycleResult(
        **summary,
        contracts_upserted=contracts_upserted,
        states_written=states_written,
        probability_outputs_written=probability_outputs_written,
        market_outcomes_written=market_outcomes_written,
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
    raw_summary: RawTreeIdleSummary | None = None,
    store: DuckDbIngestStore,
    status_path: Path,
    normalized_health_path: Path,
    include_next: bool,
    reprocess_all: bool,
    previous_status_mtime_ns: int | None = None,
    status_mtime_ns: int | None = None,
    force_state_build: bool = False,
    write_health: bool = True,
    refresh_outcomes: bool = True,
    compute_probabilities: bool = False,
    state_read_cache: CurrentDecisionStateReadCache | None = None,
    probability_status_path: Path | None = None,
    outcome_status_path: Path | None = None,
    target_status_path: Path | None = None,
) -> RustNormalizerCycleResult:
    cycle_started = time.perf_counter()
    probability_status_path = probability_status_path or normalized_health_path.with_name(
        "probabilities.json"
    )
    outcome_status_path = outcome_status_path or normalized_health_path.with_name(
        "outcomes.json"
    )
    target_status_path = target_status_path or normalized_health_path.with_name("targets.json")
    if status_mtime_ns is None:
        status_mtime_ns = _file_mtime_ns(status_path)
    build_state = status_mtime_ns is not None and (
        force_state_build
        or reprocess_all
        or status_mtime_ns != previous_status_mtime_ns
    )

    contracts_upserted = 0
    states_written = 0
    probability_outputs_written = 0
    unavailable: tuple[UnavailableDecisionState, ...] = ()
    if build_state:
        try:
            state_result = build_current_decision_state_snapshots(
                status_path=status_path,
                store=store,
                include_next=include_next,
                read_cache=state_read_cache,
            )
        except ValueError as exc:
            unavailable = (_state_build_unavailable(exc),)
        else:
            contracts_upserted = state_result.contracts_upserted
            states_written = state_result.states_written
            unavailable = state_result.unavailable
            if compute_probabilities:
                probability_outputs_written = _compute_probability_outputs(
                    store=store,
                    out_path=probability_status_path,
                )
    market_outcomes_written = (
        _upsert_market_outcomes(
            store=store,
            out_path=outcome_status_path,
        )
        if refresh_outcomes
        else 0
    )
    _write_target_cache_status(
        store=store,
        status_path=status_path,
        out_path=target_status_path,
        asof_ts=datetime.now(timezone.utc),
    )
    state_at = time.perf_counter()

    health_skipped = not write_health
    if write_health:
        write_normalized_health_status(store=store, out_path=normalized_health_path)
    health_at = time.perf_counter()

    return RustNormalizerCycleResult(
        **_idle_normalizer_summary(
            raw_signature=raw_signature,
            raw_summary=raw_summary,
        ),
        contracts_upserted=contracts_upserted,
        states_written=states_written,
        probability_outputs_written=probability_outputs_written,
        market_outcomes_written=market_outcomes_written,
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


def _compute_probability_outputs(*, store: DuckDbIngestStore, out_path: Path) -> int:
    written, skipped, errors = compute_and_persist_probability_outputs(
        store=store,
        limit=PROBABILITY_OUTPUT_LIMIT,
        max_state_age_seconds=PROBABILITY_MAX_STATE_AGE_SECONDS,
        active_only=True,
    )
    with store._connection() as conn:
        rows = latest_probability_output_rows_from_connection(
            conn=conn,
            limit=PROBABILITY_OUTPUT_LIMIT,
            max_state_age_seconds=PROBABILITY_MAX_STATE_AGE_SECONDS,
            active_only=True,
        )
    _write_probability_status(
        out_path=out_path,
        rows=rows,
        skipped=skipped,
        errors=errors,
    )
    if errors:
        print(
            "probability_output_errors " + json.dumps(list(errors), separators=(",", ":")),
            flush=True,
        )
    return written


def _state_build_unavailable(exc: ValueError) -> UnavailableDecisionState:
    return UnavailableDecisionState(
        contract_id="",
        token_id="",
        reason=f"state_build_failed: {exc}",
    )


def _upsert_market_outcomes(*, store: DuckDbIngestStore, out_path: Path) -> int:
    written = upsert_official_market_outcomes(
        store=store,
        asof_ts=datetime.now(timezone.utc),
        market_payload_source=_official_outcome_payload_source_from_env(),
        max_markets=_official_outcome_refresh_limit_from_env(),
        pending_sweep_limit=_official_outcome_pending_sweep_limit_from_env(),
    )
    with store._connection() as conn:
        rows = latest_market_outcome_rows_from_connection(
            conn=conn,
            limit=_outcome_output_limit_from_env(),
        )
    write_outcome_history_status(out_path=out_path, rows=rows)
    return written


def _write_target_cache_status(
    *,
    store: DuckDbIngestStore,
    status_path: Path,
    out_path: Path,
    asof_ts: datetime,
) -> None:
    windows = _target_cache_windows(status_path)
    rows: list[dict[str, Any]] = []
    with store._connection() as conn:
        for window in windows:
            tick = _chainlink_threshold_tick(
                conn=conn,
                symbol=f"{window['asset']}/USD",
                start_ts=window["start_ts"],
                asof_ts=asof_ts,
            )
            rows.append(
                {
                    "asset": window["asset"],
                    "interval": window["interval"],
                    "market_slug": window["market_slug"],
                    "start_ts": window["start_ts"].isoformat(),
                    "expiry_ts": window["expiry_ts"].isoformat(),
                    "threshold_price": tick["price"] if tick is not None else None,
                    "threshold_event_ts": (
                        tick["event_ts"].isoformat() if tick is not None else None
                    ),
                    "threshold_observed_ts": (
                        tick["observed_ts"].isoformat() if tick is not None else None
                    ),
                }
            )
    payload = {
        "schema_version": "polymarket-target-cache-v1",
        "ok": True,
        "state": "OK",
        "generated_at": asof_ts.astimezone(timezone.utc).isoformat(),
        "rows": rows,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(f"{out_path.suffix}.tmp")
    tmp_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    durable_replace(tmp_path, out_path)


def _target_cache_windows(status_path: Path) -> tuple[dict[str, Any], ...]:
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ()
    if not isinstance(payload, dict):
        return ()
    windows: list[dict[str, Any]] = []
    for group in ("current", "next", "next_next"):
        raw_contracts = payload.get(group, ())
        if not isinstance(raw_contracts, list):
            continue
        for raw_contract in raw_contracts:
            if not isinstance(raw_contract, dict):
                continue
            window = raw_contract.get("window")
            if not isinstance(window, dict):
                continue
            asset = str(window.get("asset", "")).strip().upper()
            interval = str(window.get("interval", "")).strip()
            start_ts = _parse_status_timestamp(window.get("start_ts"))
            expiry_ts = _parse_status_timestamp(window.get("end_ts"))
            if not asset or not interval or start_ts is None or expiry_ts is None:
                continue
            windows.append(
                {
                    "asset": asset,
                    "interval": interval,
                    "market_slug": (
                        f"{asset.lower()}-updown-{interval}-{int(start_ts.timestamp())}"
                    ),
                    "start_ts": start_ts,
                    "expiry_ts": expiry_ts,
                }
            )
    return tuple(windows)


def _chainlink_threshold_tick(
    *,
    conn: Any,
    symbol: str,
    start_ts: datetime,
    asof_ts: datetime,
) -> dict[str, Any] | None:
    if asof_ts < start_ts:
        return None
    row = conn.execute(
        """
        select price, event_ts::VARCHAR, observed_ts::VARCHAR
        from core.price_ticks
        where source_key = 'polymarket_rtds_chainlink'
          and symbol = ?
          and event_ts <= ?
          and observed_ts <= ?
        order by event_ts desc, observed_ts desc
        limit 1
        """,
        [symbol, start_ts, asof_ts],
    ).fetchone()
    if row is None:
        return None
    return {
        "price": row[0],
        "event_ts": _parse_status_timestamp(row[1]),
        "observed_ts": _parse_status_timestamp(row[2]),
    }


def _parse_status_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _has_expired_pending_official_outcomes(*, store: DuckDbIngestStore) -> bool:
    with store._connection() as conn:
        row = conn.execute(
            """
            select count(*)
            from validation.market_outcome_history
            where expiry_ts <= ?
              and official_winner is null
              and official_resolution_status = 'pending'
            """,
            [datetime.now(timezone.utc)],
        ).fetchone()
    return bool(row is not None and int(row[0]) > 0)


def _official_outcome_payload_source_from_env() -> PolymarketClobMarketPayloadSource | None:
    source = os.environ.get(OFFICIAL_OUTCOME_SOURCE_ENV, "").strip().lower()
    if source not in {"clob", "polymarket_clob", "polymarket_clob_market"}:
        return None
    return PolymarketClobMarketPayloadSource(
        base_url=os.environ.get(
            "POLYMARKET_CLOB_HTTP_URL",
            "https://clob.polymarket.com",
        ),
        timeout_seconds=float(os.environ.get("POLYMARKET_OFFICIAL_OUTCOME_TIMEOUT_SECONDS", "2.0")),
    )


def _official_outcome_refresh_limit_from_env() -> int | None:
    raw_limit = os.environ.get(OFFICIAL_OUTCOME_REFRESH_LIMIT_ENV)
    if raw_limit is None or raw_limit.strip() == "":
        return OUTCOME_REFRESH_MARKET_LIMIT
    limit = int(raw_limit)
    return limit if limit > 0 else None


def _official_outcome_pending_sweep_limit_from_env() -> int | None:
    raw_limit = os.environ.get(OFFICIAL_OUTCOME_PENDING_SWEEP_LIMIT_ENV)
    if raw_limit is None or raw_limit.strip() == "":
        return OUTCOME_PENDING_SWEEP_LIMIT
    limit = int(raw_limit)
    return limit if limit > 0 else None


def _outcome_output_limit_from_env() -> int:
    raw_limit = os.environ.get(OUTCOME_OUTPUT_LIMIT_ENV)
    if raw_limit is None or raw_limit.strip() == "":
        return OUTCOME_OUTPUT_LIMIT
    return max(20, int(raw_limit))


def _write_probability_status(
    *,
    out_path: Path,
    rows: list[dict[str, Any]],
    skipped: int,
    errors: tuple[str, ...],
) -> None:
    payload = {
        "schema_version": "polymarket-probability-runtime-v1",
        "ok": not errors,
        "state": "OK" if not errors else "PARTIAL",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cached": False,
        "model_version": rows[0]["model_version"] if rows else None,
        "rows": rows,
        "skipped": skipped,
        "errors": list(errors),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(f"{out_path.suffix}.tmp")
    tmp_path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    durable_replace(tmp_path, out_path)


def _status_state_signature(status_path: Path) -> StatusStateSignature | None:
    try:
        with status_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
            mtime_ns = os.fstat(handle.fileno()).st_mtime_ns
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    state_inputs = {
        "schema_version": payload.get("schema_version"),
        "current": payload.get("current", []),
        "next": payload.get("next", []),
        "orderbooks": payload.get("orderbooks", []),
        "chainlink_prices": payload.get("chainlink_prices", []),
        "prices": payload.get("prices", []),
    }
    semantic_payload = json.dumps(
        state_inputs,
        sort_keys=True,
        separators=(",", ":"),
    )
    return StatusStateSignature(
        mtime_ns=mtime_ns,
        semantic_hash=hashlib.sha256(semantic_payload.encode("utf-8")).hexdigest(),
    )


def _status_signature_changed(
    previous: StatusStateSignature | None,
    current: StatusStateSignature | None,
) -> bool:
    if current is None:
        return False
    if previous is None:
        return True
    return current.semantic_hash != previous.semantic_hash


def _raw_tree_idle_summary(
    raw_signature: tuple[RawTreeFileSignature, ...],
) -> RawTreeIdleSummary:
    return RawTreeIdleSummary(
        files=len(raw_signature),
        file_size_bytes=sum(row.size_bytes for row in raw_signature),
    )


def _idle_normalizer_summary(
    raw_signature: tuple[RawTreeFileSignature, ...] = (),
    *,
    raw_summary: RawTreeIdleSummary | None = None,
) -> dict[str, int]:
    if raw_summary is None:
        raw_summary = _raw_tree_idle_summary(raw_signature)
    return {
        "files": raw_summary.files,
        "files_with_rows": 0,
        "files_skipped": raw_summary.files,
        "bytes_read": 0,
        "file_size_bytes": raw_summary.file_size_bytes,
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


def _outcome_refresh_due(
    *,
    last_outcome_refresh_monotonic: float | None,
    cycle_started: float,
    had_pending_outcomes: bool = False,
    interval_seconds: float | None = None,
) -> bool:
    effective_interval_seconds = (
        interval_seconds
        if interval_seconds is not None
        else (
            OUTCOME_PENDING_REFRESH_INTERVAL_SECONDS
            if had_pending_outcomes
            else OUTCOME_REFRESH_INTERVAL_SECONDS
        )
    )
    return (
        last_outcome_refresh_monotonic is None
        or cycle_started - last_outcome_refresh_monotonic >= effective_interval_seconds
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
        f"probability_outputs_written={result.probability_outputs_written} "
        f"market_outcomes_written={result.market_outcomes_written} "
        f"state_skipped={str(result.state_skipped).lower()}"
    )
