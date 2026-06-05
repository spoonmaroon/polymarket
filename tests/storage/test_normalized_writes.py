from collections.abc import Iterator, Sequence
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, TypeVar, cast, overload

import duckdb
import pytest

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import DecisionState, OrderBookObservation, PriceObservation
from polymarket_engine.probability.ensemble_weights import DynamicWeightSet
from polymarket_engine.probability.event_log import ProbabilityEventLogRow
from polymarket_engine.probability.generator_contracts import DynamicWeightScope, GeneratorId
from polymarket_engine.probability.generator_contracts import HistoricalValidationWindow
from polymarket_engine.probability.schema import ProbabilityInput, ProbabilityOutput
from polymarket_engine.storage import duckdb_store
from polymarket_engine.storage.duckdb_store import DuckDbIngestStore
from polymarket_engine.storage.duckdb_store import MarketOutcomeRecord


T = TypeVar("T")


class _CountingSequence(Sequence[T]):
    def __init__(self, items: tuple[T, ...]) -> None:
        self._items = items
        self.iterations = 0

    def __iter__(self) -> Iterator[T]:
        self.iterations += 1
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[T, ...]: ...

    def __getitem__(self, index: int | slice) -> T | tuple[T, ...]:
        return self._items[index]


def _contract() -> ContractSpec:
    return ContractSpec(
        contract_id="btc-market:UP",
        venue="polymarket",
        market_id="btc-market",
        condition_id="0xbtc",
        slug="btc-updown-5m-1780264500",
        asset="BTC",
        side="UP",
        token_id="111",
        threshold_type="start_price",
        threshold_price=None,
        comparison_operator=">=",
        start_ts=datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc),
        expiry_ts=datetime(2026, 5, 31, 20, 5, tzinfo=timezone.utc),
        settlement_source_name="chainlink_data_streams",
        settlement_source_url="https://data.chain.link/streams/btc-usd",
        settlement_symbol="BTC/USD",
        rule_text="fixture",
        rule_hash="hash",
        parser_version="test",
    )


def _state() -> DecisionState:
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    return DecisionState(
        state_id="state-1",
        asof_ts=asof_ts,
        contract=contract,
        threshold=103_950.0,
        threshold_source_key="polymarket_rtds_chainlink",
        threshold_event_ts=datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc),
        threshold_observed_ts=datetime(2026, 5, 31, 20, 0, 1, tzinfo=timezone.utc),
        seconds_left=120.0,
        settlement_price=104_000.0,
        settlement_source_key="polymarket_rtds_chainlink",
        settlement_event_ts=asof_ts,
        settlement_observed_ts=asof_ts,
        proxy_prices={"coinbase_advanced_ws": 104_010.0},
        source_disagreement_bps=0.96,
        best_bid=0.61,
        best_ask=0.64,
        executable_price=0.64,
        spread=0.03,
        book_event_ts=asof_ts,
        book_observed_ts=asof_ts,
        quote_age_ms=1000,
        source_age_ms=1000,
        source_observed_lag_ms=0,
        book_age_ms=1000,
        book_observed_lag_ms=0,
        realized_returns=(0.001, -0.0005),
        short_realized_vol=0.01,
        medium_realized_vol=0.012,
        long_realized_vol=0.015,
        sigma_tau=0.002,
        volatility_regime="normal",
        data_quality_flags=(),
    )


def _weight_scope() -> DynamicWeightScope:
    return DynamicWeightScope(
        asset="BTC",
        horizon_seconds=300,
        seconds_left_bucket="60-120",
        z_path_bucket="far",
        vol_regime="normal",
        vol_trend="flat",
        wick_regime="quiet",
        source_quality_state="ready",
    )


def _weight_set(runtime_asof_ts: datetime) -> DynamicWeightSet:
    return DynamicWeightSet(
        weights={
            GeneratorId.LOGNORMAL_BASELINE: 0.60,
            GeneratorId.EMPIRICAL_CONDITIONAL: 0.25,
            GeneratorId.STRESS_OVERLAY: 0.15,
        },
        validation_window=HistoricalValidationWindow(
            asof_ts=datetime(2026, 5, 31, 18, 0, tzinfo=timezone.utc),
            evaluated_through_ts=datetime(2026, 5, 31, 19, 0, tzinfo=timezone.utc),
            label_window_seconds=3600,
        ),
        runtime_asof_ts=runtime_asof_ts,
        source="fixture_losses",
    )


def test_store_writes_contract_price_book_state_decision_and_label(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)

    store.upsert_contract_spec(contract)
    store.insert_price_tick(
        PriceObservation(
            source_key="polymarket_rtds_chainlink",
            symbol="BTC/USD",
            event_ts=asof_ts,
            observed_ts=asof_ts,
            price=104_000.0,
        )
    )
    store.insert_orderbook_snapshot(
        OrderBookObservation(
            venue="polymarket",
            contract_id=contract.contract_id,
            token_id=contract.token_id,
            event_ts=asof_ts,
            observed_ts=asof_ts,
            best_bid=0.61,
            best_ask=0.64,
            bid_size_top=50.0,
            ask_size_top=40.0,
            spread=0.03,
            depth_json='{"bids":[],"asks":[]}',
        )
    )
    state = _state()
    store.upsert_asof_state_input(state)
    store.insert_decision_snapshot(
        decision_id="decision-1",
        state=state,
        model={"model_version": "none"},
        decision="WAIT",
        block_reason="probability_model_not_built",
    )
    store.insert_decision_label(
        decision_id="decision-1",
        contract_id=contract.contract_id,
        expiry_ts=contract.expiry_ts,
        settlement_price=104_100.0,
        did_finish_win=True,
        did_no_touch=True,
        realized_edge=None,
        label_source="fixture",
    )

    with duckdb.connect(str(db_path)) as conn:
        assert conn.sql("select count(*) from core.contracts").fetchone() == (1,)
        assert conn.sql("select count(*) from core.price_ticks").fetchone() == (1,)
        assert conn.sql("select count(*) from core.orderbook_snapshots").fetchone() == (1,)
        assert conn.sql("select count(*) from features.asof_state_inputs").fetchone() == (1,)
        assert conn.sql(
            "select decision, block_reason from features.decision_snapshots"
        ).fetchall() == [("WAIT", "probability_model_not_built")]
        assert conn.sql(
            "select did_finish_win, did_no_touch from validation.decision_labels"
        ).fetchall() == [(True, True)]


def test_normalized_table_health_reports_counts_and_latest_writes(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    contract = _contract()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)

    store.upsert_contract_spec(contract)
    store.insert_price_tick(
        PriceObservation(
            source_key="polymarket_rtds_chainlink",
            symbol="BTC/USD",
            event_ts=asof_ts,
            observed_ts=asof_ts,
            price=104_000.0,
        )
    )
    store.insert_orderbook_snapshot(
        OrderBookObservation(
            venue="polymarket",
            contract_id=contract.contract_id,
            token_id=contract.token_id,
            event_ts=asof_ts,
            observed_ts=asof_ts,
            best_bid=0.61,
            best_ask=0.64,
            bid_size_top=50.0,
            ask_size_top=40.0,
            spread=0.03,
            depth_json='{"bids":[],"asks":[]}',
        )
    )

    health = store.normalized_table_health()

    by_table = {row["table"]: row for row in health}
    assert by_table["core.contracts"]["rows"] == 1
    assert by_table["core.price_ticks"]["latest_ts"] == asof_ts.isoformat()
    assert by_table["core.orderbook_snapshots"]["rows"] == 1
    assert by_table["features.asof_state_inputs"]["rows"] == 0
    assert by_table["features.probability_outputs"]["rows"] == 0


def test_store_inserts_probability_output_with_json_artifacts(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = _state()
    probability_input = ProbabilityInput.from_decision_state(state)
    output = ProbabilityOutput(
        state_id=state.state_id,
        asof_ts=state.asof_ts,
        p_finish=0.58,
        p_no_touch=0.81,
        z_path=probability_input.z_path,
        model_version="offline-replay-v1",
        seed=123,
        diagnostics={"paths": 1000, "blocked": False},
    )

    store.insert_probability_output(
        output_id="probability-output-1",
        probability_input=probability_input,
        output=output,
    )

    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute(
            """
            select model_version, p_finish, p_no_touch, input_json, output_json
            from features.probability_outputs
            """
        ).fetchone()

    assert row is not None
    model_version, p_finish, p_no_touch, input_json, output_json = row
    assert model_version == "offline-replay-v1"
    assert p_finish == 0.58
    assert p_no_touch == 0.81
    assert json.loads(input_json)["state_id"] == state.state_id
    assert json.loads(input_json)["z_path"] == probability_input.z_path
    assert json.loads(output_json)["diagnostics"]["paths"] == 1000


def test_insert_probability_event_log_row_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "events.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()

    asof = datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc)
    row = ProbabilityEventLogRow(
        event_id="event-1",
        output_id=None,
        state_id="state-1",
        contract_id="btc-updown-5m-1:UP",
        market_slug="btc-updown-5m-1",
        asset="BTC",
        side="UP",
        start_ts=asof,
        expiry_ts=asof + timedelta(minutes=5),
        asof_ts=asof,
        probability_kind="NOWCAST",
        backend="analytic",
        model_version="fast-nowcast-v1",
        generator_version=None,
        cache_key=None,
        cache_status=None,
        p_finish=0.62,
        p_no_touch=0.0,
        z_path=0.31,
        sigma_tau=0.001,
        executable_price=0.59,
        spread=0.01,
        seconds_left=210.0,
        wave_phase="none",
        wave_score=0.0,
        path_count=None,
        seed=None,
        queue_ms=None,
        runtime_ms=0.2,
        state_to_status_ms=12.0,
        total_lag_ms=12.0,
        generated_at=asof,
        valid_from=asof,
        valid_until=asof + timedelta(seconds=2),
        diagnostics={"source": "unit-test"},
    )

    store.insert_probability_event(row)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        saved = conn.execute(
            """
            select probability_kind, backend, model_version, p_finish, diagnostics_json
            from features.probability_event_log
            where event_id = 'event-1'
            """
        ).fetchone()

    assert saved == (
        "NOWCAST",
        "analytic",
        "fast-nowcast-v1",
        0.62,
        '{"source":"unit-test"}',
    )

    store.insert_probability_event(replace(row, p_finish=0.99))

    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from features.probability_event_log").fetchone() == (
            1,
        )
        assert conn.execute("select p_finish from features.probability_event_log").fetchone() == (
            0.62,
        )


def test_insert_simulation_artifact_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "artifact.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()

    store.insert_simulation_artifact(
        artifact_id="artifact-1",
        output_id="prob-1",
        state_id="state-1",
        asof_ts=datetime(2026, 6, 5, 17, 0, tzinfo=timezone.utc),
        model_version="offline-lognormal-chainlink-sigma-v1",
        backend="cpu",
        path_count=2_000,
        terminal_win_count=1_200,
        no_touch_win_count=900,
        terminal_price_quantiles={"p05": 100.0, "p50": 101.0, "p95": 103.0},
        crossing_count_quantiles={"p50": 1.0, "p95": 4.0},
        sampled_paths=[{"index": 0, "points": [100.0, 101.0], "terminal_win": True}],
        diagnostics={"source": "unit-test"},
    )

    with duckdb.connect(str(db_path), read_only=True) as conn:
        saved = conn.execute(
            """
            select path_count, terminal_win_count, terminal_price_quantiles_json
            from features.simulation_artifacts
            where artifact_id = 'artifact-1'
            """
        ).fetchone()

    assert saved == (2_000, 1_200, '{"p05":100.0,"p50":101.0,"p95":103.0}')


def test_store_inserts_and_reads_latest_generator_weight_snapshot(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    scope = _weight_scope()

    store.insert_generator_weight_snapshot(
        snapshot_id="weights-old",
        weight_set=_weight_set(datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc)),
        scope=scope,
        scores={GeneratorId.LOGNORMAL_BASELINE: 0.31},
        label_counts={GeneratorId.LOGNORMAL_BASELINE: 40},
    )
    store.insert_generator_weight_snapshot(
        snapshot_id="weights-new",
        weight_set=_weight_set(datetime(2026, 5, 31, 20, 5, tzinfo=timezone.utc)),
        scope=scope,
        scores={
            GeneratorId.LOGNORMAL_BASELINE: 0.29,
            GeneratorId.EMPIRICAL_CONDITIONAL: 0.34,
        },
        label_counts={
            GeneratorId.LOGNORMAL_BASELINE: 45,
            GeneratorId.EMPIRICAL_CONDITIONAL: 30,
            GeneratorId.STRESS_OVERLAY: 12,
        },
    )

    latest = store.latest_generator_weight_snapshot()

    assert latest is not None
    assert latest["snapshot_id"] == "weights-new"
    assert latest["runtime_asof_ts"] == "2026-05-31T20:05:00+00:00"
    assert latest["evaluated_through_ts"] == "2026-05-31T19:00:00+00:00"
    assert latest["label_window_seconds"] == 3600
    assert latest["source"] == "fixture_losses"
    assert latest["scope"]["asset"] == "BTC"
    assert latest["weights"]["lognormal_baseline"] == pytest.approx(0.60)
    assert latest["scores"]["empirical_conditional"] == pytest.approx(0.34)
    assert latest["label_counts"]["stress_overlay"] == 12

    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute(
            """
            select weights_json, scores_json, label_counts_json
            from research.generator_weight_snapshots
            where snapshot_id = 'weights-new'
            """
        ).fetchone()

    assert row is not None
    assert json.loads(row[0])["stress_overlay"] == pytest.approx(0.15)
    assert json.loads(row[1])["lognormal_baseline"] == pytest.approx(0.29)
    assert json.loads(row[2])["lognormal_baseline"] == 45


def test_store_upserts_and_reads_market_outcome_history(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    start_ts = datetime(2026, 5, 31, 20, 0, tzinfo=timezone.utc)
    expiry_ts = datetime(2026, 5, 31, 20, 5, tzinfo=timezone.utc)

    written = store.upsert_market_outcome_records(
        (
            MarketOutcomeRecord(
                market_id="btc-updown-5m-1780264500",
                condition_id="0xbtc",
                market_slug="btc-updown-5m-1780264500",
                asset="BTC",
                interval="5m",
                start_ts=start_ts,
                expiry_ts=expiry_ts,
                up_token_id="up-token",
                down_token_id="down-token",
                threshold_price=104_000.0,
                threshold_event_ts=start_ts,
                threshold_observed_ts=start_ts,
                end_price=104_001.0,
                end_event_ts=expiry_ts,
                end_observed_ts=expiry_ts,
                computed_winner=None,
                computed_label_source=None,
                computed_at=None,
                official_winner="UP",
                winning_token_id="up-token",
                official_resolution_status="resolved",
                official_label_source="polymarket_clob_market",
                official_resolved_at=expiry_ts,
                rule_hash="hash",
                mismatch=None,
            ),
        )
    )

    rows = store.market_outcome_history(limit=4)

    assert written == 1
    assert rows[0]["market_id"] == "btc-updown-5m-1780264500"
    assert rows[0]["computed_winner"] is None
    assert rows[0]["official_winner"] == "UP"
    assert rows[0]["winning_token_id"] == "up-token"
    assert rows[0]["official_resolution_status"] == "resolved"
    assert rows[0]["mismatch"] is None


def test_store_context_reuses_one_duckdb_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "state.duckdb"
    DuckDbIngestStore(db_path).apply_schema()
    duckdb_module = getattr(duckdb_store, "duckdb")
    real_connect = duckdb_module.connect
    connect_count = 0

    def counting_connect(*args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        nonlocal connect_count
        connect_count += 1
        return cast(duckdb.DuckDBPyConnection, real_connect(*args, **kwargs))

    monkeypatch.setattr(duckdb_module, "connect", counting_connect)

    with DuckDbIngestStore(db_path) as store:
        store.normalized_table_health()
        store.normalized_table_health()

    assert connect_count == 1


def test_store_skips_duplicate_contract_spec_batch_in_one_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "state.duckdb"
    DuckDbIngestStore(db_path).apply_schema()
    duckdb_module = getattr(duckdb_store, "duckdb")
    real_connect = duckdb_module.connect
    execute_count = 0

    class _CountingConnection:
        def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
            self._conn = conn

        def execute(self, *args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
            nonlocal execute_count
            execute_count += 1
            return self._conn.execute(*args, **kwargs)

        def register(self, *args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
            return self._conn.register(*args, **kwargs)

        def close(self) -> None:
            self._conn.close()

    def counting_connect(*args: Any, **kwargs: Any) -> _CountingConnection:
        return _CountingConnection(cast(duckdb.DuckDBPyConnection, real_connect(*args, **kwargs)))

    monkeypatch.setattr(duckdb_module, "connect", counting_connect)

    with DuckDbIngestStore(db_path) as store:
        store.upsert_contract_specs((_contract(),))
        store.upsert_contract_specs((_contract(),))

    assert execute_count == 2


def test_normalized_table_health_uses_one_duckdb_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "state.duckdb"
    DuckDbIngestStore(db_path).apply_schema()
    duckdb_module = getattr(duckdb_store, "duckdb")
    real_connect = duckdb_module.connect
    execute_count = 0

    class _CountingConnection:
        def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
            self._conn = conn

        def execute(self, *args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
            nonlocal execute_count
            execute_count += 1
            return self._conn.execute(*args, **kwargs)

        def close(self) -> None:
            self._conn.close()

    def counting_connect(*args: Any, **kwargs: Any) -> _CountingConnection:
        return _CountingConnection(cast(duckdb.DuckDBPyConnection, real_connect(*args, **kwargs)))

    monkeypatch.setattr(duckdb_module, "connect", counting_connect)

    with DuckDbIngestStore(db_path) as store:
        store.normalized_table_health()

    assert execute_count == 1


def test_store_inserts_probability_output_with_negative_seed(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    state = _state()
    probability_input = ProbabilityInput.from_decision_state(state)
    output = ProbabilityOutput(
        state_id=state.state_id,
        asof_ts=state.asof_ts,
        p_finish=0.58,
        p_no_touch=0.81,
        z_path=probability_input.z_path,
        model_version="offline-replay-v1",
        seed=-1,
        diagnostics={},
    )

    store.insert_probability_output(
        output_id="probability-output-negative-seed",
        probability_input=probability_input,
        output=output,
    )

    with duckdb.connect(str(db_path), read_only=True) as conn:
        row = conn.execute("select seed from features.probability_outputs").fetchone()

    assert row == (-1,)


def test_register_ingest_file_records_retention_manifest(tmp_path: Path) -> None:
    db_path = tmp_path / "collector.duckdb"
    raw_path = tmp_path / "file.parquet"
    raw_path.write_bytes(b"abc")
    store = DuckDbIngestStore(db_path)
    store.apply_schema()

    store.register_ingest_file(
        file_id="file-1",
        source_key="coinbase_advanced_ws",
        stream_key="ticker",
        partition_date="2026-05-31",
        partition_hour=21,
        path=str(raw_path),
        sha256="abc123",
        row_count=2,
        first_event_ts=datetime(2026, 5, 31, 21, 0, 0, tzinfo=timezone.utc),
        last_event_ts=datetime(2026, 5, 31, 21, 0, 1, tzinfo=timezone.utc),
    )

    with duckdb.connect(str(db_path)) as conn:
        rows = conn.sql(
            "select source_key, stream_key, retention_class, archive_after_days "
            "from ops.retention_manifests"
        ).fetchall()

    assert rows == [("coinbase_advanced_ws", "ticker", "raw_hot_90d", 90)]


def test_store_batch_writes_prices_and_orderbooks(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)

    store.insert_price_ticks(
        (
            PriceObservation(
                source_key="polymarket_rtds_chainlink",
                symbol="BTC/USD",
                event_ts=asof_ts,
                observed_ts=asof_ts,
                price=104_000.0,
            ),
            PriceObservation(
                source_key="polymarket_rtds_chainlink",
                symbol="ETH/USD",
                event_ts=asof_ts,
                observed_ts=asof_ts,
                price=3_900.0,
            ),
        ),
        raw_file_id="sha256:raw",
    )
    store.insert_orderbook_snapshots(
        (
            OrderBookObservation(
                venue="polymarket",
                contract_id="0xbook",
                token_id="token-1",
                event_ts=asof_ts,
                observed_ts=asof_ts,
                best_bid=0.61,
                best_ask=0.64,
                bid_size_top=50.0,
                ask_size_top=40.0,
                spread=0.03,
                depth_json='{"bids":[],"asks":[]}',
            ),
        ),
        raw_file_id="sha256:raw",
    )

    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.price_ticks").fetchone() == (2,)
        assert conn.execute("select count(*) from core.orderbook_snapshots").fetchone() == (1,)
        assert conn.execute(
            "select distinct raw_file_id from core.price_ticks"
        ).fetchall() == [("sha256:raw",)]


def test_store_builds_price_tick_batch_columns_in_one_pass(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    ticks = _CountingSequence(
        (
            PriceObservation(
                source_key="polymarket_rtds_chainlink",
                symbol="BTC/USD",
                event_ts=asof_ts,
                observed_ts=asof_ts,
                price=104_000.0,
            ),
            PriceObservation(
                source_key="polymarket_rtds_chainlink",
                symbol="ETH/USD",
                event_ts=asof_ts,
                observed_ts=asof_ts,
                price=3_900.0,
            ),
        )
    )

    store.insert_price_ticks(ticks, raw_file_id="sha256:raw")

    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.price_ticks").fetchone() == (2,)
        assert conn.execute(
            "select distinct raw_file_id from core.price_ticks"
        ).fetchall() == [("sha256:raw",)]
    assert ticks.iterations == 1


def test_store_builds_orderbook_snapshot_batch_columns_in_one_pass(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)

    snapshots = _CountingSequence(
        (
            OrderBookObservation(
                venue="polymarket",
                contract_id="0xbook",
                token_id="token-1",
                event_ts=asof_ts,
                observed_ts=asof_ts,
                best_bid=0.61,
                best_ask=0.64,
                bid_size_top=50.0,
                ask_size_top=40.0,
                spread=0.03,
                depth_json='{"bids":[],"asks":[]}',
            ),
        )
    )

    store.insert_orderbook_snapshots(snapshots, raw_file_id="sha256:raw")

    with duckdb.connect(str(db_path), read_only=True) as conn:
        assert conn.execute("select count(*) from core.orderbook_snapshots").fetchone() == (
            1,
        )
        assert conn.execute(
            "select distinct raw_file_id from core.orderbook_snapshots"
        ).fetchall() == [("sha256:raw",)]
    assert snapshots.iterations == 1
