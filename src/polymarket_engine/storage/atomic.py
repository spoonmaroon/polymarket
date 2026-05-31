from __future__ import annotations

import os
from pathlib import Path


def durable_link(tmp: Path, final: Path, *, parent_fsync: bool = True) -> None:
    _fsync_file(tmp)
    os.link(tmp, final)
    if parent_fsync:
        _fsync_dir(final.parent)


def durable_replace(tmp: Path, final: Path, *, parent_fsync: bool = True) -> None:
    _fsync_file(tmp)
    os.replace(tmp, final)
    if parent_fsync:
        _fsync_dir(final.parent)


def _fsync_file(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        os.close(fd)
