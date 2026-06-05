import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_probability_state_keeps_previous_rows_during_empty_refresh(
    tmp_path: Path,
) -> None:
    bundled = tmp_path / "probability_state_test.cjs"
    subprocess.run(
        [
            str(ROOT / "ui/node_modules/esbuild/bin/esbuild"),
            str(ROOT / "tests/ui/probability_state_test.ts"),
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
