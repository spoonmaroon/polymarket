from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "migrate_mac_data_to_spoon.sh"


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _local_repo(tmp_path: Path) -> Path:
    local_repo = tmp_path / "local"
    for child in ("data/raw", "data/db", "data/live", "logs"):
        (local_repo / child).mkdir(parents=True)
    return local_repo


def test_migration_script_does_not_use_gnu_only_rsync_info_flag(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    rejected_flag = tmp_path / "rejected-flag"
    _write_executable(fake_bin / "ssh", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(
        fake_bin / "rsync",
        f"""#!/usr/bin/env bash
for arg in "$@"; do
  if [ "$arg" = "--info=progress2" ]; then
    echo rejected > "{rejected_flag}"
    exit 64
  fi
done
exit 0
""",
    )

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "LOCAL_REPO": str(_local_repo(tmp_path)),
            "REMOTE_HOST": "spoon",
            "REMOTE_DATA_DIR": "/tmp/polymarket-data",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert not rejected_flag.exists()


def test_migration_script_fails_when_rsync_fails(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "ssh", "#!/usr/bin/env bash\nexit 0\n")
    _write_executable(fake_bin / "rsync", "#!/usr/bin/env bash\nexit 23\n")

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "LOCAL_REPO": str(_local_repo(tmp_path)),
            "REMOTE_HOST": "spoon",
            "REMOTE_DATA_DIR": "/tmp/polymarket-data",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 23
