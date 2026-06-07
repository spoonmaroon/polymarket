from pathlib import Path

from polymarket_engine.cluster.artifact_sync import MirrorPlan
from polymarket_engine.cluster.artifact_sync import build_rsync_command
from polymarket_engine.cluster.artifact_sync import mirror_is_fresh


def test_build_rsync_command_uses_delay_updates_and_timeout() -> None:
    command = build_rsync_command(
        MirrorPlan(
            source_host="spoon",
            source_path="/home/spoon/polymarket-data/live/probability_inputs.json",
            target_path="/home/ender/polymarket-data/live/probability_inputs.json",
            timeout_seconds=5,
        )
    )

    assert command == [
        "rsync",
        "-az",
        "--delay-updates",
        "--partial",
        "--timeout=5",
        "spoon:/home/spoon/polymarket-data/live/probability_inputs.json",
        "/home/ender/polymarket-data/live/probability_inputs.json",
    ]


def test_mirror_is_fresh_uses_file_mtime(tmp_path: Path) -> None:
    path = tmp_path / "probability_inputs.json"
    path.write_text("{}", encoding="utf-8")

    assert mirror_is_fresh(
        path=path,
        now_seconds=100.0,
        mtime_seconds=97.0,
        max_age_seconds=5.0,
    )
    assert not mirror_is_fresh(
        path=path,
        now_seconds=100.0,
        mtime_seconds=90.0,
        max_age_seconds=5.0,
    )
