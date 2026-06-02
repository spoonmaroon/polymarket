from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
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


def _contract_specs_signature(contracts: Sequence[ContractSpec]) -> tuple[tuple[object, ...], ...]:
    rows = tuple(
        (
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
        )
        for contract in contracts
    )
    return tuple(sorted(rows, key=lambda row: (str(row[0]), repr(row))))


@dataclass
class OrderBookSnapshotBatch:
    venues: list[str] = field(default_factory=list)
    contract_ids: list[str] = field(default_factory=list)
    token_ids: list[str] = field(default_factory=list)
    event_timestamps: list[datetime | str] = field(default_factory=list)
    observed_timestamps: list[datetime | str] = field(default_factory=list)
    best_bids: list[float | None] = field(default_factory=list)
    best_asks: list[float | None] = field(default_factory=list)
    bid_sizes_top: list[float | None] = field(default_factory=list)
    ask_sizes_top: list[float | None] = field(default_factory=list)
    spreads: list[float | None] = field(default_factory=list)
    depth_json_values: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.venues)

    def append(
        self,
        *,
        venue: str,
        contract_id: str,
        token_id: str,
        event_ts: datetime | str,
        observed_ts: datetime | str,
        best_bid: float | None,
        best_ask: float | None,
        bid_size_top: float | None,
        ask_size_top: float | None,
        spread: float | None,
        depth_json: str,
    ) -> None:
        self.venues.append(venue)
        self.contract_ids.append(contract_id)
        self.token_ids.append(token_id)
        self.event_timestamps.append(event_ts)
        self.observed_timestamps.append(observed_ts)
        self.best_bids.append(best_bid)
        self.best_asks.append(best_ask)
        self.bid_sizes_top.append(bid_size_top)
        self.ask_sizes_top.append(ask_size_top)
        self.spreads.append(spread)
        self.depth_json_values.append(depth_json)

    def append_observation(self, snapshot: OrderBookObservation) -> None:
        self.append(
            venue=snapshot.venue,
            contract_id=snapshot.contract_id,
            token_id=snapshot.token_id,
            event_ts=snapshot.event_ts,
            observed_ts=snapshot.observed_ts,
            best_bid=snapshot.best_bid,
            best_ask=snapshot.best_ask,
            bid_size_top=snapshot.bid_size_top,
            ask_size_top=snapshot.ask_size_top,
            spread=snapshot.spread,
            depth_json=snapshot.depth_json,
        )


class DuckDbIngestStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None
        self._last_contract_specs_signature: tuple[tuple[object, ...], ...] | None = None

    def __enter__(self) -> DuckDbIngestStore:
        if self._conn is None:
            self._conn = duckdb.connect(str(self.db_path))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def _connection(self) -> Iterator[duckdb.DuckDBPyConnection]:
        if self._conn is not None:
            yield self._conn
            return
        with duckdb.connect(str(self.db_path)) as conn:
            yield conn

    def apply_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        schema_path = Path(__file__).with_name("schema.sql")
        with self._connection() as conn:
            _drop_incompatible_tables(conn)
            conn.sql(schema_path.read_text())
        self._last_contract_specs_signature = None

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
        with self._connection() as conn:
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
        with self._connection() as conn:
            row = conn.execute(
                """
                select byte_offset
                from ops.raw_file_checkpoints
                where path = ?
                """,
                [str(path)],
            ).fetchone()
        return int(row[0]) if row is not None else None

    def raw_file_checkpoints(self, paths: Sequence[Path]) -> dict[Path, int]:
        path_by_value = {str(path): path for path in paths}
        if not path_by_value:
            return {}
        paths_frame = pl.DataFrame({"path": list(path_by_value)})
        with self._connection() as conn:
            conn.register("raw_checkpoint_paths", paths_frame)
            rows = conn.execute(
                """
                select checkpoints.path, checkpoints.byte_offset
                from ops.raw_file_checkpoints as checkpoints
                join raw_checkpoint_paths as paths using (path)
                """
            ).fetchall()
        return {
            path_by_value[str(row[0])]: int(row[1])
            for row in rows
            if str(row[0]) in path_by_value
        }

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
        with self._connection() as conn:
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
        self.upsert_contract_rules((rule,))

    def upsert_contract_rules(self, rules: Sequence[NormalizedContractRule]) -> None:
        if not rules:
            return
        now = datetime.now(timezone.utc)
        rows_frame = pl.DataFrame(
            {
                "market_id": [rule.market_id for rule in rules],
                "condition_id": [rule.condition_id for rule in rules],
                "slug": [rule.slug for rule in rules],
                "asset": [rule.asset for rule in rules],
                "contract_type": [rule.contract_type for rule in rules],
                "start_ts": [rule.start_ts for rule in rules],
                "end_ts": [rule.end_ts for rule in rules],
                "expiry_ts": [rule.expiry_ts for rule in rules],
                "threshold_type": [rule.threshold_type for rule in rules],
                "threshold_price": [rule.threshold_price for rule in rules],
                "comparison_operator_up": [rule.comparison_operator_up for rule in rules],
                "comparison_operator_down": [
                    rule.comparison_operator_down for rule in rules
                ],
                "settlement_source_name": [rule.settlement_source_name for rule in rules],
                "settlement_source_url": [rule.settlement_source_url for rule in rules],
                "settlement_symbol": [rule.settlement_symbol for rule in rules],
                "outcome_token_ids_json": [
                    json.dumps(rule.outcome_token_ids, sort_keys=True) for rule in rules
                ],
                "rule_text": [rule.rule_text for rule in rules],
                "rule_hash": [rule.rule_hash for rule in rules],
                "parser_version": [rule.parser_version for rule in rules],
                "accepted": [rule.accepted for rule in rules],
                "reject_reason": [rule.reject_reason for rule in rules],
                "updated_at": [now for _ in rules],
            }
        )
        with self._connection() as conn:
            conn.register("contract_rule_rows", rows_frame)
            conn.execute(
                """
                insert or replace into core.contract_rules
                (market_id, condition_id, slug, asset, contract_type, start_ts, end_ts,
                 expiry_ts, threshold_type, threshold_price, comparison_operator_up,
                 comparison_operator_down, settlement_source_name, settlement_source_url,
                 settlement_symbol, outcome_token_ids_json, rule_text, rule_hash,
                 parser_version, accepted, reject_reason, updated_at)
                select market_id, condition_id, slug, asset, contract_type, start_ts, end_ts,
                       expiry_ts, threshold_type, threshold_price, comparison_operator_up,
                       comparison_operator_down, settlement_source_name, settlement_source_url,
                       settlement_symbol, outcome_token_ids_json, rule_text, rule_hash,
                       parser_version, accepted, reject_reason, updated_at
                from contract_rule_rows
                """,
            )

    def upsert_contract_spec(self, contract: ContractSpec) -> None:
        self.upsert_contract_specs((contract,))

    def upsert_contract_specs(self, contracts: Sequence[ContractSpec]) -> None:
        if not contracts:
            return
        signature = _contract_specs_signature(contracts)
        if signature == self._last_contract_specs_signature:
            return
        now = datetime.now(timezone.utc)
        ids_frame = pl.DataFrame({"contract_id": [contract.contract_id for contract in contracts]})
        with self._connection() as conn:
            conn.register("contract_spec_ids", ids_frame)
            existing = {
                row[0]: row[1]
                for row in conn.execute(
                    """
                    select contracts.contract_id, contracts.first_seen_ts
                    from core.contracts as contracts
                    join contract_spec_ids as ids using (contract_id)
                    """
                ).fetchall()
            }
            rows_frame = pl.DataFrame(
                {
                    "contract_id": [contract.contract_id for contract in contracts],
                    "venue": [contract.venue for contract in contracts],
                    "market_id": [contract.market_id for contract in contracts],
                    "condition_id": [contract.condition_id for contract in contracts],
                    "slug": [contract.slug for contract in contracts],
                    "asset": [contract.asset for contract in contracts],
                    "side": [contract.side for contract in contracts],
                    "token_id": [contract.token_id for contract in contracts],
                    "threshold_type": [contract.threshold_type for contract in contracts],
                    "threshold_price": [contract.threshold_price for contract in contracts],
                    "comparison_operator": [
                        contract.comparison_operator for contract in contracts
                    ],
                    "start_ts": [contract.start_ts for contract in contracts],
                    "expiry_ts": [contract.expiry_ts for contract in contracts],
                    "settlement_source_name": [
                        contract.settlement_source_name for contract in contracts
                    ],
                    "settlement_source_url": [
                        contract.settlement_source_url for contract in contracts
                    ],
                    "settlement_symbol": [contract.settlement_symbol for contract in contracts],
                    "rule_text": [contract.rule_text for contract in contracts],
                    "rule_hash": [contract.rule_hash for contract in contracts],
                    "parser_version": [contract.parser_version for contract in contracts],
                    "first_seen_ts": [
                        existing.get(contract.contract_id, now) for contract in contracts
                    ],
                    "last_seen_ts": [now for _ in contracts],
                }
            )
            conn.register("contract_spec_rows", rows_frame)
            conn.execute(
                """
                insert or replace into core.contracts
                (contract_id, venue, market_id, condition_id, slug, asset, side, token_id,
                 threshold_type, threshold_price, comparison_operator, start_ts, expiry_ts,
                 settlement_source_name, settlement_source_url, settlement_symbol, rule_text,
                 rule_hash, parser_version, first_seen_ts, last_seen_ts)
                select contract_id, venue, market_id, condition_id, slug, asset, side, token_id,
                       threshold_type, threshold_price, comparison_operator, start_ts, expiry_ts,
                       settlement_source_name, settlement_source_url, settlement_symbol, rule_text,
                       rule_hash, parser_version, first_seen_ts, last_seen_ts
                from contract_spec_rows
                """,
            )
        self._last_contract_specs_signature = signature

    def insert_price_tick(self, tick: PriceObservation, raw_file_id: str | None = None) -> None:
        self.insert_price_ticks((tick,), raw_file_id=raw_file_id)

    def insert_price_ticks(
        self,
        ticks: Sequence[PriceObservation],
        raw_file_id: str | None = None,
    ) -> None:
        if not ticks:
            return
        source_keys: list[str] = []
        symbols: list[str] = []
        event_timestamps: list[datetime] = []
        observed_timestamps: list[datetime] = []
        prices: list[float] = []
        bids: list[float | None] = []
        asks: list[float | None] = []
        sequences: list[str | None] = []
        raw_file_ids: list[str | None] = []

        for tick in ticks:
            source_keys.append(tick.source_key)
            symbols.append(tick.symbol)
            event_timestamps.append(tick.event_ts)
            observed_timestamps.append(tick.observed_ts)
            prices.append(tick.price)
            bids.append(tick.bid)
            asks.append(tick.ask)
            sequences.append(tick.sequence)
            raw_file_ids.append(raw_file_id)

        frame = pl.DataFrame(
            {
                "source_key": source_keys,
                "symbol": symbols,
                "event_ts": event_timestamps,
                "observed_ts": observed_timestamps,
                "price": prices,
                "bid": bids,
                "ask": asks,
                "sequence": sequences,
                "raw_file_id": raw_file_ids,
            }
        )
        with self._connection() as conn:
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
        batch = OrderBookSnapshotBatch()
        for snapshot in snapshots:
            batch.append_observation(snapshot)
        self.insert_orderbook_snapshot_batch(batch, raw_file_id=raw_file_id)

    def insert_orderbook_snapshot_batch(
        self,
        batch: OrderBookSnapshotBatch,
        raw_file_id: str | None = None,
    ) -> None:
        if not batch:
            return
        frame = pl.DataFrame(
            {
                "venue": batch.venues,
                "contract_id": batch.contract_ids,
                "token_id": batch.token_ids,
                "event_ts": batch.event_timestamps,
                "observed_ts": batch.observed_timestamps,
                "best_bid": batch.best_bids,
                "best_ask": batch.best_asks,
                "bid_size_top": batch.bid_sizes_top,
                "ask_size_top": batch.ask_sizes_top,
                "spread": batch.spreads,
                "depth_json": batch.depth_json_values,
                "raw_file_id": [raw_file_id] * len(batch),
            }
        )
        with self._connection() as conn:
            conn.register("orderbook_snapshot_rows", frame)
            conn.execute(
                """
                insert or replace into core.orderbook_snapshots
                (venue, contract_id, token_id, event_ts, observed_ts, best_bid, best_ask,
                 bid_size_top, ask_size_top, spread, depth_json, raw_file_id)
                select venue, contract_id, token_id, event_ts::TIMESTAMPTZ, observed_ts::TIMESTAMPTZ, best_bid, best_ask,
                       bid_size_top, ask_size_top, spread, depth_json, raw_file_id
                from orderbook_snapshot_rows
                """
            )

    def upsert_asof_state_input(self, state: DecisionState) -> None:
        self.upsert_asof_state_inputs((state,))

    def upsert_asof_state_inputs(self, states: Sequence[DecisionState]) -> None:
        if not states:
            return
        now = datetime.now(timezone.utc)
        frame = pl.DataFrame(
            {
                "state_id": [state.state_id for state in states],
                "contract_id": [state.contract.contract_id for state in states],
                "asof_ts": [state.asof_ts for state in states],
                "asset": [state.contract.asset for state in states],
                "side": [state.contract.side for state in states],
                "threshold": [state.threshold for state in states],
                "threshold_source_key": [state.threshold_source_key for state in states],
                "threshold_event_ts": [state.threshold_event_ts for state in states],
                "threshold_observed_ts": [state.threshold_observed_ts for state in states],
                "seconds_left": [state.seconds_left for state in states],
                "settlement_price": [state.settlement_price for state in states],
                "settlement_source_key": [state.settlement_source_key for state in states],
                "settlement_event_ts": [state.settlement_event_ts for state in states],
                "settlement_observed_ts": [state.settlement_observed_ts for state in states],
                "proxy_prices_json": [_json(state.proxy_prices) for state in states],
                "source_disagreement_bps": [
                    state.source_disagreement_bps for state in states
                ],
                "best_bid": [state.best_bid for state in states],
                "best_ask": [state.best_ask for state in states],
                "executable_price": [state.executable_price for state in states],
                "spread": [state.spread for state in states],
                "book_event_ts": [state.book_event_ts for state in states],
                "book_observed_ts": [state.book_observed_ts for state in states],
                "quote_age_ms": [state.quote_age_ms for state in states],
                "source_age_ms": [state.source_age_ms for state in states],
                "source_observed_lag_ms": [
                    state.source_observed_lag_ms for state in states
                ],
                "book_age_ms": [state.book_age_ms for state in states],
                "book_observed_lag_ms": [state.book_observed_lag_ms for state in states],
                "realized_returns_json": [
                    _json(list(state.realized_returns)) for state in states
                ],
                "short_realized_vol": [state.short_realized_vol for state in states],
                "medium_realized_vol": [state.medium_realized_vol for state in states],
                "long_realized_vol": [state.long_realized_vol for state in states],
                "sigma_tau": [state.sigma_tau for state in states],
                "volatility_regime": [state.volatility_regime for state in states],
                "data_quality_flags_json": [
                    _json(list(state.data_quality_flags)) for state in states
                ],
                "created_at": [now for _ in states],
            }
        )
        with self._connection() as conn:
            conn.register("asof_state_input_rows", frame)
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
                select state_id, contract_id, asof_ts, asset, side, threshold,
                       threshold_source_key, threshold_event_ts, threshold_observed_ts,
                       seconds_left, settlement_price, settlement_source_key,
                       settlement_event_ts, settlement_observed_ts, proxy_prices_json,
                       source_disagreement_bps, best_bid, best_ask, executable_price, spread,
                       book_event_ts, book_observed_ts, quote_age_ms, source_age_ms,
                       source_observed_lag_ms, book_age_ms, book_observed_lag_ms,
                       realized_returns_json, short_realized_vol, medium_realized_vol,
                       long_realized_vol, sigma_tau, volatility_regime,
                       data_quality_flags_json, created_at
                from asof_state_input_rows
                """,
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
        with self._connection() as conn:
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
        with self._connection() as conn:
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
        with self._connection() as conn:
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
        with self._connection() as conn:
            rows = conn.execute(
                """
                select table_name, rows, latest_ts
                from (
                    select 1 as sort_order, 'core.contracts' as table_name,
                           count(*) as rows, max(last_seen_ts) as latest_ts
                    from core.contracts
                    union all
                    select 2 as sort_order, 'core.contract_rules' as table_name,
                           count(*) as rows, max(updated_at) as latest_ts
                    from core.contract_rules
                    union all
                    select 3 as sort_order, 'core.price_ticks' as table_name,
                           count(*) as rows, max(observed_ts) as latest_ts
                    from core.price_ticks
                    union all
                    select 4 as sort_order, 'core.orderbook_snapshots' as table_name,
                           count(*) as rows, max(observed_ts) as latest_ts
                    from core.orderbook_snapshots
                    union all
                    select 5 as sort_order, 'features.asof_state_inputs' as table_name,
                           count(*) as rows, max(created_at) as latest_ts
                    from features.asof_state_inputs
                    union all
                    select 6 as sort_order, 'features.decision_snapshots' as table_name,
                           count(*) as rows, max(created_at) as latest_ts
                    from features.decision_snapshots
                    union all
                    select 7 as sort_order, 'features.probability_outputs' as table_name,
                           count(*) as rows, max(created_at) as latest_ts
                    from features.probability_outputs
                ) as health_rows
                order by sort_order
                """
            ).fetchall()
        return tuple(
            {
                "table": row[0],
                "rows": int(row[1]),
                "latest_ts": _isoformat_utc(row[2]),
            }
            for row in rows
        )

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

    def latest_price_ticks(
        self,
        *,
        source_key: str,
        symbols: Sequence[str],
        asof_ts: datetime,
    ) -> dict[str, PriceObservation]:
        unique_symbols = tuple(dict.fromkeys(symbols))
        if not unique_symbols:
            return {}
        symbol_frame = pl.DataFrame({"symbol": list(unique_symbols)})
        with self._connection() as conn:
            conn.register("latest_price_symbols", symbol_frame)
            rows = conn.execute(
                """
                select source_key, symbol, event_ts::VARCHAR, observed_ts::VARCHAR,
                       price, bid, ask, sequence
                from (
                    select ticks.*,
                           row_number() over (
                               partition by ticks.symbol
                               order by ticks.event_ts desc, ticks.observed_ts desc
                           ) as row_number
                    from core.price_ticks as ticks
                    join latest_price_symbols as symbols using (symbol)
                    where ticks.source_key = ?
                      and ticks.event_ts <= ?
                      and ticks.observed_ts <= ?
                ) as ranked_ticks
                where row_number = 1
                """,
                [source_key, asof_ts, asof_ts],
            ).fetchall()
        return {
            row[1]: PriceObservation(
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
        }

    def latest_price_ticks_before(
        self,
        *,
        source_key: str,
        symbols: Sequence[str],
        event_ts_lte: datetime,
        observed_ts_lte: datetime,
    ) -> dict[str, PriceObservation]:
        unique_symbols = tuple(dict.fromkeys(symbols))
        if not unique_symbols:
            return {}
        symbol_frame = pl.DataFrame({"symbol": list(unique_symbols)})
        with self._connection() as conn:
            conn.register("latest_price_before_symbols", symbol_frame)
            rows = conn.execute(
                """
                select source_key, symbol, event_ts::VARCHAR, observed_ts::VARCHAR,
                       price, bid, ask, sequence
                from (
                    select ticks.*,
                           row_number() over (
                               partition by ticks.symbol
                               order by ticks.event_ts desc, ticks.observed_ts desc
                           ) as row_number
                    from core.price_ticks as ticks
                    join latest_price_before_symbols as symbols using (symbol)
                    where ticks.source_key = ?
                      and ticks.event_ts <= ?
                      and ticks.observed_ts <= ?
                ) as ranked_ticks
                where row_number = 1
                """,
                [source_key, event_ts_lte, observed_ts_lte],
            ).fetchall()
        return {
            row[1]: PriceObservation(
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
        }

    def latest_price_tick_before(
        self,
        *,
        source_key: str,
        symbol: str,
        event_ts_lte: datetime,
        observed_ts_lte: datetime,
    ) -> PriceObservation | None:
        with self._connection() as conn:
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
        with self._connection() as conn:
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

    def price_ticks_before_by_symbol(
        self,
        *,
        source_key: str,
        symbols: Sequence[str],
        asof_ts: datetime,
        limit: int,
    ) -> dict[str, tuple[PriceObservation, ...]]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        unique_symbols = tuple(dict.fromkeys(symbols))
        if not unique_symbols:
            return {}
        symbol_frame = pl.DataFrame({"symbol": list(unique_symbols)})
        with self._connection() as conn:
            conn.register("price_history_symbols", symbol_frame)
            rows = conn.execute(
                """
                select source_key, symbol, event_ts::VARCHAR, observed_ts::VARCHAR,
                       price, bid, ask, sequence
                from (
                    select ticks.*,
                           row_number() over (
                               partition by ticks.symbol
                               order by ticks.event_ts desc, ticks.observed_ts desc
                           ) as row_number
                    from core.price_ticks as ticks
                    join price_history_symbols as symbols using (symbol)
                    where ticks.source_key = ?
                      and ticks.event_ts <= ?
                      and ticks.observed_ts <= ?
                ) as ranked_ticks
                where row_number <= ?
                order by symbol asc, event_ts asc, observed_ts asc
                """,
                [source_key, asof_ts, asof_ts, limit],
            ).fetchall()
        history: dict[str, list[PriceObservation]] = {}
        for row in rows:
            history.setdefault(row[1], []).append(
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
            )
        return {symbol: tuple(rows) for symbol, rows in history.items()}

    def latest_orderbook_snapshot(
        self,
        *,
        venue: str,
        token_id: str,
        asof_ts: datetime,
    ) -> OrderBookObservation | None:
        with self._connection() as conn:
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

    def latest_orderbook_snapshots(
        self,
        *,
        venue: str,
        token_ids: Sequence[str],
        asof_ts: datetime,
    ) -> dict[str, OrderBookObservation]:
        unique_token_ids = tuple(dict.fromkeys(token_ids))
        if not unique_token_ids:
            return {}
        token_frame = pl.DataFrame({"token_id": list(unique_token_ids)})
        with self._connection() as conn:
            conn.register("latest_orderbook_token_ids", token_frame)
            rows = conn.execute(
                """
                select venue, contract_id, token_id, event_ts::VARCHAR, observed_ts::VARCHAR,
                       best_bid, best_ask, bid_size_top, ask_size_top, spread, depth_json
                from (
                    select snapshots.*,
                           row_number() over (
                               partition by snapshots.token_id
                               order by snapshots.event_ts desc, snapshots.observed_ts desc
                           ) as row_number
                    from core.orderbook_snapshots as snapshots
                    join latest_orderbook_token_ids as ids using (token_id)
                    where snapshots.venue = ?
                      and snapshots.event_ts <= ?
                      and snapshots.observed_ts <= ?
                ) as ranked_snapshots
                where row_number = 1
                """,
                [venue, asof_ts, asof_ts],
            ).fetchall()
        return {
            row[2]: OrderBookObservation(
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
            for row in rows
        }


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
