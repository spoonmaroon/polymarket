from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_part_two_docs_describe_active_5m_rust_state_manager() -> None:
    text = (ROOT / "docs" / "PART_TWO_LIVE_COLLECTORS.md").read_text(encoding="utf-8")

    assert "active live path is the Rust SDK state-manager runtime" in text
    assert "BTC/ETH 5m current and next windows" in text
    assert "--mode state-manager" in text
    assert "--interval 5m" in text
    assert "--state-snapshot-dir" in text
    assert "append-only raw WebSocket journals and state" in text
    assert "polymarket-engine normalize-rust-events" in text
    assert "write-normalized-health" in text
    assert "build-current-decision-states" in text
    assert "latency_marks" in text
    assert "--intervals 5m,15m" not in text


def test_readme_points_to_state_manager_not_legacy_collector() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "Rust State Manager" in text
    assert "--mode state-manager" in text
    assert "--interval 5m" in text
    assert "legacy Python collector is retired" in text
    assert "normalize-rust-events" in text
    assert "write-normalized-health" in text
    assert "build-current-decision-states" in text


def test_spoon_docs_describe_two_window_runtime_and_normalizer_sidecar() -> None:
    part_two = (ROOT / "docs" / "PART_TWO_LIVE_COLLECTORS.md").read_text(
        encoding="utf-8"
    )
    deployment = (ROOT / "docs" / "SPOON_DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "current and next 5m windows" in part_two
    assert "POLYMARKET_PREWARM_WINDOWS=2" in deployment
    assert "normalizer sidecar" in part_two
    assert "normalized_health.json" in part_two
    assert "core.contract_rules remains empty" in part_two
    assert "features.decision_snapshots remains empty until probability" in part_two
    assert "origin/codex/rust-raw-normalizer" in deployment


def test_live_docs_keep_hot_decisions_inside_rust_state_manager() -> None:
    text = (ROOT / "docs" / "PART_TWO_LIVE_COLLECTORS.md").read_text(encoding="utf-8")

    assert "--decision-snapshot-dir" in text
    assert "hot decision" in text.lower()
    assert "DuckDB owns normalized replay/research" in text
    assert "must not sit on the live decision path" in text


def test_engine_plan_reflects_active_runtime_boundary() -> None:
    text = (ROOT / "docs" / "BINARY_CONTRACT_ENGINE_PLAN.md").read_text(
        encoding="utf-8"
    )

    assert "BTC/ETH 5-minute current and next contract windows" in text
    assert "Rust owns hot read-only state" in text
    assert "append-only hot `DecisionState` snapshots" in text
    assert "DuckDB/Python own raw-journal normalization, replay" in text
    assert "DuckDB must not sit on the live decision path" in text
    assert "hot-state replay equivalence is proven" in text
    assert "`polymarket-engine collect` is retired and must fail closed" in text


def test_engine_plan_keeps_monte_carlo_offline_and_replay_derived() -> None:
    text = (ROOT / "docs" / "BINARY_CONTRACT_ENGINE_PLAN.md").read_text(
        encoding="utf-8"
    )

    assert "offline derived artifacts from an as-of `DecisionState`/`ProbabilityInput`" in text
    assert "replay and research outputs only, not live authority" in text
    assert "not paper trading, and not execution" in text
    assert "must not enter the hot decision path" in text
    assert "no authority over live decisions, paper trading, or execution" in text


def test_spoon_docs_include_latency_probe_without_order_placement() -> None:
    text = (ROOT / "docs" / "SPOON_DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "--mode latency-probe" in text
    assert "no-auth" in text.lower()
    assert "does not place orders" in text


def test_spoon_docs_record_vps_cadence_revisit() -> None:
    text = (ROOT / "docs" / "SPOON_DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "THEPC" in text
    assert "current normalizer cadence" in text
    assert "POLYMARKET_NORMALIZER_INTERVAL_SECONDS=0.1" in text
    assert "POLYMARKET_NORMALIZER_INTERVAL_SECONDS=0.25" in text
    assert "home-server CPU compromise" in text
    assert "After VPS migration" in text


def test_spoon_docs_include_read_only_cockpit_tui() -> None:
    text = (ROOT / "docs" / "SPOON_DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "polymarket-cockpit-tui" in text
    assert "--engine-api-url" in text
    assert "read-only" in text
    assert "POLYMARKET_ENABLE_CONTAINER_STATUS=1" in text


def test_spoon_docs_pin_safe_hot_replay_gate_command() -> None:
    deployment = (ROOT / "docs" / "SPOON_DEPLOYMENT.md").read_text(encoding="utf-8")
    part_two = (ROOT / "docs" / "PART_TWO_LIVE_COLLECTORS.md").read_text(
        encoding="utf-8"
    )
    command = (
        "python3 scripts/run_hot_replay_gate.py "
        "--raw-root /home/spoon/polymarket-data/raw "
        "--duckdb-path /home/spoon/polymarket-data/db/polymarket.duckdb "
        "--snapshot-dir /home/spoon/polymarket-data/live/hot-replay-snapshot "
        "--report-out /home/spoon/polymarket-data/live/hot_decision_replay_report.json "
        "--limit 40 --scan-limit 5000"
    )

    assert command in deployment
    assert "copied read-only snapshot" in deployment
    assert "does not pause collector or normalizer" in deployment
    assert "must not enter the hot live decision path" in deployment
    assert "copied read-only snapshot" in part_two
    assert "does not pause collector or normalizer" in part_two
    assert "must not enter the hot live decision path" in part_two
