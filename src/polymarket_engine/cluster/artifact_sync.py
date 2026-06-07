from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MirrorPlan:
    source_host: str
    source_path: str
    target_path: str
    timeout_seconds: int


def build_rsync_command(plan: MirrorPlan) -> list[str]:
    if plan.timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    return [
        "rsync",
        "-az",
        "--delay-updates",
        "--partial",
        f"--timeout={plan.timeout_seconds}",
        f"{plan.source_host}:{plan.source_path}",
        plan.target_path,
    ]


def mirror_is_fresh(
    *,
    path: Path,
    now_seconds: float,
    mtime_seconds: float,
    max_age_seconds: float,
) -> bool:
    if max_age_seconds <= 0:
        raise ValueError("max_age_seconds must be positive")
    if not path.exists():
        return False
    return now_seconds - mtime_seconds <= max_age_seconds
