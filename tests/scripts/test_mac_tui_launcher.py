from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
DESKTOP_LAUNCHERS = [
    Path("/Users/goon/Desktop/Polymarket TUI.command"),
    Path("/Users/goon/Desktop/Desktop - spoon/Polymarket TUI.command"),
]


def test_mac_tui_launcher_uses_local_tunnel_and_rebuilds_stale_binary() -> None:
    script = (ROOT / "scripts" / "open_tui_mac.sh").read_text(encoding="utf-8")

    assert "http://127.0.0.1:8000" in script
    assert "check_mac_polymarket_tunnel.sh" in script
    assert ".polymarket-cockpit-tui.git-head" in script
    assert 'cargo build --release -p polymarket-cockpit-tui' in script
    assert 'git -C "$REPO" rev-parse HEAD' in script
    assert 'POLYMARKET_TUI_TEST_LAUNCH' in script


@pytest.mark.skipif(
    not all(path.exists() for path in DESKTOP_LAUNCHERS),
    reason="Enoch Mac desktop launchers are not present on this machine",
)
def test_desktop_tui_launchers_delegate_to_canonical_script() -> None:
    expected = "#!/bin/zsh\nexec /Users/goon/polymarket/scripts/open_tui_mac.sh\n"

    for launcher in DESKTOP_LAUNCHERS:
        assert launcher.read_text(encoding="utf-8") == expected
