import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_live_path_status_helper_handles_retained_mc_rows(
    tmp_path: Path,
) -> None:
    bundled = tmp_path / "live_path_status_test.cjs"
    subprocess.run(
        [
            str(ROOT / "ui/node_modules/esbuild/bin/esbuild"),
            str(ROOT / "tests/ui/live_path_status_test.ts"),
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
