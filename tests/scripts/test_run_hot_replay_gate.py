from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType

import duckdb

from polymarket_engine.domain.market_state import PriceObservation
from polymarket_engine.ingestion.rust_event_normalizer import normalize_rust_event_tree
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def _load_script() -> ModuleType:
    script_path = Path(__file__).parents[2] / "scripts" / "run_hot_replay_gate.py"
    spec = importlib.util.spec_from_file_location("run_hot_replay_gate", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_copies_duckdb_with_read_only_attach(tmp_path: Path) -> None:
    source = tmp_path / "source.duckdb"
    snapshot = tmp_path / "snapshots" / "hot_replay_snapshot.duckdb"
    store = DuckDbIngestStore(source)
    store.apply_schema()
    event_ts = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
    store.insert_price_tick(
        PriceObservation(
            source_key="polymarket_rtds_chainlink",
            symbol="BTC/USD",
            event_ts=event_ts,
            observed_ts=event_ts,
            price=70000.0,
        )
    )

    module = _load_script()
    module._copy_duckdb_snapshot(source, snapshot)

    with duckdb.connect(str(snapshot), read_only=True) as conn:
        row = conn.execute(
            """
            select source_key, symbol, price
            from core.price_ticks
            """
        ).fetchone()

    assert row == ("polymarket_rtds_chainlink", "BTC/USD", 70000.0)


def test_gate_runs_verifier_against_snapshot(tmp_path: Path) -> None:
    source = tmp_path / "source.duckdb"
    raw_root = tmp_path / "raw"
    snapshot_dir = tmp_path / "snapshots"
    report_out = tmp_path / "reports" / "hot_decision_replay_report.json"
    store = DuckDbIngestStore(source)
    store.apply_schema()
    start_ts = datetime(2026, 6, 2, 8, 10, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(seconds=72, milliseconds=500)
    settlement_event_ts = asof_ts - timedelta(seconds=2)
    settlement_observed_ts = asof_ts - timedelta(seconds=1)
    book_event_ts = asof_ts - timedelta(seconds=3)
    book_observed_ts = asof_ts - timedelta(milliseconds=500)
    token_id = "up-token"

    _write_jsonl(
        raw_root
        / "polymarket_rtds_chainlink"
        / "price_update"
        / "date=2026-06-02"
        / "hour=08"
        / "events.jsonl",
        [
            {
                "source_key": "polymarket_rtds_chainlink",
                "stream_key": "price_update",
                "symbol": "BTC/USD",
                "event_ts": start_ts.isoformat(),
                "observed_ts": (start_ts + timedelta(milliseconds=100)).isoformat(),
                "payload": {"value": "70000.0"},
            },
            {
                "source_key": "polymarket_rtds_chainlink",
                "stream_key": "price_update",
                "symbol": "BTC/USD",
                "event_ts": settlement_event_ts.isoformat(),
                "observed_ts": settlement_observed_ts.isoformat(),
                "payload": {"value": "70050.0"},
            },
        ],
    )
    _write_jsonl(
        raw_root
        / "polymarket_clob_market_ws"
        / "best_bid_ask"
        / "date=2026-06-02"
        / "hour=08"
        / "events.jsonl",
        [
            {
                "source_key": "polymarket_clob_market_ws",
                "stream_key": "best_bid_ask",
                "symbol": token_id,
                "event_ts": book_event_ts.isoformat(),
                "observed_ts": book_observed_ts.isoformat(),
                "payload": {
                    "contract_id": "0xcondition",
                    "token_id": token_id,
                    "best_bid": "0.61",
                    "best_ask": "0.64",
                    "spread": "0.03",
                },
            }
        ],
    )
    _write_jsonl(
        raw_root
        / "polymarket_decision_state"
        / "hot_state"
        / "date=2026-06-02"
        / "hour=08"
        / "decision-state.jsonl",
        [_hot_row(start_ts, asof_ts)],
    )
    normalize_rust_event_tree(raw_root=raw_root, store=store)
    module = _load_script()

    payload = module.run_gate(
        raw_root=raw_root,
        duckdb_path=source,
        snapshot_dir=snapshot_dir,
        report_out=report_out,
        limit=40,
        scan_limit=5000,
    )

    assert payload["ok"] is True
    assert payload["rows_checked"] == 1
    assert payload["mismatch_count"] == 0
    assert payload["source_duckdb_path"] == str(source)
    assert payload["snapshot_duckdb_path"] == str(
        snapshot_dir / "hot_replay_snapshot.duckdb"
    )
    assert json.loads(report_out.read_text(encoding="utf-8")) == payload


def _hot_row(start_ts: datetime, asof_ts: datetime) -> dict[str, object]:
    return {
        "schema_version": "rust-hot-decision-state-v1",
        "state_id": f"btc-updown-5m-{int(start_ts.timestamp())}:UP:{asof_ts.isoformat()}",
        "trigger_kind": "OrderBookTopOfBook",
        "trigger_symbol": None,
        "trigger_token_id": "up-token",
        "asof_ts": asof_ts.isoformat(),
        "contract": {
            "window": {
                "asset": "BTC",
                "interval": "5m",
                "start_ts": start_ts.isoformat(),
                "end_ts": (start_ts + timedelta(minutes=5)).isoformat(),
            },
            "up": {"asset": "BTC", "side": "Up", "token_id": "up-token"},
            "down": {"asset": "BTC", "side": "Down", "token_id": "down-token"},
        },
        "side": "Up",
        "token_id": "up-token",
        "threshold_price": "70000.0",
        "threshold_event_ts": start_ts.isoformat(),
        "settlement_price": "70050.0",
        "settlement_event_ts": (asof_ts - timedelta(seconds=2)).isoformat(),
        "best_bid": "0.61",
        "best_ask": "0.64",
        "executable_price": "0.64",
        "spread": "0.03",
        "source_age_ms": 1000,
        "book_age_ms": 500,
        "data_quality_flags": [],
        "latency": {
            "trigger_event_to_observed_ms": 2500,
            "observed_to_state_us": 100,
            "state_to_persist_us": None,
            "total_event_to_persist_ms": None,
        },
    }


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{json.dumps(row, sort_keys=True)}\n" for row in rows),
        encoding="utf-8",
    )
