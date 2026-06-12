from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
import uuid
from collections.abc import Sequence
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from polymarket_engine.ops.recovery_manager import RecoveryConfig
from polymarket_engine.ops.recovery_manager import RecoveryInputs
from polymarket_engine.ops.recovery_manager import evaluate_recovery_state
from polymarket_engine.ops.recovery_manager import write_recovery_status


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
    env_file: Path | None = None
    compose_files: tuple[Path, ...] = ()
    api_base_url: str = "http://127.0.0.1:8000"
    required_services: tuple[str, ...] = DEFAULT_REQUIRED_SERVICES
    optional_containers: tuple[str, ...] = DEFAULT_OPTIONAL_CONTAINERS
    once_timeout_seconds: float = 120.0
    poll_interval_seconds: float = 2.0
    loop_interval_seconds: float = 30.0
    recovery_status_path: Path | None = None
    recovery_boot_id: str = field(default_factory=lambda: f"runtime-keeper-{uuid.uuid4().hex}")
    recovery_startup_ts: datetime | None = None
    recovery_config: RecoveryConfig = field(default_factory=RecoveryConfig)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "env_file",
            self.env_file or self.repo / "deploy" / "collector" / ".env",
        )
        object.__setattr__(
            self,
            "compose_files",
            self.compose_files or (self.repo / "deploy" / "collector" / "docker-compose.yml",),
        )
        object.__setattr__(
            self,
            "recovery_status_path",
            self.recovery_status_path or self.data_dir / "live" / "recovery_status.json",
        )
        object.__setattr__(
            self,
            "recovery_startup_ts",
            self.recovery_startup_ts or datetime.now(timezone.utc),
        )

    @property
    def compose_file(self) -> Path:
        return self.compose_files[0]

    @property
    def report_path(self) -> Path:
        return self.data_dir / "live" / "runtime_keeper.json"


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
    content_type: str = ""


class HttpClient(Protocol):
    def get(self, url: str, *, timeout_seconds: float) -> HttpResult: ...


@dataclass(frozen=True)
class RuntimeKeeper:
    config: RuntimeKeeperConfig
    runner: CommandRunner | None = None
    http_client: HttpClient | None = None

    def run_once(self) -> dict[str, Any]:
        checks: list[KeeperCheck] = []
        actions: list[str] = []

        runner = self.runner or SubprocessRunner()
        client = self.http_client or UrlHttpClient()

        docker_info = run_command_safely(runner, ("docker", "info"), timeout_seconds=10.0)
        if not docker_info.ok:
            checks.append(
                KeeperCheck(
                    name="docker:info",
                    ok=False,
                    detail=docker_info.stderr.strip() or "docker info failed",
                )
            )
            generated_at = datetime.now(timezone.utc)
            payload = report_payload(checks, actions, generated_at=generated_at)
            self._write_report(payload)
            self._write_recovery_status(checks, generated_at=generated_at)
            return payload
        checks.append(KeeperCheck(name="docker:info", ok=True, detail="docker responsive"))

        service_checks: list[KeeperCheck] = []
        for service in self.config.required_services:
            ps_command = compose_command(self.config, "ps", service, "--services", "--status", "running")
            service_check = evaluate_required_service(
                service=service,
                result=run_command_safely(
                    runner,
                    ps_command,
                    timeout_seconds=self.config.poll_interval_seconds,
                ),
            )
            if not service_check.ok:
                up_command = compose_command(self.config, "up", "-d", service)
                result = run_command_safely(
                    runner,
                    up_command,
                    timeout_seconds=self.config.once_timeout_seconds,
                )
                actions.append(f"compose up {service}")
                if not result.ok:
                    checks.append(
                        KeeperCheck(
                            name=f"compose:{service}:up",
                            ok=False,
                            detail=result.stderr.strip() or "compose up failed",
                        )
                    )
                service_check = evaluate_required_service(
                    service=service,
                    result=run_command_safely(
                        runner,
                        ps_command,
                        timeout_seconds=self.config.poll_interval_seconds,
                    ),
                )
            service_checks.append(service_check)

        for container in self.config.optional_containers:
            start_result = run_command_safely(
                runner,
                ("docker", "start", container),
                timeout_seconds=self.config.once_timeout_seconds,
            )
            if start_result.ok:
                actions.append(f"docker start {container}")
            else:
                actions.append(f"docker start {container} skipped")

        checks.extend(service_checks)

        for container in self.config.optional_containers:
            checks.append(
                evaluate_optional_container(
                    container=container,
                    result=run_command_safely(
                        runner,
                        (
                            "docker",
                            "ps",
                            "--filter",
                            f"name={container}",
                            "--format",
                            "{{.Names}}",
                        ),
                        timeout_seconds=self.config.poll_interval_seconds,
                    ),
                )
            )

        checks.extend(self._http_checks(client))

        generated_at = datetime.now(timezone.utc)
        payload = report_payload(checks, actions, generated_at=generated_at)
        self._write_report(payload)
        self._write_recovery_status(checks, generated_at=generated_at)
        return payload

    def run_loop(self) -> None:
        while True:
            self.run_once()
            import time

            time.sleep(self.config.loop_interval_seconds)

    def _http_checks(self, client: HttpClient) -> tuple[KeeperCheck, ...]:
        base = self.config.api_base_url.rstrip("/")
        return evaluate_http_checks(
            health=get_http_safely(client, f"{base}/health", timeout_seconds=5.0),
            ui=get_http_safely(client, f"{base}/", timeout_seconds=5.0),
            live=get_http_safely(
                client,
                f"{base}/api/runtime/live?limit=8",
                timeout_seconds=8.0,
            ),
            probabilities=get_http_safely(
                client,
                f"{base}/api/runtime/probabilities?limit=8",
                timeout_seconds=8.0,
            ),
        )

    def _write_report(self, payload: dict[str, Any]) -> None:
        self.config.report_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.config.report_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(self.config.report_path)

    def _write_recovery_status(
        self,
        checks: Sequence[KeeperCheck],
        *,
        generated_at: datetime,
    ) -> None:
        recovery_status_path = self.config.recovery_status_path
        recovery_startup_ts = self.config.recovery_startup_ts
        if recovery_status_path is None:
            raise ValueError("recovery_status_path must be configured")
        if recovery_startup_ts is None:
            raise ValueError("recovery_startup_ts must be configured")
        previous_healthy_cycles = read_previous_consecutive_healthy_cycles(
            recovery_status_path,
            boot_id=self.config.recovery_boot_id,
        )
        state = evaluate_recovery_state(
            recovery_inputs_from_checks(
                checks,
                boot_id=self.config.recovery_boot_id,
                startup_ts=recovery_startup_ts,
                now=generated_at,
                previous_consecutive_healthy_cycles=previous_healthy_cycles,
            ),
            self.config.recovery_config,
        )
        write_recovery_status(recovery_status_path, state, generated_at=generated_at)


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


class UrlHttpClient:
    def get(self, url: str, *, timeout_seconds: float) -> HttpResult:
        try:
            with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                content_type = response.headers.get("content-type", "")
                return HttpResult(
                    status_code=int(response.status),
                    json_payload=_parse_json_payload(body, content_type),
                    text=body,
                    content_type=content_type,
                )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            content_type = exc.headers.get("content-type", "")
            return HttpResult(
                status_code=int(exc.code),
                json_payload=_parse_json_payload(body, content_type),
                text=body,
                content_type=content_type,
            )
        except OSError as exc:
            return HttpResult(status_code=0, json_payload={}, text=f"{type(exc).__name__}: {exc}")


def run_command_safely(
    runner: CommandRunner,
    args: tuple[str, ...],
    *,
    timeout_seconds: float,
) -> CommandResult:
    try:
        return runner.run(args, timeout_seconds=timeout_seconds)
    except Exception as exc:
        return CommandResult(args=args, returncode=1, stdout="", stderr=_exception_detail(exc))


def get_http_safely(
    client: HttpClient,
    url: str,
    *,
    timeout_seconds: float,
) -> HttpResult:
    try:
        return client.get(url, timeout_seconds=timeout_seconds)
    except Exception as exc:
        return HttpResult(status_code=0, json_payload={}, text=_exception_detail(exc))


def _exception_detail(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _parse_json_payload(body: str, content_type: str) -> dict[str, Any]:
    if "json" not in content_type.lower():
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def compose_command(config: RuntimeKeeperConfig, *args: str) -> tuple[str, ...]:
    compose_file_args = tuple(
        part
        for compose_file in config.compose_files
        for part in ("-f", str(compose_file))
    )
    return (
        "docker",
        "compose",
        "--env-file",
        str(config.env_file),
        *compose_file_args,
        *args,
    )


def evaluate_required_service(service: str, result: CommandResult) -> KeeperCheck:
    is_running = result.ok and service in {line.strip() for line in result.stdout.splitlines()}
    return KeeperCheck(
        name=f"compose:{service}",
        ok=is_running,
        detail="running" if is_running else "not running",
    )


def evaluate_optional_container(container: str, result: CommandResult) -> KeeperCheck:
    is_running = result.ok and container in {
        line.strip() for line in result.stdout.splitlines()
    }
    return KeeperCheck(
        name=f"container:{container}",
        ok=is_running,
        detail="running" if is_running else "not running",
    )


def evaluate_http_checks(
    *,
    health: HttpResult,
    ui: HttpResult,
    live: HttpResult,
    probabilities: HttpResult,
) -> tuple[KeeperCheck, ...]:
    live_monitor = (live.json_payload.get("monitor") or {})
    live_orderbooks = live_monitor.get("orderbooks") or []
    probability_rows = probabilities.json_payload.get("rows") or []
    health_ok = health.status_code == 200 and health.json_payload.get("status") == "ok"
    ui_ok = ui.status_code == 200 and "<title>Probability Runtime</title>" in ui.text
    live_status_ok = _nested_ok(
        live.json_payload.get("status"),
        default=live.json_payload.get("ok") is True,
    )
    live_gates_ok = _nested_ok(
        live.json_payload.get("gates"),
        default=live.json_payload.get("ok") is True,
    )
    live_ok = (
        live.status_code == 200
        and live_status_ok
        and live_gates_ok
        and len(live_orderbooks) > 0
    )
    probabilities_ok = _probabilities_recovery_ok(
        probabilities,
        probability_row_count=len(probability_rows),
    )
    live_detail = _live_check_detail(
        live=live,
        live_ok=live_ok,
        live_orderbook_count=len(live_orderbooks),
    )
    probabilities_detail = _probability_check_detail(
        probabilities=probabilities,
        probabilities_ok=probabilities_ok,
        probability_row_count=len(probability_rows),
    )
    return (
        KeeperCheck(
            name="api:/health",
            ok=health_ok,
            detail=(
                "ok"
                if health_ok
                else _http_failure_detail(health)
            ),
        ),
        KeeperCheck(
            name="api:/",
            ok=ui_ok,
            detail=(
                "ui served"
                if ui_ok
                else _http_failure_detail(ui)
            ),
        ),
        KeeperCheck(
            name="api:/api/runtime/live",
            ok=live_ok,
            detail=live_detail,
        ),
        KeeperCheck(
            name="api:/api/runtime/probabilities",
            ok=probabilities_ok,
            detail=probabilities_detail,
        ),
    )


def recovery_inputs_from_checks(
    checks: Sequence[KeeperCheck],
    *,
    boot_id: str,
    startup_ts: datetime,
    now: datetime,
    previous_consecutive_healthy_cycles: int,
) -> RecoveryInputs:
    check_by_name = {check.name: check for check in checks}
    failed_checks = tuple(check for check in checks if not check.ok)
    all_checks_ok = all(check.ok for check in checks)
    live_ok = _check_ok(check_by_name, "api:/api/runtime/live")
    probabilities_ok = _check_ok(check_by_name, "api:/api/runtime/probabilities")
    consecutive_healthy_cycles = (
        previous_consecutive_healthy_cycles + 1 if all_checks_ok else 0
    )

    return RecoveryInputs(
        boot_id=boot_id,
        startup_ts=startup_ts,
        now=now,
        status_ok=live_ok,
        normalized_health_ok=not _failed_check_mentions(failed_checks, "normalized"),
        api_ok=_check_ok(check_by_name, "api:/health"),
        price_fresh=live_ok,
        orderbook_fresh=live_ok,
        probability_inputs_fresh=probabilities_ok,
        volatility_fresh=not _failed_check_mentions(failed_checks, "volatility"),
        target_fresh=not _failed_check_mentions(failed_checks, "target"),
        sigma_valid=not _failed_check_mentions(failed_checks, "sigma"),
        k_stable=not _failed_check_mentions(
            failed_checks,
            "k_unstable",
            "threshold_unstable",
        ),
        duckdb_ok=not _failed_check_mentions(failed_checks, "duckdb"),
        cpu_percent=None,
        memory_mb=None,
        queue_length=None,
        recent_api_blocked=_failed_check_mentions(
            failed_checks,
            "api_blocked",
            "status=403",
            "status=429",
        ),
        recent_decode_error=_failed_check_mentions(
            failed_checks,
            "decode",
            "json parse failed",
        ),
        consecutive_healthy_cycles=consecutive_healthy_cycles,
        recovery_attempts=0,
    )


def read_previous_consecutive_healthy_cycles(path: Path, *, boot_id: str) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(payload, dict) or payload.get("boot_id") != boot_id:
        return 0
    value = payload.get("consecutive_healthy_cycles")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _check_ok(check_by_name: dict[str, KeeperCheck], name: str) -> bool:
    check = check_by_name.get(name)
    return bool(check and check.ok)


def _nested_ok(value: object, *, default: bool) -> bool:
    if isinstance(value, dict):
        return value.get("ok") is True
    return default


def _probabilities_recovery_ok(
    probabilities: HttpResult,
    *,
    probability_row_count: int,
) -> bool:
    if probabilities.status_code != 200 or probabilities.json_payload.get("ok") is not True:
        return False
    state = probabilities.json_payload.get("state")
    if state == "OK":
        return probability_row_count > 0
    if state != "OFFLOAD_BLOCKED":
        return False

    offload = probabilities.json_payload.get("offload")
    reason_codes: set[str] = set()
    if isinstance(offload, dict):
        reason_codes = {
            str(reason)
            for reason in offload.get("reason_codes", [])
            if reason is not None
        }
    if "no_probability_inputs" in reason_codes:
        return False

    lanes = probabilities.json_payload.get("lanes")
    nowcast_count = lanes.get("NOWCAST", 0) if isinstance(lanes, dict) else 0
    last_good_rows = probabilities.json_payload.get("last_good_rows") or []
    return probability_row_count > 0 or bool(last_good_rows) or int(nowcast_count or 0) > 0


def _failed_check_mentions(checks: Sequence[KeeperCheck], *needles: str) -> bool:
    normalized_needles = tuple(needle.lower() for needle in needles)
    return any(
        any(needle in f"{check.name} {check.detail}".lower() for needle in normalized_needles)
        for check in checks
    )


def _http_response_failed(result: HttpResult) -> bool:
    return result.status_code != 200 or not result.json_payload


def _live_check_detail(
    *,
    live: HttpResult,
    live_ok: bool,
    live_orderbook_count: int,
) -> str:
    if live_ok:
        return "live rows present"
    if _http_error_message(live) is not None or _http_response_failed(live):
        return _http_failure_detail(live)
    if live.json_payload.get("ok") is not True:
        return _http_failure_detail(live, semantic_detail="ok_not_true")
    if live_orderbook_count == 0:
        return "missing live rows"
    return _http_failure_detail(live)


def _probability_check_detail(
    *,
    probabilities: HttpResult,
    probabilities_ok: bool,
    probability_row_count: int,
) -> str:
    if probabilities_ok:
        if probabilities.json_payload.get("state") == "OFFLOAD_BLOCKED":
            return "probability runtime warm"
        return "probability rows present"
    if _http_error_message(probabilities) is not None or _http_response_failed(probabilities):
        return _http_failure_detail(probabilities)
    if probabilities.json_payload.get("ok") is not True:
        return _http_failure_detail(probabilities, semantic_detail="ok_not_true")
    state = probabilities.json_payload.get("state")
    if state != "OK":
        return _http_failure_detail(probabilities, semantic_detail=f"state={state}")
    if probability_row_count == 0:
        return "missing probability rows"
    return _http_failure_detail(probabilities)


def _http_error_message(result: HttpResult) -> tuple[str, Any] | None:
    for message_field in ("error", "detail", "message"):
        if message_field in result.json_payload:
            return message_field, result.json_payload[message_field]
    return None


def _http_error_detail(result: HttpResult) -> str:
    return _http_failure_detail(result)


def _http_failure_detail(result: HttpResult, *, semantic_detail: str | None = None) -> str:
    parts = [
        f"status={result.status_code}",
        f"content_type={result.content_type}",
    ]
    message = _http_error_message(result)
    if message is not None:
        field, value = message
        parts.append(f"{field}={value}")
    elif semantic_detail is not None:
        parts.append(semantic_detail)
    parts.append(f"body_prefix={result.text[:120]}")
    return " ".join(parts)


def report_payload(
    checks: Sequence[KeeperCheck],
    actions: Sequence[str],
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    now = generated_at or datetime.now(timezone.utc)
    return {
        "schema_version": "polymarket-runtime-keeper-v1",
        "generated_at": now.isoformat(),
        "ok": all(check.ok for check in checks),
        "actions": list(actions),
        "checks": [asdict(check) for check in checks],
    }
