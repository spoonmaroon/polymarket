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
