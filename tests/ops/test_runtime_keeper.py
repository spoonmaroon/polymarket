from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from typing import cast

import pytest

from polymarket_engine.ops.recovery_manager import RecoveryConfig
from polymarket_engine.ops.runtime_keeper import (
    CommandResult,
    HttpResult,
    KeeperCheck,
    RuntimeKeeper,
    RuntimeKeeperConfig,
    compose_command,
    evaluate_http_checks,
    evaluate_optional_container,
    evaluate_required_service,
    recovery_inputs_from_checks,
)


BASE = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)


def test_runtime_keeper_defaults_match_thepc_layout() -> None:
    config = RuntimeKeeperConfig()

    assert config.repo == Path("/home/ender/polymarket")
    assert config.data_dir == Path("/home/ender/polymarket-data")
    assert config.compose_file == Path("/home/ender/polymarket/deploy/collector/docker-compose.yml")
    assert config.compose_files == (
        Path("/home/ender/polymarket/deploy/collector/docker-compose.yml"),
    )
    assert config.env_file == Path("/home/ender/polymarket/deploy/collector/.env")
    assert config.api_base_url == "http://127.0.0.1:8000"
    assert config.required_services == ("collector", "normalizer", "outcome-refresh", "api")
    assert config.optional_containers == ("polymarket-rust-collector-gpu-probability-worker-1",)
    assert config.report_path == Path("/home/ender/polymarket-data/live/runtime_keeper.json")
    assert config.recovery_status_path == Path(
        "/home/ender/polymarket-data/live/recovery_status.json"
    )


def test_default_recovery_boot_ids_are_distinguishable() -> None:
    first = RuntimeKeeperConfig()
    second = RuntimeKeeperConfig()
    explicit = RuntimeKeeperConfig(recovery_boot_id="boot-1")

    assert first.recovery_boot_id != second.recovery_boot_id
    assert explicit.recovery_boot_id == "boot-1"


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


def test_compose_command_uses_all_configured_compose_files() -> None:
    config = RuntimeKeeperConfig(
        repo=Path("/repo"),
        env_file=Path("/repo/.env"),
        compose_files=(Path("/repo/base.yml"), Path("/repo/thepc.yml")),
    )

    assert compose_command(config, "up", "-d", "api") == (
        "docker",
        "compose",
        "--env-file",
        "/repo/.env",
        "-f",
        "/repo/base.yml",
        "-f",
        "/repo/thepc.yml",
        "up",
        "-d",
        "api",
    )


def test_command_result_reports_success_from_return_code() -> None:
    assert CommandResult(("docker", "info"), 0, "ok", "").ok is True
    assert CommandResult(("docker", "info"), 1, "", "boom").ok is False


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
    result = CommandResult(
        ("docker", "ps"),
        0,
        "polymarket-rust-collector-gpu-probability-worker-1\n",
        "",
    )

    check = evaluate_optional_container(
        "polymarket-rust-collector-gpu-probability-worker-1",
        result,
    )

    assert check.name == "container:polymarket-rust-collector-gpu-probability-worker-1"
    assert check.ok is True
    assert check.detail == "running"


def test_http_checks_require_health_ui_live_and_probability_rows() -> None:
    checks = evaluate_http_checks(
        health=HttpResult(200, {"status": "ok"}, ""),
        ui=HttpResult(200, {}, "<title>Probability Runtime</title>"),
        live=HttpResult(
            200,
            {"ok": True, "monitor": {"orderbooks": [{"id": 1}]}},
            "",
        ),
        probabilities=HttpResult(
            200,
            {"ok": True, "state": "OK", "rows": [{"contract": "BTC 5m UP"}]},
            "",
        ),
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
        live=HttpResult(
            200,
            {"ok": True, "monitor": {"orderbooks": [{"id": 1}]}},
            "",
        ),
        probabilities=HttpResult(200, {"ok": True, "state": "OK", "rows": []}, ""),
    )

    probability_check = checks[-1]
    assert probability_check.ok is False
    assert probability_check.detail == "missing probability rows"


def test_http_checks_accept_offload_blocked_when_nowcast_rows_are_fresh() -> None:
    checks = evaluate_http_checks(
        health=HttpResult(200, {"status": "ok"}, ""),
        ui=HttpResult(200, {}, "<title>Probability Runtime</title>"),
        live=HttpResult(
            200,
            {"ok": True, "monitor": {"orderbooks": [{"id": 1}]}},
            "",
        ),
        probabilities=HttpResult(
            200,
            {
                "ok": True,
                "state": "OFFLOAD_BLOCKED",
                "rows": [],
                "lanes": {"NOWCAST": 8},
                "last_good_rows": [{"contract": "BTC 5m UP"}],
                "offload": {
                    "reason_codes": [
                        "runtime_not_ready",
                        "insufficient_healthy_cycles",
                    ]
                },
            },
            "",
        ),
    )

    probability_check = checks[-1]
    assert probability_check.ok is True
    assert probability_check.detail == "probability runtime warm"


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
        if (
            len(args) >= 6
            and args[:2] == ("docker", "compose")
            and args[-4] != "ps"
            and args[-1] == "running"
        ):
            service = args[-4]
            return CommandResult(args, 0, service + "\n", "")
        if args[:2] == ("docker", "ps"):
            return CommandResult(
                args,
                0,
                "polymarket-rust-collector-gpu-probability-worker-1\n",
                "",
            )
        return CommandResult(args, 1, "", "unexpected command")


class StoppedServiceRunner(FakeRunner):
    def __init__(self) -> None:
        super().__init__()
        self.started_services: set[str] = set()

    def run(self, args: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:
        self.calls.append(args)
        if args[:2] == ("docker", "info"):
            return CommandResult(args, 0, "Server: Docker Desktop", "")
        if args[-3:-1] == ("up", "-d"):
            self.started_services.add(args[-1])
            return CommandResult(args, 0, "", "")
        if (
            len(args) >= 6
            and args[:2] == ("docker", "compose")
            and args[-4] != "ps"
            and args[-1] == "running"
        ):
            service = args[-4]
            stdout = service + "\n" if service in self.started_services else ""
            return CommandResult(args, 0, stdout, "")
        if args[:2] == ("docker", "start"):
            return CommandResult(args, 0, args[2] + "\n", "")
        if args[:2] == ("docker", "ps"):
            return CommandResult(
                args,
                0,
                "polymarket-rust-collector-gpu-probability-worker-1\n",
                "",
            )
        return CommandResult(args, 1, "", "unexpected command")


class FakeHttpClient:
    def get(self, url: str, *, timeout_seconds: float) -> HttpResult:
        if url.endswith("/health"):
            return HttpResult(200, {"status": "ok"}, "")
        if url.endswith("/"):
            return HttpResult(200, {}, "<title>Probability Runtime</title>")
        if url.endswith("/api/runtime/live?limit=8"):
            return HttpResult(
                200,
                {"ok": True, "monitor": {"orderbooks": [{"id": 1}]}},
                "",
            )
        if url.endswith("/api/runtime/probabilities?limit=8"):
            return HttpResult(
                200,
                {"ok": True, "state": "OK", "rows": [{"contract": "BTC 5m UP"}]},
                "",
            )
        raise AssertionError(url)


def ready_recovery_config(
    tmp_path: Path,
    *,
    required_healthy_cycles: int = 0,
) -> RuntimeKeeperConfig:
    return RuntimeKeeperConfig(
        repo=tmp_path / "repo",
        data_dir=tmp_path / "data",
        recovery_boot_id="boot-1",
        recovery_config=RecoveryConfig(
            warmup_min_seconds=0,
            required_healthy_cycles=required_healthy_cycles,
        ),
        recovery_startup_ts=BASE - timedelta(minutes=5),
    )


def read_recovery_status(config: RuntimeKeeperConfig) -> dict[str, Any]:
    recovery_status_path = config.recovery_status_path
    assert recovery_status_path is not None
    return cast(
        dict[str, Any],
        json.loads(recovery_status_path.read_text(encoding="utf-8")),
    )


def test_runtime_keeper_starts_services_optional_container_and_writes_report(tmp_path: Path) -> None:
    runner = StoppedServiceRunner()
    config = RuntimeKeeperConfig(repo=tmp_path / "repo", data_dir=tmp_path / "data")
    keeper = RuntimeKeeper(config=config, runner=runner, http_client=FakeHttpClient())

    payload = keeper.run_once()

    assert payload["ok"] is True
    assert "compose up collector" in payload["actions"]
    assert "docker start polymarket-rust-collector-gpu-probability-worker-1" in payload["actions"]
    assert (tmp_path / "data" / "live" / "runtime_keeper.json").is_file()
    assert any(call[:2] == ("docker", "info") for call in runner.calls)


def test_runtime_keeper_does_not_compose_up_already_running_services(tmp_path: Path) -> None:
    runner = FakeRunner()
    config = RuntimeKeeperConfig(repo=tmp_path / "repo", data_dir=tmp_path / "data")
    keeper = RuntimeKeeper(config=config, runner=runner, http_client=FakeHttpClient())

    payload = keeper.run_once()

    assert payload["ok"] is True
    assert not [call for call in runner.calls if call[-3:-1] == ("up", "-d")]
    assert all(not action.startswith("compose up ") for action in payload["actions"])


def test_runtime_keeper_writes_ready_recovery_status_for_healthy_run(tmp_path: Path) -> None:
    config = ready_recovery_config(tmp_path)
    keeper = RuntimeKeeper(config=config, runner=FakeRunner(), http_client=FakeHttpClient())

    keeper.run_once()

    payload = read_recovery_status(config)
    assert payload["schema_version"] == "polymarket-recovery-runtime-v1"
    assert payload["runtime_phase"] == "READY"
    assert payload["ready"] is True
    assert payload["reasons"] == []
    assert payload["boot_id"] == "boot-1"


def test_runtime_keeper_requires_two_consecutive_healthy_cycles(tmp_path: Path) -> None:
    config = ready_recovery_config(tmp_path, required_healthy_cycles=2)
    keeper = RuntimeKeeper(config=config, runner=FakeRunner(), http_client=FakeHttpClient())

    keeper.run_once()
    first = read_recovery_status(config)

    assert first["consecutive_healthy_cycles"] == 1
    assert first["runtime_phase"] == "WARMING"
    assert first["ready"] is False
    assert "insufficient_healthy_cycles" in first["reasons"]

    keeper.run_once()
    second = read_recovery_status(config)

    assert second["consecutive_healthy_cycles"] == 2
    assert second["runtime_phase"] == "READY"
    assert second["ready"] is True
    assert second["reasons"] == []


def test_runtime_keeper_failed_run_resets_consecutive_healthy_cycles(
    tmp_path: Path,
) -> None:
    class FailingLiveHttpClient(FakeHttpClient):
        def get(self, url: str, *, timeout_seconds: float) -> HttpResult:
            if url.endswith("/api/runtime/live?limit=8"):
                return HttpResult(200, {"ok": False, "monitor": {"orderbooks": []}}, "")
            return super().get(url, timeout_seconds=timeout_seconds)

    config = ready_recovery_config(tmp_path, required_healthy_cycles=2)
    RuntimeKeeper(config=config, runner=FakeRunner(), http_client=FakeHttpClient()).run_once()
    assert read_recovery_status(config)["consecutive_healthy_cycles"] == 1

    RuntimeKeeper(
        config=config,
        runner=FakeRunner(),
        http_client=FailingLiveHttpClient(),
    ).run_once()
    recovery = read_recovery_status(config)

    assert recovery["consecutive_healthy_cycles"] == 0
    assert recovery["ready"] is False
    assert "orderbook_stale" in recovery["reasons"]


def test_runtime_keeper_reports_docker_unavailable(tmp_path: Path) -> None:
    class BrokenDockerRunner(FakeRunner):
        def run(self, args: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:
            self.calls.append(args)
            if args[:2] == ("docker", "info"):
                return CommandResult(args, 1, "", "docker unavailable")
            return CommandResult(args, 1, "", "should not run")

    keeper = RuntimeKeeper(
        config=ready_recovery_config(tmp_path),
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
    recovery = read_recovery_status(keeper.config)
    assert recovery["ready"] is False
    assert recovery["runtime_phase"] == "DEGRADED"
    assert "status_unhealthy" in recovery["reasons"]
    assert "api_unhealthy" in recovery["reasons"]


def test_runtime_keeper_runner_exception_still_writes_status_files(
    tmp_path: Path,
) -> None:
    class TimeoutRunner(FakeRunner):
        def run(self, args: tuple[str, ...], *, timeout_seconds: float) -> CommandResult:
            raise subprocess.TimeoutExpired(args, timeout_seconds)

    config = ready_recovery_config(tmp_path)
    keeper = RuntimeKeeper(
        config=config,
        runner=TimeoutRunner(),
        http_client=FakeHttpClient(),
    )

    payload = keeper.run_once()

    assert payload["ok"] is False
    assert payload["checks"][0]["name"] == "docker:info"
    assert "TimeoutExpired" in payload["checks"][0]["detail"]
    assert config.report_path.is_file()
    recovery = read_recovery_status(config)
    assert recovery["ready"] is False
    assert "status_unhealthy" in recovery["reasons"]


def test_runtime_keeper_http_exception_still_writes_status_files(tmp_path: Path) -> None:
    class ExplodingHttpClient(FakeHttpClient):
        def get(self, url: str, *, timeout_seconds: float) -> HttpResult:
            if url.endswith("/api/runtime/probabilities?limit=8"):
                raise RuntimeError("probability client exploded")
            return super().get(url, timeout_seconds=timeout_seconds)

    config = ready_recovery_config(tmp_path)
    keeper = RuntimeKeeper(
        config=config,
        runner=FakeRunner(),
        http_client=ExplodingHttpClient(),
    )

    payload = keeper.run_once()

    assert payload["ok"] is False
    probability_check = next(
        check for check in payload["checks"] if check["name"] == "api:/api/runtime/probabilities"
    )
    assert "RuntimeError" in probability_check["detail"]
    assert config.report_path.is_file()
    recovery = read_recovery_status(config)
    assert recovery["ready"] is False
    assert "probability_inputs_stale" in recovery["reasons"]


@pytest.mark.parametrize(
    ("client", "expected_reason"),
    [
        ("live", "orderbook_stale"),
        ("probabilities", "probability_inputs_stale"),
    ],
)
def test_runtime_keeper_maps_runtime_failures_to_non_ready_recovery_status(
    tmp_path: Path,
    client: str,
    expected_reason: str,
) -> None:
    class FailingHttpClient(FakeHttpClient):
        def get(self, url: str, *, timeout_seconds: float) -> HttpResult:
            if client == "live" and url.endswith("/api/runtime/live?limit=8"):
                return HttpResult(200, {"ok": False, "monitor": {"orderbooks": []}}, "")
            if client == "probabilities" and url.endswith(
                "/api/runtime/probabilities?limit=8"
            ):
                return HttpResult(200, {"ok": True, "state": "STALE", "rows": []}, "")
            return super().get(url, timeout_seconds=timeout_seconds)

    config = ready_recovery_config(tmp_path)
    keeper = RuntimeKeeper(config=config, runner=FakeRunner(), http_client=FailingHttpClient())

    keeper.run_once()

    recovery = read_recovery_status(config)
    assert recovery["ready"] is False
    assert recovery["runtime_phase"] == "DEGRADED"
    assert expected_reason in recovery["reasons"]


def recovery_inputs_for_failed_detail(
    *,
    check_name: str,
    detail: str,
) -> Any:
    return recovery_inputs_from_checks(
        (
            KeeperCheck("api:/health", True, "ok"),
            KeeperCheck("api:/api/runtime/live", True, "live rows present"),
            KeeperCheck(check_name, False, detail),
        ),
        boot_id="boot-1",
        startup_ts=BASE - timedelta(minutes=5),
        now=BASE,
        previous_consecutive_healthy_cycles=1,
    )


def test_json_content_type_alone_does_not_set_decode_error_recent() -> None:
    inputs = recovery_inputs_for_failed_detail(
        check_name="api:/api/runtime/probabilities",
        detail="status=500 content_type=application/json body_prefix=server error",
    )

    assert inputs.recent_decode_error is False


@pytest.mark.parametrize("detail", ["decode error", "JSON parse failed"])
def test_explicit_decode_detail_sets_decode_error_recent(detail: str) -> None:
    inputs = recovery_inputs_for_failed_detail(
        check_name="api:/api/runtime/probabilities",
        detail=detail,
    )

    assert inputs.recent_decode_error is True


def test_generic_blocked_wording_does_not_set_api_blocked_recent() -> None:
    inputs = recovery_inputs_for_failed_detail(
        check_name="api:/api/runtime/probabilities",
        detail="state=BLOCKED body_prefix=probability offload blocked",
    )

    assert inputs.recent_api_blocked is False


@pytest.mark.parametrize(
    "detail",
    [
        "API_BLOCKED upstream throttle",
        "status=403 content_type=text/plain body_prefix=forbidden",
        "status=429 content_type=text/plain body_prefix=rate limited",
    ],
)
def test_explicit_api_blocked_details_set_api_blocked_recent(detail: str) -> None:
    inputs = recovery_inputs_for_failed_detail(
        check_name="api:/health",
        detail=detail,
    )

    assert inputs.recent_api_blocked is True
