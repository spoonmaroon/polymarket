import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_market_monitor_rows_merge_timestamp_equivalent_live_sources(
    tmp_path: Path,
) -> None:
    bundled = tmp_path / "market_monitor_rows_test.cjs"
    subprocess.run(
        [
            str(ROOT / "ui/node_modules/esbuild/bin/esbuild"),
            str(ROOT / "tests/ui/market_monitor_rows_test.ts"),
            "--bundle",
            "--platform=node",
            "--format=cjs",
            f"--outfile={bundled}",
        ],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    subprocess.run(
        ["node", str(bundled)],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
