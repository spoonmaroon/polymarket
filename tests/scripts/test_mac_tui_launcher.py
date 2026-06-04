from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_mac_tui_launcher_uses_thepc_api_and_rebuilds_stale_binary() -> None:
    script = (ROOT / "scripts" / "open_tui_mac.sh").read_text(encoding="utf-8")

    assert "http://100.72.104.49:8000" in script
    assert ".polymarket-cockpit-tui.git-head" in script
    assert 'cargo build --release -p polymarket-cockpit-tui' in script
    assert 'git -C "$REPO" rev-parse HEAD' in script
    assert 'POLYMARKET_TUI_TEST_LAUNCH' in script
