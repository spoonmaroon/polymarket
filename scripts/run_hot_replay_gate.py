from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from polymarket_engine.features.hot_decision_replay import (
    recent_hot_decision_rows,
    replay_ready_hot_decision_rows,
    verify_hot_decision_rows,
)
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def _escape_sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _copy_duckdb_snapshot(source: Path, snapshot: Path) -> None:
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    if snapshot.exists():
        snapshot.unlink()

    source_literal = _escape_sql_string(str(source))
    source_catalog = _quote_identifier("source_db")
    with duckdb.connect(str(snapshot)) as conn:
        row = conn.execute("select current_database()").fetchone()
        if row is None:
            raise RuntimeError("DuckDB did not return current database")
        destination_catalog = _quote_identifier(str(row[0]))
        conn.execute(f"attach {source_literal} as {source_catalog} (read_only)")
        conn.execute(f"copy from database {source_catalog} to {destination_catalog}")


def run_gate(
    *,
    raw_root: Path,
    duckdb_path: Path,
    snapshot_dir: Path,
    report_out: Path,
    limit: int = 40,
    scan_limit: int = 5000,
) -> dict[str, object]:
    snapshot_path = snapshot_dir / "hot_replay_snapshot.duckdb"
    _copy_duckdb_snapshot(duckdb_path, snapshot_path)

    store = DuckDbIngestStore(snapshot_path)
    scanned_rows = recent_hot_decision_rows(raw_root, limit=scan_limit)
    selection = replay_ready_hot_decision_rows(
        rows=scanned_rows,
        store=store,
        limit=limit,
    )
    result = verify_hot_decision_rows(rows=selection.rows, store=store)
    payload: dict[str, object] = {
        "ok": result.ok and result.rows_checked > 0,
        "source_duckdb_path": str(duckdb_path),
        "snapshot_duckdb_path": str(snapshot_path),
        "raw_root": str(raw_root),
        "rows_scanned": selection.rows_scanned,
        "rows_checked": result.rows_checked,
        "rows_skipped_not_replay_ready": selection.rows_skipped_not_replay_ready,
        "rows_skipped_quality_blocked": selection.rows_skipped_quality_blocked,
        "rows_skipped_not_replay_ready_by_reason": selection.rows_skipped_not_replay_ready_by_reason,
        "rows_skipped_quality_blocked_by_reason": selection.rows_skipped_quality_blocked_by_reason,
        "price_observed_watermark": _isoformat_optional(selection.price_observed_watermark),
        "orderbook_observed_watermark": _isoformat_optional(selection.orderbook_observed_watermark),
        "mismatch_count": len(result.mismatches),
        "mismatches": [
            {
                "state_id": mismatch.state_id,
                "field": mismatch.field,
                "hot_value": mismatch.hot_value,
                "replay_value": mismatch.replay_value,
            }
            for mismatch in result.mismatches
        ],
    }
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _isoformat_optional(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy the live DuckDB into a snapshot and verify hot decision replay."
    )
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--duckdb-path", type=Path, required=True)
    parser.add_argument("--snapshot-dir", type=Path, required=True)
    parser.add_argument("--report-out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--scan-limit", type=int, default=5000)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    payload = run_gate(
        raw_root=args.raw_root,
        duckdb_path=args.duckdb_path,
        snapshot_dir=args.snapshot_dir,
        report_out=args.report_out,
        limit=args.limit,
        scan_limit=args.scan_limit,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["ok"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
