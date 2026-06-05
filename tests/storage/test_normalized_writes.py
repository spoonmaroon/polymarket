from collections.abc import Iterator, Sequence
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, TypeVar, cast, overload

import duckdb
import pytest

from polymarket_engine.domain.contracts import ContractSpec
from polymarket_engine.domain.market_state import DecisionState, OrderBookObservation, PriceObservation
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


def test_store_inserts_and_reads_simulation_artifact(tmp_path: Path) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)

    store.insert_simulation_artifact(
        artifact_id="artifact-1",
        output_id="probability-output-1",
        state_id="state-1",
        asof_ts=asof_ts,
        model_version="offline-replay-v1",
        backend="cpu_rayon",
        artifact={"paths": [0.1, -0.2], "summary": {"path_count": 2}},
    )

    artifact = store.simulation_artifact("artifact-1")

    assert artifact is not None
    assert artifact["artifact_id"] == "artifact-1"
    assert artifact["output_id"] == "probability-output-1"
    assert artifact["state_id"] == "state-1"
    assert artifact["asof_ts"] == asof_ts.isoformat()
    assert artifact["model_version"] == "offline-replay-v1"
    assert artifact["backend"] == "cpu_rayon"
    assert artifact["artifact"] == {"paths": [0.1, -0.2], "summary": {"path_count": 2}}
    assert isinstance(artifact["created_at"], str)
    with duckdb.connect(str(db_path), read_only=True) as conn:
        artifact_json_row = conn.execute(
            "select artifact_json from features.simulation_artifacts"
        ).fetchone()
    assert artifact_json_row is not None
    (artifact_json,) = artifact_json_row
    assert json.loads(artifact_json)["summary"]["path_count"] == 2


@pytest.mark.parametrize("artifact_json", ["null", "[1,2,3]", '"scalar"'])
def test_store_rejects_non_object_simulation_artifact_json(
    tmp_path: Path,
    artifact_json: str,
) -> None:
    db_path = tmp_path / "state.duckdb"
    store = DuckDbIngestStore(db_path)
    store.apply_schema()
    asof_ts = datetime(2026, 5, 31, 20, 3, tzinfo=timezone.utc)
    with duckdb.connect(str(db_path)) as conn:
        conn.execute(
            """
            insert into features.simulation_artifacts
            (artifact_id, output_id, state_id, asof_ts, model_version, backend,
             artifact_json, created_at)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "artifact-corrupt",
                "probability-output-1",
                "state-1",
                asof_ts,
                "offline-replay-v1",
                "cpu_rayon",
                artifact_json,
                asof_ts,
            ],
        )

    with pytest.raises(ValueError, match="artifact_json must be a JSON object"):
        store.simulation_artifact("artifact-corrupt")


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
