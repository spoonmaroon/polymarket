from pathlib import Path


REPO = Path(__file__).parents[2]


def test_thepc_runtime_keeper_installer_installs_loop_and_task() -> None:
    script = (REPO / "scripts" / "install_thepc_runtime_keeper.sh").read_text(
        encoding="utf-8"
    )

    assert 'exec "\\$ENGINE_BIN" runtime-keeper' in script
    assert 'python3 -m pip install --user --break-system-packages -e "$REPO"' in script
    assert "POLYMARKET_ENGINE_BIN" in script
    assert "$HOME/.local/bin/polymarket-engine" in script
    assert "POWERSHELL_SCRIPT_WINDOWS" in script
    assert 'wslpath -w "$POWERSHELL_SCRIPT"' in script
    assert '$env:USERPROFILE\\polymarket-runtime-keeper.ps1' not in script
    assert '--compose-file "$REPO/deploy/collector/docker-compose.yml"' in script
    assert '--compose-file "$REPO/deploy/collector/docker-compose.thepc-gpu-api.yml"' in script
    assert '--required-service "api"' in script
    assert '--required-service "gpu-probability-worker"' in script
    assert '--recovery-warmup-min-seconds 15' in script
    assert '--recovery-required-healthy-cycles 1' in script
    assert "--loop" in script
    assert "Register-ScheduledTask" in script
    assert "Polymarket Runtime Keeper" in script
    assert "wsl.exe" in script
    assert "Start-Sleep -Seconds 20" in script
    assert "while (\\$true)" in script
    assert "systemctl --user start polymarket-runtime-keeper.service" in script
    assert "sleep 3600" in script
    assert "polymarket-runtime-keeper.service" in script
    assert "Restart=always" in script
    assert "systemctl --user enable --now polymarket-runtime-keeper.service" in script


def test_mac_tunnel_checker_reloads_launch_agent_and_checks_health() -> None:
    script = (REPO / "scripts" / "check_mac_polymarket_tunnel.sh").read_text(
        encoding="utf-8"
    )

    assert "com.goon.polymarket-thepc-api-tunnel" in script
    assert "launchctl bootstrap" in script
    assert "launchctl kickstart" in script
    assert "http://127.0.0.1:8000/health" in script
    assert "run_mac_polymarket_tunnel.sh" in script
    assert "ProgramArguments" in script


def test_mac_tunnel_runner_targets_current_thepc_wsl_ip() -> None:
    script_path = REPO / "scripts" / "run_mac_polymarket_tunnel.sh"

    assert script_path.exists()
    script = script_path.read_text(encoding="utf-8")

    assert "wsl.exe -d" in script
    assert "hostname -I" in script
    assert "127.0.0.1:8000:${wsl_ip}:8000" in script
    assert "127.0.0.1:8000:127.0.0.1:8000" not in script


def test_gpu_node_runtime_keeper_installer_is_native_linux_without_wsl() -> None:
    script = (REPO / "scripts" / "install_gpu_node_runtime_keeper.sh").read_text(
        encoding="utf-8"
    )

    assert 'REPO="${POLYMARKET_REPO:-/home/enoch/polymarket}"' in script
    assert 'DATA_DIR="${POLYMARKET_DATA_DIR:-/home/enoch/polymarket-data}"' in script
    assert 'BIN_DIR="${POLYMARKET_BIN_DIR:-/home/enoch/bin}"' in script
    assert 'exec "$ENGINE_BIN" runtime-keeper' in script
    assert '--compose-file "$REPO/deploy/collector/docker-compose.yml"' in script
    assert '--compose-file "$REPO/deploy/collector/docker-compose.thepc-gpu-api.yml"' in script
    assert '--required-service "api"' in script
    assert '--required-service "gpu-probability-worker"' in script
    assert '--loop-interval-seconds 30' in script
    assert "loginctl enable-linger" in script
    assert '$USER' in script
    assert "command -v loginctl" in script
    assert "polymarket-runtime-keeper.service" in script
    assert "systemctl --user enable --now polymarket-runtime-keeper.service" in script
    assert "nohup \"$LOOP_SCRIPT\"" in script
    assert ">> \"$DATA_DIR/logs/runtime-keeper.log\"" in script
    assert "echo \"$!\" > \"$DATA_DIR/live/runtime-keeper.pid\"" in script
    assert "wsl.exe" not in script
    assert "powershell.exe" not in script
    assert "Register-ScheduledTask" not in script
