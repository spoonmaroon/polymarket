from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from polymarket_engine.storage.duckdb_store import DuckDbIngestStore


def _setting(conn: Any, name: str) -> object:
    row = conn.execute(f"select current_setting('{name}')").fetchone()
    assert row is not None
    return row[0]


def test_store_applies_configured_duckdb_settings_to_persistent_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLYMARKET_DUCKDB_THREADS", "1")
    monkeypatch.setenv("POLYMARKET_DUCKDB_MEMORY_LIMIT", "512MiB")
    monkeypatch.setenv("POLYMARKET_DUCKDB_PRESERVE_INSERTION_ORDER", "false")

    with DuckDbIngestStore(tmp_path / "state.duckdb") as store:
        with store._connection() as conn:
            assert _setting(conn, "threads") == 1
            assert _setting(conn, "memory_limit") == "512.0 MiB"
            assert _setting(conn, "preserve_insertion_order") is False


def test_store_applies_configured_duckdb_settings_to_temporary_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLYMARKET_DUCKDB_THREADS", "1")
    monkeypatch.setenv("POLYMARKET_DUCKDB_MEMORY_LIMIT", "512MiB")
    monkeypatch.setenv("POLYMARKET_DUCKDB_PRESERVE_INSERTION_ORDER", "false")

    store = DuckDbIngestStore(tmp_path / "state.duckdb")

    with store._connection() as conn:
        assert _setting(conn, "threads") == 1
        assert _setting(conn, "memory_limit") == "512.0 MiB"
        assert _setting(conn, "preserve_insertion_order") is False


@pytest.mark.parametrize(
    ("env_name", "env_value"),
    (
        ("POLYMARKET_DUCKDB_THREADS", "not-an-int"),
        ("POLYMARKET_DUCKDB_MEMORY_LIMIT", ""),
        ("POLYMARKET_DUCKDB_MEMORY_LIMIT", "not-a-memory-limit"),
        ("POLYMARKET_DUCKDB_PRESERVE_INSERTION_ORDER", "maybe"),
    ),
)
def test_store_rejects_invalid_duckdb_setting_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    env_value: str,
) -> None:
    monkeypatch.setenv(env_name, env_value)

    with pytest.raises(ValueError, match=env_name):
        with DuckDbIngestStore(tmp_path / "state.duckdb")._connection():
            pass
