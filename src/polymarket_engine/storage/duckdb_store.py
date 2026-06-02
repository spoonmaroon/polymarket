from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.contract_rules import NormalizedContractRule
from polymarket_engine.domain.market_state import DecisionState, OrderBookObservation, PriceObservation
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput
from polymarket_engine.storage.retention import RAW_HOT_RETENTION_DAYS, retention_manifest_class


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _strict_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class DuckDbIngestStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def apply_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path = Path(__file__).with_name("schema.sql")
        with duckdb.connect(str(self.db_path)) as conn:
            _drop_incompatible_tables(conn)
            conn.sql(schema_path.read_text())

    def register_ingest_file(
        self,
        file_id: str,
        source_key: str,
        stream_key: str,
        partition_date: str,
        partition_hour: int,
        path: str,
        sha256: str,
        row_count: int,
        first_event_ts: datetime,
        last_event_ts: datetime,
    ) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into ops.ingest_files
                (file_id, source_key, stream_key, partition_date, partition_hour, path, sha256,
                 row_count, first_event_ts, last_event_ts, written_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    file_id,
                    source_key,
                    stream_key,
                    partition_date,
                    partition_hour,
                    path,
                    sha256,
                    row_count,
                    first_event_ts,
                    last_event_ts,
                    datetime.now(timezone.utc),
                ],
            )
            conn.execute(
                """
                insert or replace into ops.retention_manifests
                (manifest_id, file_id, source_key, stream_key, partition_date, partition_hour,
                 path, sha256, row_count, first_event_ts, last_event_ts, retention_class,
                 archive_after_days, delete_after_days, archived_at, deleted_at, recorded_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    f"{file_id}:{retention_manifest_class('raw')}",
                    file_id,
                    source_key,
                    stream_key,
                    partition_date,
                    partition_hour,
                    path,
                    sha256,
                    row_count,
                    first_event_ts,
                    last_event_ts,
                    retention_manifest_class("raw"),
                    RAW_HOT_RETENTION_DAYS,
                    None,
                    None,
                    None,
                    datetime.now(timezone.utc),
                ],
            )

    def raw_file_checkpoint(self, path: Path) -> int | None:
        with duckdb.connect(str(self.db_path)) as conn:
            row = conn.execute(
                """
                select byte_offset
                from ops.raw_file_checkpoints
                where path = ?
                """,
                [str(path)],
            ).fetchone()
        return int(row[0]) if row is not None else None

    def upsert_raw_file_checkpoint(
        self,
        *,
        path: Path,
        source_key: str,
        stream_key: str,
        byte_offset: int,
        file_size_bytes: int,
        rows_read: int,
        first_event_ts: datetime | None,
        last_event_ts: datetime | None,
    ) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into ops.raw_file_checkpoints
                (path, source_key, stream_key, byte_offset, file_size_bytes, rows_read,
                 first_event_ts, last_event_ts, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    str(path),
                    source_key,
                    stream_key,
                    byte_offset,
                    file_size_bytes,
                    rows_read,
                    first_event_ts,
                    last_event_ts,
                    datetime.now(timezone.utc),
                ],
            )

    def upsert_contract_rule(self, rule: NormalizedContractRule) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into core.contract_rules
                (market_id, condition_id, slug, asset, contract_type, start_ts, end_ts,
                 expiry_ts, threshold_type, threshold_price, comparison_operator_up,
                 comparison_operator_down, settlement_source_name, settlement_source_url,
                 settlement_symbol, outcome_token_ids_json, rule_text, rule_hash,
                 parser_version, accepted, reject_reason, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    rule.market_id,
                    rule.condition_id,
                    rule.slug,
                    rule.asset,
                    rule.contract_type,
                    rule.start_ts,
                    rule.end_ts,
                    rule.expiry_ts,
                    rule.threshold_type,
                    rule.threshold_price,
                    rule.comparison_operator_up,
                    rule.comparison_operator_down,
                    rule.settlement_source_name,
                    rule.settlement_source_url,
                    rule.settlement_symbol,
                    json.dumps(rule.outcome_token_ids, sort_keys=True),
                    rule.rule_text,
                    rule.rule_hash,
                    rule.parser_version,
                    rule.accepted,
                    rule.reject_reason,
                    datetime.now(timezone.utc),
                ],
            )

    def upsert_contract_spec(self, contract: ContractSpec) -> None:
        now = datetime.now(timezone.utc)
        with duckdb.connect(str(self.db_path)) as conn:
            existing = conn.execute(
                "select first_seen_ts from core.contracts where contract_id = ?",
                [contract.contract_id],
            ).fetchone()
            first_seen_ts = now if existing is None else existing[0]
            conn.execute(
                """
                insert or replace into core.contracts
                (contract_id, venue, market_id, condition_id, slug, asset, side, token_id,
                 threshold_type, threshold_price, comparison_operator, start_ts, expiry_ts,
                 settlement_source_name, settlement_source_url, settlement_symbol, rule_text,
                 rule_hash, parser_version, first_seen_ts, last_seen_ts)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    contract.contract_id,
                    contract.venue,
                    contract.market_id,
                    contract.condition_id,
                    contract.slug,
                    contract.asset,
                    contract.side,
                    contract.token_id,
                    contract.threshold_type,
                    contract.threshold_price,
                    contract.comparison_operator,
                    contract.start_ts,
                    contract.expiry_ts,
                    contract.settlement_source_name,
                    contract.settlement_source_url,
                    contract.settlement_symbol,
                    contract.rule_text,
                    contract.rule_hash,
                    contract.parser_version,
                    first_seen_ts,
                    now,
                ],
            )

    def insert_price_tick(self, tick: PriceObservation, raw_file_id: str | None = None) -> None:
        self.insert_price_ticks((tick,), raw_file_id=raw_file_id)

    def insert_price_ticks(
        self,
        ticks: Sequence[PriceObservation],
        raw_file_id: str | None = None,
    ) -> None:
        if not ticks:
            return
        frame = pl.DataFrame(
            {
                "source_key": [tick.source_key for tick in ticks],
                "symbol": [tick.symbol for tick in ticks],
                "event_ts": [tick.event_ts for tick in ticks],
                "observed_ts": [tick.observed_ts for tick in ticks],
                "price": [tick.price for tick in ticks],
                "bid": [tick.bid for tick in ticks],
                "ask": [tick.ask for tick in ticks],
                "sequence": [tick.sequence for tick in ticks],
                "raw_file_id": [raw_file_id for _ in ticks],
            }
        )
        with duckdb.connect(str(self.db_path)) as conn:
            conn.register("price_tick_rows", frame)
            conn.execute(
                """
                insert or replace into core.price_ticks
                (source_key, symbol, event_ts, observed_ts, price, bid, ask, sequence, raw_file_id)
                select source_key, symbol, event_ts, observed_ts, price, bid, ask, sequence, raw_file_id
                from price_tick_rows
                """
            )

    def insert_orderbook_snapshot(
        self,
        snapshot: OrderBookObservation,
        raw_file_id: str | None = None,
    ) -> None:
        self.insert_orderbook_snapshots((snapshot,), raw_file_id=raw_file_id)

    def insert_orderbook_snapshots(
        self,
        snapshots: Sequence[OrderBookObservation],
        raw_file_id: str | None = None,
    ) -> None:
        if not snapshots:
            return
        frame = pl.DataFrame(
            {
                "venue": [snapshot.venue for snapshot in snapshots],
                "contract_id": [snapshot.contract_id for snapshot in snapshots],
                "token_id": [snapshot.token_id for snapshot in snapshots],
                "event_ts": [snapshot.event_ts for snapshot in snapshots],
                "observed_ts": [snapshot.observed_ts for snapshot in snapshots],
                "best_bid": [snapshot.best_bid for snapshot in snapshots],
                "best_ask": [snapshot.best_ask for snapshot in snapshots],
                "bid_size_top": [snapshot.bid_size_top for snapshot in snapshots],
                "ask_size_top": [snapshot.ask_size_top for snapshot in snapshots],
                "spread": [snapshot.spread for snapshot in snapshots],
                "depth_json": [snapshot.depth_json for snapshot in snapshots],
                "raw_file_id": [raw_file_id for _ in snapshots],
            }
        )
        with duckdb.connect(str(self.db_path)) as conn:
            conn.register("orderbook_snapshot_rows", frame)
            conn.execute(
                """
                insert or replace into core.orderbook_snapshots
                (venue, contract_id, token_id, event_ts, observed_ts, best_bid, best_ask,
                 bid_size_top, ask_size_top, spread, depth_json, raw_file_id)
                select venue, contract_id, token_id, event_ts, observed_ts, best_bid, best_ask,
                       bid_size_top, ask_size_top, spread, depth_json, raw_file_id
                from orderbook_snapshot_rows
                """
            )

    def upsert_asof_state_input(self, state: DecisionState) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into features.asof_state_inputs
                (state_id, contract_id, asof_ts, asset, side, threshold,
                 threshold_source_key, threshold_event_ts, threshold_observed_ts,
                 seconds_left, settlement_price, settlement_source_key, settlement_event_ts,
                 settlement_observed_ts, proxy_prices_json,
                 source_disagreement_bps, best_bid, best_ask, executable_price, spread,
                 book_event_ts, book_observed_ts, quote_age_ms, source_age_ms,
                 source_observed_lag_ms, book_age_ms, book_observed_lag_ms,
                 realized_returns_json, short_realized_vol, medium_realized_vol,
                 long_realized_vol, sigma_tau, volatility_regime, data_quality_flags_json,
                 created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    state.state_id,
                    state.contract.contract_id,
                    state.asof_ts,
                    state.contract.asset,
                    state.contract.side,
                    state.threshold,
                    state.threshold_source_key,
                    state.threshold_event_ts,
                    state.threshold_observed_ts,
                    state.seconds_left,
                    state.settlement_price,
                    state.settlement_source_key,
                    state.settlement_event_ts,
                    state.settlement_observed_ts,
                    _json(state.proxy_prices),
                    state.source_disagreement_bps,
                    state.best_bid,
                    state.best_ask,
                    state.executable_price,
                    state.spread,
                    state.book_event_ts,
                    state.book_observed_ts,
                    state.quote_age_ms,
                    state.source_age_ms,
                    state.source_observed_lag_ms,
                    state.book_age_ms,
                    state.book_observed_lag_ms,
                    _json(list(state.realized_returns)),
                    state.short_realized_vol,
                    state.medium_realized_vol,
                    state.long_realized_vol,
                    state.sigma_tau,
                    state.volatility_regime,
                    _json(list(state.data_quality_flags)),
                    datetime.now(timezone.utc),
                ],
            )

    def insert_decision_snapshot(
        self,
        *,
        decision_id: str,
        state: DecisionState,
        model: dict[str, object],
        decision: str,
        block_reason: str | None,
    ) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into features.decision_snapshots
                (decision_id, state_id, contract_id, asof_ts, market_id, token_id,
                 state_json, model_json, decision, block_reason, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    decision_id,
                    state.state_id,
                    state.contract.contract_id,
                    state.asof_ts,
                    state.contract.market_id,
                    state.contract.token_id,
                    _json(state.to_json_dict()),
                    _json(model),
                    decision,
                    block_reason,
                    datetime.now(timezone.utc),
                ],
            )

    def insert_decision_label(
        self,
        *,
        decision_id: str,
        contract_id: str,
        expiry_ts: datetime,
        settlement_price: float,
        did_finish_win: bool,
        did_no_touch: bool,
        realized_edge: float | None,
        label_source: str,
    ) -> None:
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into validation.decision_labels
                (decision_id, contract_id, expiry_ts, settlement_price, did_finish_win,
                 did_no_touch, realized_edge, label_source, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    decision_id,
                    contract_id,
                    expiry_ts,
                    settlement_price,
                    did_finish_win,
                    did_no_touch,
                    realized_edge,
                    label_source,
                    datetime.now(timezone.utc),
                ],
            )

    def insert_probability_output(
        self,
        *,
        output_id: str,
        probability_input: ProbabilityInput,
        output: ProbabilityOutput,
    ) -> None:
        if output.state_id != probability_input.state_id:
            raise ValueError("output state_id must match probability_input state_id")
        if output.asof_ts != probability_input.asof_ts:
            raise ValueError("output asof_ts must match probability_input asof_ts")
        with duckdb.connect(str(self.db_path)) as conn:
            conn.execute(
                """
                insert or replace into features.probability_outputs
                (output_id, state_id, asof_ts, model_version, p_finish, p_no_touch,
                 z_path, seed, input_json, output_json, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    output_id,
                    output.state_id,
                    output.asof_ts,
                    output.model_version,
                    output.p_finish,
                    output.p_no_touch,
                    output.z_path,
                    output.seed,
                    _strict_json(probability_input.to_json_dict()),
                    _strict_json(output.to_json_dict()),
                    datetime.now(timezone.utc),
                ],
            )

    def normalized_table_health(self) -> tuple[dict[str, object], ...]:
        checks = (
            ("core.contracts", "last_seen_ts"),
            ("core.contract_rules", "updated_at"),
            ("core.price_ticks", "observed_ts"),
            ("core.orderbook_snapshots", "observed_ts"),
            ("features.asof_state_inputs", "created_at"),
            ("features.decision_snapshots", "created_at"),
            ("features.probability_outputs", "created_at"),
        )
        rows: list[dict[str, object]] = []
        with duckdb.connect(str(self.db_path)) as conn:
            for table_name, latest_column in checks:
                row = conn.execute(
                    f"select count(*) as rows, max({latest_column}) from {table_name}"
                ).fetchone()
                if row is None:
                    count, latest_ts = 0, None
                else:
                    count, latest_ts = row
                rows.append(
                    {
                        "table": table_name,
                        "rows": int(count),
                        "latest_ts": _isoformat_utc(latest_ts),
                    }
                )
        return tuple(rows)

    def latest_price_tick(
        self,
        *,
        source_key: str,
        symbol: str,
        asof_ts: datetime,
    ) -> PriceObservation | None:
        return self.latest_price_tick_before(
            source_key=source_key,
            symbol=symbol,
            event_ts_lte=asof_ts,
            observed_ts_lte=asof_ts,
        )

    def latest_price_tick_before(
        self,
        *,
        source_key: str,
        symbol: str,
        event_ts_lte: datetime,
        observed_ts_lte: datetime,
    ) -> PriceObservation | None:
        with duckdb.connect(str(self.db_path)) as conn:
            row = conn.execute(
                """
                select source_key, symbol, event_ts::VARCHAR, observed_ts::VARCHAR,
                       price, bid, ask, sequence
                from core.price_ticks
                where source_key = ?
                  and symbol = ?
                  and event_ts <= ?
                  and observed_ts <= ?
                order by event_ts desc, observed_ts desc
                limit 1
                """,
                [source_key, symbol, event_ts_lte, observed_ts_lte],
            ).fetchone()
        if row is None:
            return None
        return PriceObservation(
            source_key=row[0],
            symbol=row[1],
            event_ts=_parse_duckdb_timestamptz(row[2]),
            observed_ts=_parse_duckdb_timestamptz(row[3]),
            price=row[4],
            bid=row[5],
            ask=row[6],
            sequence=row[7],
        )

    def price_ticks_before(
        self,
        *,
        source_key: str,
        symbol: str,
        asof_ts: datetime,
        limit: int,
    ) -> tuple[PriceObservation, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        with duckdb.connect(str(self.db_path)) as conn:
            rows = conn.execute(
                """
                select source_key, symbol, event_ts::VARCHAR, observed_ts::VARCHAR,
                       price, bid, ask, sequence
                from (
                    select source_key, symbol, event_ts, observed_ts, price, bid, ask, sequence
                    from core.price_ticks
                    where source_key = ?
                      and symbol = ?
                      and event_ts <= ?
                      and observed_ts <= ?
                    order by event_ts desc, observed_ts desc
                    limit ?
                ) as latest_ticks
                order by event_ts asc, observed_ts asc
                """,
                [source_key, symbol, asof_ts, asof_ts, limit],
            ).fetchall()
        return tuple(
            PriceObservation(
                source_key=row[0],
                symbol=row[1],
                event_ts=_parse_duckdb_timestamptz(row[2]),
                observed_ts=_parse_duckdb_timestamptz(row[3]),
                price=row[4],
                bid=row[5],
                ask=row[6],
                sequence=row[7],
            )
            for row in rows
        )

    def latest_orderbook_snapshot(
        self,
        *,
        venue: str,
        token_id: str,
        asof_ts: datetime,
    ) -> OrderBookObservation | None:
        with duckdb.connect(str(self.db_path)) as conn:
            row = conn.execute(
                """
                select venue, contract_id, token_id, event_ts::VARCHAR, observed_ts::VARCHAR,
                       best_bid, best_ask, bid_size_top, ask_size_top, spread, depth_json
                from core.orderbook_snapshots
                where venue = ?
                  and token_id = ?
                  and event_ts <= ?
                  and observed_ts <= ?
                order by event_ts desc, observed_ts desc
                limit 1
                """,
                [venue, token_id, asof_ts, asof_ts],
            ).fetchone()
        if row is None:
            return None
        return OrderBookObservation(
            venue=row[0],
            contract_id=row[1],
            token_id=row[2],
            event_ts=_parse_duckdb_timestamptz(row[3]),
            observed_ts=_parse_duckdb_timestamptz(row[4]),
            best_bid=row[5],
            best_ask=row[6],
            bid_size_top=row[7],
            ask_size_top=row[8],
            spread=row[9],
            depth_json=row[10],
        )


def _parse_duckdb_timestamptz(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace(" ", "T"))
    return parsed.astimezone(timezone.utc)


def _isoformat_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _drop_incompatible_tables(conn: duckdb.DuckDBPyConnection) -> None:
    required_columns = {
        ("core", "contracts"): {
            "market_id",
            "threshold_type",
            "comparison_operator",
            "settlement_source_name",
            "parser_version",
        },
        ("features", "asof_state_inputs"): {
            "threshold_source_key",
            "settlement_event_ts",
            "book_event_ts",
            "source_observed_lag_ms",
            "book_observed_lag_ms",
        },
    }
    for (schema_name, table_name), columns in required_columns.items():
        existing = {
            str(row[0])
            for row in conn.execute(
                """
                select column_name
                from information_schema.columns
                where table_schema = ? and table_name = ?
                """,
                [schema_name, table_name],
            ).fetchall()
        }
        if existing and not columns.issubset(existing):
            conn.execute(f"drop table {schema_name}.{table_name}")
