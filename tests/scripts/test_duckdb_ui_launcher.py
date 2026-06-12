from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_mac_duckdb_ui_launcher_tunnels_spoon_duckdb_ui() -> None:
    script = (ROOT / "scripts" / "open_duckdb_ui_mac.sh").read_text(
        encoding="utf-8"
    )

    assert 'SPOON_HOST="${SPOON_HOST:-' in script
    assert "100.126.126.1" in script
    assert '"$SPOON_HOST"' in script
    assert "POLYMARKET_DUCKDB_UI_PORT:-4213" in script
    assert "/home/spoon/bin/open-polymarket-duckdb-ui.sh" in script
    assert "/api/meta" in script
    assert "is_spoon_viewer" in script
    assert "clear_stale_local_endpoint" in script
    assert "lsof -tiTCP" in script
    assert "ssh -o ExitOnForwardFailure=yes -f -N -L" in script
    assert 'ssh -n "$SPOON_HOST" "test -x $REMOTE_SCRIPT"' in script
    assert 'ssh -n "$SPOON_HOST" "$REMOTE_SCRIPT --port $REMOTE_PORT"' in script
    assert "http://127.0.0.1:${LOCAL_PORT}" in script
    assert "THEPC" not in script
    assert "POLYMARKET_DUCKDB_UI_TEST_LAUNCH" in script


def test_spoon_duckdb_ui_installer_snapshots_spoon_live_db() -> None:
    script = (ROOT / "scripts" / "install_spoon_duckdb_ui.sh").read_text(
        encoding="utf-8"
    )

    assert 'SPOON_HOST="${SPOON_HOST:-' in script
    assert "100.126.126.1" in script
    assert '"$SPOON_HOST"' in script
    assert "/home/spoon/polymarket-data/db/polymarket.duckdb" in script
    assert "/home/spoon/polymarket-data/duckdb-ui/current-polymarket.duckdb" in script
    assert "polymarket_duckdb_viewer.py" in script
    assert "cp --reflink=auto --sparse=always" in script
    assert '"$SOURCE_DB.wal"' in script
    assert 'rm -f "$SNAPSHOT_DB.wal"' in script
    assert 'mv "$SNAPSHOT_TMP.wal" "$SNAPSHOT_DB.wal"' in script
    assert 'cat > "$REMOTE_SCRIPT"' in script
    assert "POLYMARKET_REPO" in script
    assert "/home/spoon/polymarket-main" in script
    assert "docker-compose.spoon-cpu-authority.yml" in script
    assert "deploy/collector/.env" in script
    assert "for service in normalizer outcome-refresh" in script
    assert "trap restart_duckdb_services EXIT" in script
    assert "for attempt in 1 2 3 4 5" in script
    assert "ionice -c2 -n7" in script
    assert "nice -n 10" in script
    assert "POLYMARKET_DUCKDB_UI_INSTALL_ONLY" in script
    assert "/api/meta" in script
    assert "ThreadingHTTPServer" in script
    assert "Snapshot generated" in script
    assert '< /dev/null >/dev/null 2>> "$LOG_FILE" &' in script
