from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_part_two_docs_describe_active_5m_rust_state_manager() -> None:
    text = (ROOT / "docs" / "PART_TWO_LIVE_COLLECTORS.md").read_text(encoding="utf-8")

    assert "active live path is the Rust SDK state-manager runtime" in text
    assert "BTC/ETH 5m current, next, and next-next windows" in text
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
