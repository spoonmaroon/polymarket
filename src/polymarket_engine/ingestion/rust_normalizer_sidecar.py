from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polymarket_engine.features.rust_decision_snapshots import (
    UnavailableDecisionState,
    build_current_decision_state_snapshots,
)
from polymarket_engine.health.normalized_status import write_normalized_health_status
from polymarket_engine.ingestion.rust_event_normalizer import (
    RustEventNormalizeResult,
    normalize_rust_event_tree,
)
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


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
    unavailable: tuple[UnavailableDecisionState, ...]
    elapsed_ms: int
    normalize_ms: int
    state_ms: int
    health_ms: int

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

    contracts_upserted = 0
    states_written = 0
    unavailable: tuple[UnavailableDecisionState, ...] = ()
    if status_path.exists():
        state_result = build_current_decision_state_snapshots(
            status_path=status_path,
            store=store,
            include_next=include_next,
        )
        contracts_upserted = state_result.contracts_upserted
        states_written = state_result.states_written
        unavailable = state_result.unavailable
    state_at = time.perf_counter()

    write_normalized_health_status(store=store, out_path=normalized_health_path)
    health_at = time.perf_counter()

    return RustNormalizerCycleResult(
        **_normalizer_summary(results),
        contracts_upserted=contracts_upserted,
        states_written=states_written,
        unavailable=unavailable,
        elapsed_ms=_elapsed_ms(cycle_started, health_at),
        normalize_ms=_elapsed_ms(cycle_started, normalized_at),
        state_ms=_elapsed_ms(normalized_at, state_at),
        health_ms=_elapsed_ms(state_at, health_at),
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
        while True:
            result = _run_rust_normalizer_cycle_with_store(
                raw_root=raw_root,
                store=store,
                status_path=status_path,
                normalized_health_path=normalized_health_path,
                include_next=include_next,
                reprocess_all=reprocess_all,
                apply_schema=False,
            )
            print(_cycle_log_line(result), flush=True)
            cycles_run += 1
            if max_cycles is not None and cycles_run >= max_cycles:
                return
            time.sleep(interval_seconds)


def _normalizer_summary(results: tuple[RustEventNormalizeResult, ...]) -> dict[str, int]:
    return {
        "files": len(results),
        "files_with_rows": sum(1 for result in results if result.rows_read > 0),
        "files_skipped": sum(1 for result in results if result.rows_read == 0),
        "bytes_read": sum(result.end_byte_offset - result.start_byte_offset for result in results),
        "file_size_bytes": sum(result.file_size_bytes for result in results),
        "rows_read": sum(result.rows_read for result in results),
        "price_ticks_written": sum(result.price_ticks_written for result in results),
        "orderbooks_written": sum(result.orderbooks_written for result in results),
    }


def _elapsed_ms(start: float, end: float) -> int:
    return max(0, int(round((end - start) * 1000)))


def _cycle_log_line(result: RustNormalizerCycleResult) -> str:
    return (
        "normalizer_cycle "
        f"elapsed_ms={result.elapsed_ms} "
        f"normalize_ms={result.normalize_ms} "
        f"state_ms={result.state_ms} "
        f"health_ms={result.health_ms} "
        f"files={result.files} "
        f"rows_read={result.rows_read} "
        f"bytes_read={result.bytes_read}"
    )
