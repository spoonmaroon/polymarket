from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from collections.abc import Sequence
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


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
    api_base_url: str = "http://127.0.0.1:8000"
    required_services: tuple[str, ...] = DEFAULT_REQUIRED_SERVICES
    optional_containers: tuple[str, ...] = DEFAULT_OPTIONAL_CONTAINERS
    once_timeout_seconds: float = 120.0
    poll_interval_seconds: float = 2.0
    loop_interval_seconds: float = 30.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "env_file",
            self.env_file or self.repo / "deploy" / "collector" / ".env",
        )

    @property
    def compose_file(self) -> Path:
        return self.repo / "deploy" / "collector" / "docker-compose.yml"

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

        docker_info = runner.run(("docker", "info"), timeout_seconds=10.0)
        if not docker_info.ok:
            checks.append(
                KeeperCheck(
                    name="docker:info",
                    ok=False,
                    detail=docker_info.stderr.strip() or "docker info failed",
                )
            )
            payload = report_payload(checks, actions)
            self._write_report(payload)
            return payload
        checks.append(KeeperCheck(name="docker:info", ok=True, detail="docker responsive"))

        for service in self.config.required_services:
            up_command = compose_command(self.config, "up", "-d", service)
            result = runner.run(up_command, timeout_seconds=self.config.once_timeout_seconds)
            actions.append(f"compose up {service}")
            if not result.ok:
                checks.append(
                    KeeperCheck(
                        name=f"compose:{service}:up",
                        ok=False,
                        detail=result.stderr.strip() or "compose up failed",
                    )
                )

        for container in self.config.optional_containers:
            start_result = runner.run(
                ("docker", "start", container),
                timeout_seconds=self.config.once_timeout_seconds,
            )
            if start_result.ok:
                actions.append(f"docker start {container}")
            else:
                actions.append(f"docker start {container} skipped")

        for service in self.config.required_services:
            checks.append(
                evaluate_required_service(
                    service=service,
                    result=runner.run(
                        (*compose_command(self.config, "ps", service, "--services", "--status", "running"),
                         ),
                        timeout_seconds=self.config.poll_interval_seconds,
                    ),
                )
            )

        for container in self.config.optional_containers:
            checks.append(
                evaluate_optional_container(
                    container=container,
                    result=runner.run(
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

        payload = report_payload(checks, actions)
        self._write_report(payload)
        return payload

    def run_loop(self) -> None:
        while True:
            self.run_once()
            import time

            time.sleep(self.config.loop_interval_seconds)

    def _http_checks(self, client: HttpClient) -> tuple[KeeperCheck, ...]:
        base = self.config.api_base_url.rstrip("/")
        return evaluate_http_checks(
            health=client.get(f"{base}/health", timeout_seconds=5.0),
            ui=client.get(f"{base}/", timeout_seconds=5.0),
            live=client.get(
                f"{base}/api/runtime/live?limit=8",
                timeout_seconds=8.0,
            ),
            probabilities=client.get(
                f"{base}/api/runtime/probabilities?limit=8",
                timeout_seconds=8.0,
            ),
        )

    def _write_report(self, payload: dict[str, Any]) -> None:
        self.config.report_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.config.report_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(self.config.report_path)


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


def _parse_json_payload(body: str, content_type: str) -> dict[str, Any]:
    if "json" not in content_type.lower():
        return {}
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


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
    live_ok = (
        live.status_code == 200
        and live.json_payload.get("ok") is True
        and len(live_orderbooks) > 0
    )
    probabilities_ok = (
        probabilities.status_code == 200
        and probabilities.json_payload.get("ok") is True
        and probabilities.json_payload.get("state") == "OK"
        and len(probability_rows) > 0
    )
    if live_ok:
        live_detail = "live rows present"
    elif _http_error_message(live) is not None:
        live_detail = _http_error_detail(live)
    elif _http_response_failed(live):
        live_detail = _http_failure_detail(live)
    else:
        live_detail = "missing live rows"

    if probabilities_ok:
        probabilities_detail = "probability rows present"
    elif _http_error_message(probabilities) is not None:
        probabilities_detail = _http_error_detail(probabilities)
    elif _http_response_failed(probabilities):
        probabilities_detail = _http_failure_detail(probabilities)
    else:
        probabilities_detail = "missing probability rows"
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


def _http_response_failed(result: HttpResult) -> bool:
    return result.status_code != 200 or not result.json_payload


def _http_error_message(result: HttpResult) -> tuple[str, Any] | None:
    for field in ("error", "detail", "message"):
        if field in result.json_payload:
            return field, result.json_payload[field]
    return None


def _http_error_detail(result: HttpResult) -> str:
    return _http_failure_detail(result)


def _http_failure_detail(result: HttpResult) -> str:
    parts = [
        f"status={result.status_code}",
        f"content_type={result.content_type}",
    ]
    message = _http_error_message(result)
    if message is not None:
        field, value = message
        parts.append(f"{field}={value}")
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
