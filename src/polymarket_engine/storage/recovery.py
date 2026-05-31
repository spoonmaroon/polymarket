from __future__ import annotations

from pathlib import Path


ARCHIVE_SENTINEL = ".polymarket_archive_root"
TMP_SUFFIX = ".tmp"


def ensure_archive_sentinel(raw_root: Path) -> None:
    sentinel = raw_root / ARCHIVE_SENTINEL
    if not sentinel.exists():
        raise RuntimeError(
            f"archive sentinel missing at {sentinel}. "
            "Create it once with `touch data/raw/.polymarket_archive_root` "
            "after confirming this is the intended raw event volume."
        )


def cleanup_orphaned_tmp(raw_root: Path) -> tuple[Path, ...]:
    removed: list[Path] = []
    for path in raw_root.rglob(f"*{TMP_SUFFIX}"):
        if path.is_file():
            path.unlink()
            removed.append(path)
    return tuple(removed)
