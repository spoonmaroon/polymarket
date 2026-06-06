from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_mac_duckdb_ui_launcher_tunnels_thepc_duckdb_ui() -> None:
    script = (ROOT / "scripts" / "open_duckdb_ui_mac.sh").read_text(
        encoding="utf-8"
    )

    assert 'PC_HOST="${PC_HOST:-ender@100.72.104.49}"' in script
    assert 'PC_WSL_DISTRO="${PC_WSL_DISTRO:-Ubuntu}"' in script
    assert "POLYMARKET_DUCKDB_UI_PORT:-4213" in script
    assert "/home/ender/bin/open-polymarket-duckdb-ui.sh" in script
    assert "ssh -f -N -L" in script
    assert "http://127.0.0.1:${LOCAL_PORT}" in script
    assert "POLYMARKET_DUCKDB_UI_TEST_LAUNCH" in script
