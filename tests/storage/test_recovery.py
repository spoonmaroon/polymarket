from pathlib import Path

from polymarket_engine.storage.recovery import cleanup_orphaned_tmp, ensure_archive_sentinel


def test_cleanup_orphaned_tmp_removes_only_tmp_files(tmp_path: Path) -> None:
    tmp_file = tmp_path / "source=coinbase" / "event.parquet.tmp"
    final_file = tmp_path / "source=coinbase" / "event.parquet"
    tmp_file.parent.mkdir(parents=True)
    tmp_file.write_bytes(b"partial")
    final_file.write_bytes(b"complete")

    removed = cleanup_orphaned_tmp(tmp_path)

    assert removed == (tmp_file,)
    assert not tmp_file.exists()
    assert final_file.exists()


def test_ensure_archive_sentinel_rejects_missing_sentinel(tmp_path: Path) -> None:
    try:
        ensure_archive_sentinel(tmp_path)
    except RuntimeError as exc:
        assert ".polymarket_archive_root" in str(exc)
    else:
        raise AssertionError("missing sentinel should raise")


def test_ensure_archive_sentinel_accepts_existing_sentinel(tmp_path: Path) -> None:
    (tmp_path / ".polymarket_archive_root").touch()

    ensure_archive_sentinel(tmp_path)
