# Polymarket Runtime Keeper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a repo-owned startup/watchdog command that can bring the THEPC Polymarket runtime back after service restarts, Docker/WSL delays, or ordinary reboot recovery, and report exactly what is still broken.

**Architecture:** Add a Python `runtime-keeper` CLI that runs inside THEPC WSL and treats Docker Compose as the source of process truth. The keeper starts required Compose services, starts configured optional containers such as the GPU probability worker when they already exist, verifies API/UI/live/probability freshness, writes `data/live/runtime_keeper.json`, and can run once or in a loop. A separate Mac helper verifies the SSH tunnel LaunchAgent so the browser URL on the Mac remains a client-side concern, not part of the WSL process supervisor.

**Tech Stack:** Python 3.11+, existing `polymarket-engine` CLI, Docker Compose, FastAPI runtime endpoints, macOS `launchctl`, Windows Task Scheduler via PowerShell wrapper for WSL startup.

---

## Scope

This plan covers process recovery after the operating systems and Docker engine are available.

It does not set BIOS power-restore policy, Windows auto-login policy, UPS behavior, or hardware wake settings. Those are manual host configuration items. The keeper must make those limits visible in its report instead of pretending to solve them.

## Current Runtime Facts

- THEPC Compose services `collector`, `normalizer`, `outcome-refresh`, and `api` already use `restart: unless-stopped`.
- The current Mac tunnel is a LaunchAgent at `/Users/goon/Library/LaunchAgents/com.goon.polymarket-thepc-api-tunnel.plist` with `RunAtLoad` and `KeepAlive`.
- Prior resilience testing showed runtime-only container restart passed, while full-stack status was blocked by stale/unhealthy `outcome-refresh`.
- The GPU probability worker has existed as `polymarket-rust-collector-gpu-probability-worker-1`, but it may be outside the currently checked-in Compose file. The keeper should start an existing optional container by name, not invent a GPU worker if no container or Compose service exists.

## File Structure

- Create: `src/polymarket_engine/ops/__init__.py`
  - Marks operational helpers as a package.
- Create: `src/polymarket_engine/ops/runtime_keeper.py`
  - Owns keeper config, command runner abstraction, HTTP checks, Docker/Compose actions, JSON report writing, and loop mode.
- Modify: `src/polymarket_engine/cli.py`
  - Adds `runtime-keeper` subcommand and maps args into `RuntimeKeeperConfig`.
- Create: `tests/ops/test_runtime_keeper.py`
  - Unit tests for config, checks, recovery action ordering, report writing, and loop stop behavior.
- Modify: `tests/test_cli.py`
  - Adds parser and command dispatch coverage for `runtime-keeper`.
- Create: `scripts/install_thepc_runtime_keeper.sh`
  - Installs a WSL loop wrapper and a Windows Task Scheduler entry that starts the keeper after THEPC user logon.
- Create: `scripts/check_mac_polymarket_tunnel.sh`
  - Verifies/reloads the Mac LaunchAgent that forwards `127.0.0.1:8000` to THEPC.
- Create: `tests/scripts/test_runtime_keeper_scripts.py`
  - Static tests for installer scripts so quoting and target commands do not drift.
- Modify: `docs/SPOON_DEPLOYMENT.md`
  - Adds a short operator runbook for installing, running, and interpreting the keeper.

## Task 1: Add Runtime Keeper Data Model And Command Builder

**Files:**
- Create: `src/polymarket_engine/ops/__init__.py`
- Create: `src/polymarket_engine/ops/runtime_keeper.py`
- Test: `tests/ops/test_runtime_keeper.py`

- [ ] **Step 1: Write failing tests for config defaults and command construction**

Create `tests/ops/test_runtime_keeper.py`:

```python
from __future__ import annotations

from pathlib import Path

from polymarket_engine.ops.runtime_keeper import CommandResult
from polymarket_engine.ops.runtime_keeper import RuntimeKeeperConfig
from polymarket_engine.ops.runtime_keeper import compose_command


def test_runtime_keeper_defaults_match_thepc_layout() -> None:
    config = RuntimeKeeperConfig()

    assert config.repo == Path("/home/ender/polymarket")
    assert config.data_dir == Path("/home/ender/polymarket-data")
    assert config.compose_file == Path("/home/ender/polymarket/deploy/collector/docker-compose.yml")
    assert config.env_file == Path("/home/ender/polymarket/deploy/collector/.env")
    assert config.api_base_url == "http://127.0.0.1:8000"
    assert config.required_services == ("collector", "normalizer", "outcome-refresh", "api")
    assert config.optional_containers == ("polymarket-rust-collector-gpu-probability-worker-1",)
    assert config.report_path == Path("/home/ender/polymarket-data/live/runtime_keeper.json")


def test_compose_command_uses_env_file_and_compose_file() -> None:
    config = RuntimeKeeperConfig(repo=Path("/repo"), env_file=Path("/repo/.env"))

    assert compose_command(config, "up", "-d", "api") == (
        "docker",
        "compose",
        "--env-file",
        "/repo/.env",
        "-f",
        "/repo/deploy/collector/docker-compose.yml",
        "up",
        "-d",
        "api",
    )


def test_command_result_reports_success_from_return_code() -> None:
    assert CommandResult(("docker", "info"), 0, "ok", "").ok is True
    assert CommandResult(("docker", "info"), 1, "", "boom").ok is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q tests/ops/test_runtime_keeper.py
```

Expected: fail with `ModuleNotFoundError: No module named 'polymarket_engine.ops'`.

- [ ] **Step 3: Add package marker and minimal model implementation**

Create `src/polymarket_engine/ops/__init__.py`:

```python
"""Operational helpers for deployed Polymarket runtime maintenance."""
```

Create `src/polymarket_engine/ops/runtime_keeper.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


DEFAULT_REPO = Path("/home/ender/polymarket")
DEFAULT_DATA_DIR = Path("/home/ender/polymarket-data")
DEFAULT_REQUIRED_SERVICES = ("collector", "normalizer", "outcome-refresh", "api")
DEFAULT_OPTIONAL_CONTAINERS = ("polymarket-rust-collector-gpu-probability-worker-1",)


@dataclass(frozen=True)
class CommandResult:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class CommandRunner(Protocol):
    def run(self, args: tuple[str, ...], *, timeout_seconds: float) -> CommandResult: ...


@dataclass(frozen=True)
class RuntimeKeeperConfig:
    repo: Path = DEFAULT_REPO
    data_dir: Path = DEFAULT_DATA_DIR
    api_base_url: str = "http://127.0.0.1:8000"
    required_services: tuple[str, ...] = DEFAULT_REQUIRED_SERVICES
    optional_containers: tuple[str, ...] = DEFAULT_OPTIONAL_CONTAINERS
    once_timeout_seconds: float = 120.0
    poll_interval_seconds: float = 2.0
    loop_interval_seconds: float = 30.0

    @property
    def compose_file(self) -> Path:
        return self.repo / "deploy" / "collector" / "docker-compose.yml"

    @property
    def env_file(self) -> Path:
        return self.repo / "deploy" / "collector" / ".env"

    @property
    def report_path(self) -> Path:
        return self.data_dir / "live" / "runtime_keeper.json"


def compose_command(config: RuntimeKeeperConfig, *args: str) -> tuple[str, ...]:
    return (
        "docker",
        "compose",
        "--env-file",
        str(config.env_file),
        "-f",
        str(config.compose_file),
        *args,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest -q tests/ops/test_runtime_keeper.py
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/ops tests/ops/test_runtime_keeper.py
git commit -m "Add runtime keeper command model"
```

## Task 2: Add HTTP And Docker Health Evaluation

**Files:**
- Modify: `src/polymarket_engine/ops/runtime_keeper.py`
- Test: `tests/ops/test_runtime_keeper.py`

- [ ] **Step 1: Add failing tests for runtime health checks**

Append to `tests/ops/test_runtime_keeper.py`:

```python
from polymarket_engine.ops.runtime_keeper import HttpResult
from polymarket_engine.ops.runtime_keeper import evaluate_http_checks
from polymarket_engine.ops.runtime_keeper import evaluate_optional_container
from polymarket_engine.ops.runtime_keeper import evaluate_required_service


def test_required_service_is_ok_when_compose_reports_running() -> None:
    result = CommandResult(("docker", "compose", "ps"), 0, "api\n", "")

    check = evaluate_required_service("api", result)

    assert check.name == "compose:api"
    assert check.ok is True
    assert check.detail == "running"


def test_required_service_fails_when_compose_reports_no_service() -> None:
    result = CommandResult(("docker", "compose", "ps"), 0, "", "")

    check = evaluate_required_service("api", result)

    assert check.ok is False
    assert check.detail == "not running"


def test_optional_container_is_ok_when_docker_ps_reports_container() -> None:
    result = CommandResult(("docker", "ps"), 0, "polymarket-rust-collector-gpu-probability-worker-1\n", "")

    check = evaluate_optional_container("polymarket-rust-collector-gpu-probability-worker-1", result)

    assert check.name == "container:polymarket-rust-collector-gpu-probability-worker-1"
    assert check.ok is True
    assert check.detail == "running"


def test_http_checks_require_health_ui_live_and_probability_rows() -> None:
    checks = evaluate_http_checks(
        health=HttpResult(200, {"status": "ok"}, ""),
        ui=HttpResult(200, {}, "<title>Probability Runtime</title>"),
        live=HttpResult(200, {"ok": True, "monitor": {"orderbooks": [{"id": 1}]}}, ""),
        probabilities=HttpResult(200, {"ok": True, "state": "OK", "rows": [{"contract": "BTC 5m UP"}]}, ""),
    )

    assert [check.name for check in checks] == [
        "api:/health",
        "api:/",
        "api:/api/runtime/live",
        "api:/api/runtime/probabilities",
    ]
    assert all(check.ok for check in checks)


def test_http_checks_expose_missing_probability_rows() -> None:
    checks = evaluate_http_checks(
        health=HttpResult(200, {"status": "ok"}, ""),
        ui=HttpResult(200, {}, "<title>Probability Runtime</title>"),
        live=HttpResult(200, {"ok": True, "monitor": {"orderbooks": [{"id": 1}]}}, ""),
        probabilities=HttpResult(200, {"ok": True, "state": "OK", "rows": []}, ""),
    )

    probability_check = checks[-1]
    assert probability_check.ok is False
    assert probability_check.detail == "missing probability rows"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q tests/ops/test_runtime_keeper.py
```

Expected: fail with `ImportError` for `HttpResult` or `evaluate_http_checks`.

- [ ] **Step 3: Implement health evaluation helpers**

Add to `src/polymarket_engine/ops/runtime_keeper.py`:

```python
import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class KeeperCheck:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    json_payload: dict[str, Any]
    text: str


class UrlHttpClient:
    def get(self, url: str, *, timeout_seconds: float) -> HttpResult:
        try:
            with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                content_type = response.headers.get("content-type", "")
                payload: dict[str, Any] = {}
                if "json" in content_type:
                    parsed = json.loads(body)
                    if isinstance(parsed, dict):
                        payload = parsed
                return HttpResult(int(response.status), payload, body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return HttpResult(int(exc.code), {}, body)
        except OSError as exc:
            return HttpResult(0, {}, f"{type(exc).__name__}: {exc}")


def evaluate_required_service(service: str, result: CommandResult) -> KeeperCheck:
    running = result.ok and service in {line.strip() for line in result.stdout.splitlines()}
    return KeeperCheck(
        name=f"compose:{service}",
        ok=running,
        detail="running" if running else "not running",
    )


def evaluate_optional_container(container: str, result: CommandResult) -> KeeperCheck:
    running = result.ok and container in {line.strip() for line in result.stdout.splitlines()}
    return KeeperCheck(
        name=f"container:{container}",
        ok=running,
        detail="running" if running else "not running",
    )


def evaluate_http_checks(
    *,
    health: HttpResult,
    ui: HttpResult,
    live: HttpResult,
    probabilities: HttpResult,
) -> tuple[KeeperCheck, ...]:
    live_orderbooks = ((live.json_payload.get("monitor") or {}).get("orderbooks") or [])
    probability_rows = probabilities.json_payload.get("rows") or []
    return (
        KeeperCheck(
            name="api:/health",
            ok=health.status_code == 200 and health.json_payload.get("status") == "ok",
            detail="ok" if health.status_code == 200 and health.json_payload.get("status") == "ok" else f"status={health.status_code}",
        ),
        KeeperCheck(
            name="api:/",
            ok=ui.status_code == 200 and "<title>Probability Runtime</title>" in ui.text,
            detail="ui served" if ui.status_code == 200 and "<title>Probability Runtime</title>" in ui.text else f"status={ui.status_code}",
        ),
        KeeperCheck(
            name="api:/api/runtime/live",
            ok=live.status_code == 200 and live.json_payload.get("ok") is True and len(live_orderbooks) > 0,
            detail="live rows present" if live.status_code == 200 and live.json_payload.get("ok") is True and len(live_orderbooks) > 0 else "missing live rows",
        ),
        KeeperCheck(
            name="api:/api/runtime/probabilities",
            ok=probabilities.status_code == 200
            and probabilities.json_payload.get("ok") is True
            and probabilities.json_payload.get("state") == "OK"
            and len(probability_rows) > 0,
            detail="probability rows present"
            if probabilities.status_code == 200
            and probabilities.json_payload.get("ok") is True
            and probabilities.json_payload.get("state") == "OK"
            and len(probability_rows) > 0
            else "missing probability rows",
        ),
    )


def report_payload(checks: Sequence[KeeperCheck], actions: Sequence[str]) -> dict[str, Any]:
    return {
        "schema_version": "polymarket-runtime-keeper-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ok": all(check.ok for check in checks),
        "actions": list(actions),
        "checks": [asdict(check) for check in checks],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest -q tests/ops/test_runtime_keeper.py
```

Expected: all runtime keeper tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/ops/runtime_keeper.py tests/ops/test_runtime_keeper.py
git commit -m "Add runtime keeper health checks"
```

## Task 3: Add Recovery Orchestration And Report Writing

**Files:**
- Modify: `src/polymarket_engine/ops/runtime_keeper.py`
- Test: `tests/ops/test_runtime_keeper.py`

- [ ] **Step 1: Add failing orchestration test**

Append to `tests/ops/test_runtime_keeper.py`:

```python
from typing import Any

from polymarket_engine.ops.runtime_keeper import RuntimeKeeper


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def run(self, args: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:
        self.calls.append(args)
        if args[:2] == ("docker", "info"):
            return CommandResult(args, 0, "Server: Docker Desktop", "")
        if args[-3:] == ("up", "-d", "collector"):
            return CommandResult(args, 0, "", "")
        if args[-3:] == ("up", "-d", "normalizer"):
            return CommandResult(args, 0, "", "")
        if args[-3:] == ("up", "-d", "outcome-refresh"):
            return CommandResult(args, 0, "", "")
        if args[-3:] == ("up", "-d", "api"):
            return CommandResult(args, 0, "", "")
        if args[:2] == ("docker", "start"):
            return CommandResult(args, 0, args[2] + "\n", "")
        if args[-4:] == ("ps", "--services", "--status", "running"):
            service = args[-5]
            return CommandResult(args, 0, service + "\n", "")
        if args[:2] == ("docker", "ps"):
            return CommandResult(args, 0, "polymarket-rust-collector-gpu-probability-worker-1\n", "")
        return CommandResult(args, 1, "", "unexpected command")


class FakeHttpClient:
    def get(self, url: str, *, timeout_seconds: float) -> HttpResult:
        if url.endswith("/health"):
            return HttpResult(200, {"status": "ok"}, "")
        if url.endswith("/"):
            return HttpResult(200, {}, "<title>Probability Runtime</title>")
        if url.endswith("/api/runtime/live?limit=8"):
            return HttpResult(200, {"ok": True, "monitor": {"orderbooks": [{"id": 1}]}}, "")
        if url.endswith("/api/runtime/probabilities?limit=8"):
            return HttpResult(200, {"ok": True, "state": "OK", "rows": [{"contract": "BTC 5m UP"}]}, "")
        raise AssertionError(url)


def test_runtime_keeper_starts_services_optional_container_and_writes_report(tmp_path: Path) -> None:
    runner = FakeRunner()
    config = RuntimeKeeperConfig(repo=tmp_path / "repo", data_dir=tmp_path / "data")
    keeper = RuntimeKeeper(config=config, runner=runner, http_client=FakeHttpClient())

    payload = keeper.run_once()

    assert payload["ok"] is True
    assert "compose up collector" in payload["actions"]
    assert "docker start polymarket-rust-collector-gpu-probability-worker-1" in payload["actions"]
    assert (tmp_path / "data" / "live" / "runtime_keeper.json").is_file()
    assert any(call[:2] == ("docker", "info") for call in runner.calls)


def test_runtime_keeper_reports_docker_unavailable(tmp_path: Path) -> None:
    class BrokenDockerRunner(FakeRunner):
        def run(self, args: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:
            self.calls.append(args)
            if args[:2] == ("docker", "info"):
                return CommandResult(args, 1, "", "docker unavailable")
            return CommandResult(args, 1, "", "should not run")

    keeper = RuntimeKeeper(
        config=RuntimeKeeperConfig(repo=tmp_path / "repo", data_dir=tmp_path / "data"),
        runner=BrokenDockerRunner(),
        http_client=FakeHttpClient(),
    )

    payload = keeper.run_once()

    assert payload["ok"] is False
    assert payload["checks"][0] == {
        "name": "docker:info",
        "ok": False,
        "detail": "docker unavailable",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
uv run pytest -q tests/ops/test_runtime_keeper.py
```

Expected: fail with `ImportError` for `RuntimeKeeper`.

- [ ] **Step 3: Implement runner, orchestration, and report writing**

Add to `src/polymarket_engine/ops/runtime_keeper.py`:

```python
import subprocess
import time


class SubprocessRunner:
    def run(self, args: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:
        completed = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        return CommandResult(
            args=args,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


class RuntimeKeeper:
    def __init__(
        self,
        *,
        config: RuntimeKeeperConfig,
        runner: CommandRunner | None = None,
        http_client: UrlHttpClient | None = None,
    ) -> None:
        self.config = config
        self.runner = runner or SubprocessRunner()
        self.http_client = http_client or UrlHttpClient()

    def run_once(self) -> dict[str, Any]:
        checks: list[KeeperCheck] = []
        actions: list[str] = []

        docker_info = self.runner.run(("docker", "info"), timeout_seconds=10)
        if not docker_info.ok:
            checks.append(KeeperCheck("docker:info", False, docker_info.stderr.strip() or "docker info failed"))
            payload = report_payload(checks, actions)
            self._write_report(payload)
            return payload
        checks.append(KeeperCheck("docker:info", True, "docker responsive"))

        for service in self.config.required_services:
            command = compose_command(self.config, "up", "-d", service)
            result = self.runner.run(command, timeout_seconds=45)
            actions.append(f"compose up {service}")
            if not result.ok:
                checks.append(KeeperCheck(f"compose:{service}:up", False, result.stderr.strip() or "compose up failed"))

        for container in self.config.optional_containers:
            result = self.runner.run(("docker", "start", container), timeout_seconds=20)
            if result.ok:
                actions.append(f"docker start {container}")
            else:
                actions.append(f"docker start {container} skipped")

        for service in self.config.required_services:
            result = self.runner.run(
                (*compose_command(self.config, "ps", service), "--services", "--status", "running"),
                timeout_seconds=15,
            )
            checks.append(evaluate_required_service(service, result))

        for container in self.config.optional_containers:
            result = self.runner.run(
                ("docker", "ps", "--filter", f"name={container}", "--format", "{{.Names}}"),
                timeout_seconds=15,
            )
            checks.append(evaluate_optional_container(container, result))

        checks.extend(self._http_checks())

        payload = report_payload(checks, actions)
        self._write_report(payload)
        return payload

    def run_loop(self) -> None:
        while True:
            self.run_once()
            time.sleep(self.config.loop_interval_seconds)

    def _http_checks(self) -> tuple[KeeperCheck, ...]:
        base = self.config.api_base_url.rstrip("/")
        return evaluate_http_checks(
            health=self.http_client.get(f"{base}/health", timeout_seconds=5),
            ui=self.http_client.get(f"{base}/", timeout_seconds=5),
            live=self.http_client.get(f"{base}/api/runtime/live?limit=8", timeout_seconds=8),
            probabilities=self.http_client.get(f"{base}/api/runtime/probabilities?limit=8", timeout_seconds=8),
        )

    def _write_report(self, payload: dict[str, Any]) -> None:
        self.config.report_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config.report_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(self.config.report_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
uv run pytest -q tests/ops/test_runtime_keeper.py
```

Expected: all runtime keeper tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/polymarket_engine/ops/runtime_keeper.py tests/ops/test_runtime_keeper.py
git commit -m "Add runtime keeper recovery loop"
```

## Task 4: Add CLI Subcommand

**Files:**
- Modify: `src/polymarket_engine/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing CLI parser test**

Append to `tests/test_cli.py`:

```python
def test_parse_runtime_keeper_args() -> None:
    args = parse_args(
        [
            "runtime-keeper",
            "--repo",
            "/home/ender/polymarket",
            "--data-dir",
            "/home/ender/polymarket-data",
            "--api-base-url",
            "http://127.0.0.1:8000",
            "--optional-container",
            "polymarket-rust-collector-gpu-probability-worker-1",
            "--loop",
            "--loop-interval-seconds",
            "15",
        ]
    )

    assert args.command == "runtime-keeper"
    assert args.repo == Path("/home/ender/polymarket")
    assert args.data_dir == Path("/home/ender/polymarket-data")
    assert args.api_base_url == "http://127.0.0.1:8000"
    assert args.optional_container == ["polymarket-rust-collector-gpu-probability-worker-1"]
    assert args.loop is True
    assert args.loop_interval_seconds == 15.0
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest -q tests/test_cli.py::test_parse_runtime_keeper_args
```

Expected: fail with argparse invalid choice `runtime-keeper`.

- [ ] **Step 3: Add CLI parser and dispatch**

Modify `src/polymarket_engine/cli.py`.

Add parser setup near the other subparsers:

```python
    runtime_keeper = subparsers.add_parser("runtime-keeper")
    runtime_keeper.add_argument("--repo", type=Path, default=Path("/home/ender/polymarket"))
    runtime_keeper.add_argument("--data-dir", type=Path, default=Path("/home/ender/polymarket-data"))
    runtime_keeper.add_argument("--api-base-url", default="http://127.0.0.1:8000")
    runtime_keeper.add_argument(
        "--required-service",
        action="append",
        default=None,
        help="Compose service required to be running. Repeatable.",
    )
    runtime_keeper.add_argument(
        "--optional-container",
        action="append",
        default=None,
        help="Existing Docker container to start and check when present. Repeatable.",
    )
    runtime_keeper.add_argument("--loop", action="store_true")
    runtime_keeper.add_argument("--loop-interval-seconds", type=float, default=30.0)
```

Add dispatch in `run_collect_command` before the retired `collect` branch:

```python
    if args.command == "runtime-keeper":
        return _run_runtime_keeper(args)
```

Add helper near the other `_run_*` helpers:

```python
def _run_runtime_keeper(args: argparse.Namespace) -> int:
    from polymarket_engine.ops.runtime_keeper import DEFAULT_OPTIONAL_CONTAINERS
    from polymarket_engine.ops.runtime_keeper import DEFAULT_REQUIRED_SERVICES
    from polymarket_engine.ops.runtime_keeper import RuntimeKeeper
    from polymarket_engine.ops.runtime_keeper import RuntimeKeeperConfig

    config = RuntimeKeeperConfig(
        repo=args.repo,
        data_dir=args.data_dir,
        api_base_url=args.api_base_url,
        required_services=tuple(args.required_service or DEFAULT_REQUIRED_SERVICES),
        optional_containers=tuple(args.optional_container or DEFAULT_OPTIONAL_CONTAINERS),
        loop_interval_seconds=args.loop_interval_seconds,
    )
    keeper = RuntimeKeeper(config=config)
    if args.loop:
        keeper.run_loop()
        return 0
    payload = keeper.run_once()
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload.get("ok") is True else 1
```

- [ ] **Step 4: Run CLI test**

Run:

```bash
uv run pytest -q tests/test_cli.py::test_parse_runtime_keeper_args
```

Expected: pass.

- [ ] **Step 5: Run runtime keeper once locally in dry THEPC shape**

Run on Mac only to confirm graceful failure when Docker/THEPC layout is absent:

```bash
uv run polymarket-engine runtime-keeper --repo /home/ender/polymarket --data-dir /tmp/polymarket-keeper-local
```

Expected: exits non-zero if Docker or the THEPC repo path is unavailable, writes `/tmp/polymarket-keeper-local/live/runtime_keeper.json`, and does not modify repo files.

- [ ] **Step 6: Commit**

```bash
git add src/polymarket_engine/cli.py tests/test_cli.py
git commit -m "Add runtime keeper CLI"
```

## Task 5: Add THEPC Startup Installer

**Files:**
- Create: `scripts/install_thepc_runtime_keeper.sh`
- Test: `tests/scripts/test_runtime_keeper_scripts.py`

- [ ] **Step 1: Write failing script test**

Create `tests/scripts/test_runtime_keeper_scripts.py`:

```python
from pathlib import Path


REPO = Path(__file__).parents[2]


def test_thepc_runtime_keeper_installer_installs_loop_and_task() -> None:
    script = (REPO / "scripts" / "install_thepc_runtime_keeper.sh").read_text(encoding="utf-8")

    assert "polymarket-engine runtime-keeper" in script
    assert "--loop" in script
    assert "Register-ScheduledTask" in script
    assert "Polymarket Runtime Keeper" in script
    assert "wsl.exe" in script
    assert "Start-Sleep -Seconds 20" in script
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest -q tests/scripts/test_runtime_keeper_scripts.py::test_thepc_runtime_keeper_installer_installs_loop_and_task
```

Expected: fail with `FileNotFoundError` for `scripts/install_thepc_runtime_keeper.sh`.

- [ ] **Step 3: Create THEPC installer script**

Create `scripts/install_thepc_runtime_keeper.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

REPO="${POLYMARKET_REPO:-/home/ender/polymarket}"
DATA_DIR="${POLYMARKET_DATA_DIR:-/home/ender/polymarket-data}"
BIN_DIR="${POLYMARKET_BIN_DIR:-/home/ender/bin}"
WSL_DISTRO="${POLYMARKET_WSL_DISTRO:-Ubuntu}"
WINDOWS_USER_DIR="${POLYMARKET_WINDOWS_USER_DIR:-/mnt/c/Users/ender}"
LOOP_SCRIPT="$BIN_DIR/polymarket-runtime-keeper-loop.sh"
POWERSHELL_SCRIPT="$WINDOWS_USER_DIR/polymarket-runtime-keeper.ps1"
TASK_NAME="Polymarket Runtime Keeper"

mkdir -p "$BIN_DIR" "$DATA_DIR/live"

cat > "$LOOP_SCRIPT" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export PATH="\$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:\$PATH"
cd "$REPO"
exec polymarket-engine runtime-keeper \\
  --repo "$REPO" \\
  --data-dir "$DATA_DIR" \\
  --api-base-url "http://127.0.0.1:8000" \\
  --loop \\
  --loop-interval-seconds 30
EOF
chmod 755 "$LOOP_SCRIPT"

if [ ! -d "$WINDOWS_USER_DIR" ]; then
  echo "Windows user directory missing: $WINDOWS_USER_DIR" >&2
  exit 1
fi

cat > "$POWERSHELL_SCRIPT" <<EOF
\$ErrorActionPreference = 'Stop'
Start-Sleep -Seconds 20
& wsl.exe -d $WSL_DISTRO -- "$LOOP_SCRIPT"
EOF

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "\
\$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File \"\$env:USERPROFILE\\polymarket-runtime-keeper.ps1\"'; \
\$trigger = New-ScheduledTaskTrigger -AtLogOn; \
\$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1); \
Register-ScheduledTask -TaskName '$TASK_NAME' -Action \$action -Trigger \$trigger -Settings \$settings -Force | Out-Null"

echo "Installed $LOOP_SCRIPT"
echo "Installed $POWERSHELL_SCRIPT"
echo "Registered scheduled task: $TASK_NAME"
```

- [ ] **Step 4: Run script test**

Run:

```bash
uv run pytest -q tests/scripts/test_runtime_keeper_scripts.py::test_thepc_runtime_keeper_installer_installs_loop_and_task
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/install_thepc_runtime_keeper.sh tests/scripts/test_runtime_keeper_scripts.py
git commit -m "Add THEPC runtime keeper installer"
```

## Task 6: Add Mac Tunnel Checker

**Files:**
- Create: `scripts/check_mac_polymarket_tunnel.sh`
- Modify: `tests/scripts/test_runtime_keeper_scripts.py`

- [ ] **Step 1: Add failing script test**

Append to `tests/scripts/test_runtime_keeper_scripts.py`:

```python
def test_mac_tunnel_checker_reloads_launch_agent_and_checks_health() -> None:
    script = (REPO / "scripts" / "check_mac_polymarket_tunnel.sh").read_text(encoding="utf-8")

    assert "com.goon.polymarket-thepc-api-tunnel" in script
    assert "launchctl bootstrap" in script
    assert "launchctl kickstart" in script
    assert "http://127.0.0.1:8000/health" in script
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
uv run pytest -q tests/scripts/test_runtime_keeper_scripts.py::test_mac_tunnel_checker_reloads_launch_agent_and_checks_health
```

Expected: fail with `FileNotFoundError` for `scripts/check_mac_polymarket_tunnel.sh`.

- [ ] **Step 3: Create Mac tunnel checker script**

Create `scripts/check_mac_polymarket_tunnel.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

LABEL="com.goon.polymarket-thepc-api-tunnel"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"
HEALTH_URL="${POLYMARKET_TUNNEL_HEALTH_URL:-http://127.0.0.1:8000/health}"

if [ ! -f "$PLIST" ]; then
  echo "missing LaunchAgent plist: $PLIST" >&2
  exit 1
fi

if ! launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  launchctl bootstrap "$DOMAIN" "$PLIST"
fi

launchctl kickstart -k "$DOMAIN/$LABEL"

for _ in $(seq 1 20); do
  if curl -fsS --max-time 2 "$HEALTH_URL" >/dev/null; then
    echo "Mac tunnel OK: $HEALTH_URL"
    exit 0
  fi
  sleep 1
done

echo "Mac tunnel did not become healthy: $HEALTH_URL" >&2
exit 1
```

- [ ] **Step 4: Run script tests**

Run:

```bash
uv run pytest -q tests/scripts/test_runtime_keeper_scripts.py
```

Expected: both script tests pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_mac_polymarket_tunnel.sh tests/scripts/test_runtime_keeper_scripts.py
git commit -m "Add Mac tunnel checker"
```

## Task 7: Add Deployment Runbook

**Files:**
- Modify: `docs/SPOON_DEPLOYMENT.md`
- Test: `tests/docs/test_active_runtime_docs.py`

- [ ] **Step 1: Add failing docs test**

Append to `tests/docs/test_active_runtime_docs.py`:

```python
def test_runtime_keeper_runbook_documents_startup_recovery() -> None:
    text = (REPO / "docs" / "SPOON_DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "Runtime keeper" in text
    assert "polymarket-engine runtime-keeper" in text
    assert "scripts/install_thepc_runtime_keeper.sh" in text
    assert "scripts/check_mac_polymarket_tunnel.sh" in text
    assert "runtime_keeper.json" in text
```

- [ ] **Step 2: Run docs test to verify it fails**

Run:

```bash
uv run pytest -q tests/docs/test_active_runtime_docs.py::test_runtime_keeper_runbook_documents_startup_recovery
```

Expected: fail because `Runtime keeper` is not documented yet.

- [ ] **Step 3: Add runbook section**

Append this section to `docs/SPOON_DEPLOYMENT.md`:

~~~markdown
## Runtime keeper

THEPC can run the repo-owned runtime keeper after Windows logon. The keeper runs inside WSL, starts the Compose services, starts configured optional containers such as the existing GPU probability worker container, verifies the API/UI/live/probability endpoints, and writes `/home/ender/polymarket-data/live/runtime_keeper.json`.

Install on THEPC from WSL:

```bash
cd /home/ender/polymarket
./scripts/install_thepc_runtime_keeper.sh
```

Run one manual check:

```bash
polymarket-engine runtime-keeper \
  --repo /home/ender/polymarket \
  --data-dir /home/ender/polymarket-data \
  --api-base-url http://127.0.0.1:8000
```

Run the Mac tunnel check on the Mac:

```bash
cd /Users/goon/polymarket
./scripts/check_mac_polymarket_tunnel.sh
```

The keeper is not a BIOS or Windows boot configurator. If THEPC does not power back on, Windows does not log in, Tailscale is unavailable, Docker Desktop does not start, or WSL is disabled, the keeper cannot run. Those host-level requirements must be configured and tested separately.
~~~

- [ ] **Step 4: Run docs test**

Run:

```bash
uv run pytest -q tests/docs/test_active_runtime_docs.py::test_runtime_keeper_runbook_documents_startup_recovery
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add docs/SPOON_DEPLOYMENT.md tests/docs/test_active_runtime_docs.py
git commit -m "Document runtime keeper startup recovery"
```

## Task 8: Verify On THEPC And Mac

**Files:**
- No source edits expected.
- Verification uses THEPC and Mac live state.

- [ ] **Step 1: Run local focused test suite**

Run:

```bash
uv run pytest -q \
  tests/ops/test_runtime_keeper.py \
  tests/test_cli.py::test_parse_runtime_keeper_args \
  tests/scripts/test_runtime_keeper_scripts.py \
  tests/docs/test_active_runtime_docs.py::test_runtime_keeper_runbook_documents_startup_recovery
```

Expected: all selected tests pass.

- [ ] **Step 2: Run static style check for touched files**

Run:

```bash
uv run ruff check \
  src/polymarket_engine/ops/runtime_keeper.py \
  src/polymarket_engine/cli.py \
  tests/ops/test_runtime_keeper.py \
  tests/test_cli.py \
  tests/scripts/test_runtime_keeper_scripts.py \
  tests/docs/test_active_runtime_docs.py
```

Expected: no Ruff errors.

- [ ] **Step 3: Copy current branch to THEPC through the existing deploy path or a clean branch**

Use the existing deployment path only after the worktree is clean:

```bash
./scripts/deploy_pc.sh
```

Expected: deploy script completes, API health is OK, and THEPC repo has the keeper command installed in the normalizer/API image.

- [ ] **Step 4: Run the keeper once on THEPC**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd /home/ender/polymarket && polymarket-engine runtime-keeper --repo /home/ender/polymarket --data-dir /home/ender/polymarket-data --api-base-url http://127.0.0.1:8000"'
```

Expected output includes `"ok": true`. The file `/home/ender/polymarket-data/live/runtime_keeper.json` contains checks for Docker, Compose services, optional GPU container, `/health`, `/`, `/api/runtime/live`, and `/api/runtime/probabilities`.

- [ ] **Step 5: Install THEPC scheduled startup**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd /home/ender/polymarket && ./scripts/install_thepc_runtime_keeper.sh"'
```

Expected output:

```text
Installed /home/ender/bin/polymarket-runtime-keeper-loop.sh
Installed /mnt/c/Users/ender/polymarket-runtime-keeper.ps1
Registered scheduled task: Polymarket Runtime Keeper
```

- [ ] **Step 6: Check Mac tunnel**

Run on Mac:

```bash
./scripts/check_mac_polymarket_tunnel.sh
```

Expected:

```text
Mac tunnel OK: http://127.0.0.1:8000/health
```

- [ ] **Step 7: Controlled restart verification**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "cd /home/ender/polymarket && docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml restart collector normalizer api && polymarket-engine runtime-keeper --repo /home/ender/polymarket --data-dir /home/ender/polymarket-data --api-base-url http://127.0.0.1:8000"'
```

Expected: keeper exits 0 and writes an OK report. If `outcome-refresh` remains unhealthy because of the known stale outcome status, the report names that exact check while runtime API/UI checks still show their own status.

- [ ] **Step 8: Commit verification notes if a report is requested**

If the operator wants a persistent verification artifact, create `docs/superpowers/reports/2026-06-06-runtime-keeper-verification.md` with exact commands, timestamps, and output summaries, then commit it:

```bash
git add docs/superpowers/reports/2026-06-06-runtime-keeper-verification.md
git commit -m "Record runtime keeper verification"
```

## Self-Review

- Spec coverage: this plan covers the startup program, required process inventory, restart recovery, live API/UI/probability checks, optional GPU container handling, Mac tunnel recovery, installation hooks, and operator docs.
- Scope check: this is one implementation unit because THEPC recovery and Mac tunnel verification share the same user-visible URL but remain separate scripts. BIOS power restore, Windows boot, Docker Desktop installation, WSL enablement, and Tailscale login stay outside the program.
- Placeholder scan: no deferred requirements are present. Every code-changing task includes concrete test code, implementation code, command lines, and expected results.
- Type consistency: `RuntimeKeeperConfig`, `CommandResult`, `KeeperCheck`, `HttpResult`, `RuntimeKeeper`, and `runtime-keeper` are used consistently across tests, CLI, scripts, and docs.
