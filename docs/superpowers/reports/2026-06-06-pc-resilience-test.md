# THEPC Power And Wi-Fi Loss Resilience Test

Date: 2026-06-06
Host: THEPC
SSH: ender@100.72.104.49
Repo: /home/ender/polymarket
Data: /home/ender/polymarket-data

## Baseline

### Deployed Commit

```text
468cf2a055c58a429d66b5439d28c4c07c57506b
```

### Docker State

```text
NAME                                                 IMAGE                                    COMMAND                  SERVICE                  CREATED          STATUS                        PORTS
polymarket-rust-collector-api-1                      polymarket-normalizer:468cf2a055c5       "/usr/bin/tini -- uv…"   api                      29 minutes ago   Up 29 minutes (healthy)       0.0.0.0:8000->8000/tcp, [::]:8000->8000/tcp
polymarket-rust-collector-collector-1                polymarket-rust-collector:468cf2a055c5   "/usr/bin/tini -- /u…"   collector                29 minutes ago   Up 29 minutes (healthy)
polymarket-rust-collector-gpu-probability-worker-1   polymarket-cuda-probability:9c9113d44    "/usr/bin/tini -- /u…"   gpu-probability-worker   4 hours ago      Up 4 hours
polymarket-rust-collector-normalizer-1               polymarket-normalizer:468cf2a055c5       "/usr/bin/tini -- /u…"   normalizer               29 minutes ago   Up About a minute (healthy)
polymarket-rust-collector-outcome-refresh-1          polymarket-normalizer:468cf2a055c5       "/usr/bin/tini -- po…"   outcome-refresh          29 minutes ago   Up 29 minutes (unhealthy)
```

### Runtime API Summary

```text
{'ok': True, 'orderbooks': 8, 'prices': 2, 'status_age_ms': 119, 'health_flags': []}
```

### Collector Validator

```text
{'ok': True, 'status_age_seconds': 0.095, 'price_age_ms': 107, 'orderbook_age_ms': 97}
```

### Outcome Refresh Baseline

```text
{'exists': True, 'schema_version': 'polymarket-outcome-runtime-v1', 'mtime_age_seconds': 1741.955}
```

Baseline decision: runtime-only recovery test can proceed. Full-stack recovery is already failing because `outcome-refresh` is unhealthy before any failure injection.

## Controlled Container Restart

Action:

```text
docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml restart collector normalizer api
restart_started_at=2026-06-06T03:39:27-05:00
```

Recovery API summary:

```text
{'ok': True, 'orderbooks': 8, 'prices': 2, 'status_age_ms': 351}
```

Collector validator after restart:

```text
{'ok': True, 'status_age_seconds': 0.257, 'price_age_ms': 386, 'orderbook_age_ms': 257}
```

Result: PASS for runtime-only recovery. Docker restored collector, normalizer, and API without manual repair. Full-stack status remains blocked by the pre-existing `outcome-refresh` unhealthy state.

## Current Pre-Reboot Checkpoint

Captured after restoring the probability API env guard.

```text
commit=468cf2a055c58a429d66b5439d28c4c07c57506b

runtime_summary:
{'ok': True, 'orderbooks': 8, 'prices': 2, 'status_age_ms': 3629, 'health_flags': [], 'prob_state': 'OK', 'prob_source': 'hot_inputs', 'prob_rows': 4}

validator:
{'ok': True, 'status_age_seconds': 3.672, 'price_age_ms': 4568, 'orderbook_age_ms': 3675}
```

Runtime-only status remains PASS. Full-stack status remains blocked by the pre-existing `outcome-refresh` unhealthy state.

## Controlled Windows Reboot

Status: BLOCKED pending explicit approval to reboot THEPC. This step will intentionally drop SSH, Docker, WSL, and the TUI.

## Wi-Fi Loss Test

Status: BLOCKED pending explicit confirmation that Enoch is physically at THEPC or has a non-Wi-Fi recovery path. The plan forbids intentionally disabling Wi-Fi while SSH is the only control path.

## Hard Power-Loss Test

Status: BLOCKED for local/manual execution only. The plan forbids doing a hard power cut from SSH.
