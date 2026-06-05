from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_deploy_defaults_disable_cpu_runtime_probabilities() -> None:
    compose = (ROOT / "deploy/collector/docker-compose.yml").read_text(encoding="utf-8")
    entrypoint = (ROOT / "deploy/normalizer/normalizer-entrypoint.sh").read_text(
        encoding="utf-8"
    )
    env_example = (ROOT / "deploy/collector/.env.example").read_text(encoding="utf-8")

    assert "POLYMARKET_NORMALIZER_ENABLE_PROBABILITIES:-0" in compose
    assert "POLYMARKET_ENABLE_RUNTIME_PROBABILITIES:-0" in compose
    assert 'ENABLE_PROBABILITIES="${POLYMARKET_NORMALIZER_ENABLE_PROBABILITIES:-0}"' in entrypoint
    assert "POLYMARKET_NORMALIZER_ENABLE_PROBABILITIES=0" in env_example
    assert "POLYMARKET_ENABLE_RUNTIME_PROBABILITIES=0" in env_example


def test_pc_deploy_forces_existing_env_to_gpu_runtime_probabilities() -> None:
    script = (ROOT / "scripts/deploy_pc.sh").read_text(encoding="utf-8")

    assert 'set_env POLYMARKET_NORMALIZER_ENABLE_PROBABILITIES "0" deploy/collector/.env' in script
    assert 'set_env POLYMARKET_ENABLE_RUNTIME_PROBABILITIES "0" deploy/collector/.env' in script
    assert 'export POLYMARKET_NORMALIZER_ENABLE_PROBABILITIES="0"' in script
    assert 'export POLYMARKET_ENABLE_RUNTIME_PROBABILITIES="0"' in script


def test_pc_deploy_probability_smoke_requires_fresh_cuda_rows() -> None:
    script = (ROOT / "scripts/deploy_pc.sh").read_text(encoding="utf-8")

    assert "deploy_started_at = time.time()" in script
    assert "cuda_generator_versions" in script
    assert '"cuda-lognormal-chainlink-sigma-batch-v1"' in script
    assert '"cuda-lognormal-chainlink-sigma-multiseed-v1"' in script
    assert 'row.get("generator_version") not in cuda_generator_versions' in script
    assert 'int(row.get("path_count") or 0) < 10_000' in script
    assert "required_contracts" in script
    assert '("BTC", "UP")' in script
    assert '("BTC", "DOWN")' in script
    assert '("ETH", "UP")' in script
    assert '("ETH", "DOWN")' in script
    assert "generated_at.timestamp() < deploy_started_at" in script


def test_pc_deploy_copies_artifacts_atomically() -> None:
    script = (ROOT / "scripts/deploy_pc.sh").read_text(encoding="utf-8")

    assert 'dest_tmp="$dest.tmp.$$"' in script
    assert "cat > $dest_tmp_q && mv -f $dest_tmp_q $dest_q" in script


def test_pc_deploy_retries_collector_status_after_restart() -> None:
    script = (ROOT / "scripts/deploy_pc.sh").read_text(encoding="utf-8")

    assert "collector_status_ok=0" in script
    assert "for attempt in \\$(seq 1 45); do" in script
    assert 'echo "collector status did not become ready after deploy"' in script


def test_pc_deploy_docs_list_cuda_probability_artifact_for_skip_builds() -> None:
    docs = (ROOT / "docs/SPOON_DEPLOYMENT.md").read_text(encoding="utf-8")

    assert "polymarket-cuda-probability-<sha>.tar" in docs


def test_deploy_compose_adds_nvidia_gpu_probability_worker() -> None:
    compose = (ROOT / "deploy/collector/docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "deploy/gpu/Dockerfile").read_text(encoding="utf-8")
    entrypoint = (ROOT / "deploy/gpu/gpu-probability-entrypoint.sh").read_text(encoding="utf-8")

    assert "gpu-probability-worker:" in compose
    assert "dockerfile: deploy/gpu/Dockerfile" in compose
    assert "nvidia/cuda:13.2.1-cudnn-devel-ubuntu24.04" in dockerfile
    assert "run-cuda-probability-worker" in entrypoint
    assert "driver: nvidia" in compose
    assert "capabilities: [gpu]" in compose
    assert "POLYMARKET_CUDA_PROBABILITY_IMAGE" in compose
