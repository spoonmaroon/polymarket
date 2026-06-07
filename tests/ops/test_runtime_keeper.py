from __future__ import annotations

from pathlib import Path

from polymarket_engine.ops.runtime_keeper import (
    CommandResult,
    HttpResult,
    RuntimeKeeper,
    RuntimeKeeperConfig,
    compose_command,
    evaluate_http_checks,
    evaluate_optional_container,
    evaluate_required_service,
)


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
