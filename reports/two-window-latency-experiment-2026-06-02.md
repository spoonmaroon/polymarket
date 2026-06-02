# Two-Window Latency Experiment

Date: 2026-06-02

Purpose: compare the deployed 12-token state-manager setup against a reduced
current+next-only setup for BTC/ETH 5m contracts.

## Experiment Setup

Baseline:

```text
POLYMARKET_PREWARM_WINDOWS=3
current + next + next-next
12 CLOB token subscriptions
```

Experiment:

```text
POLYMARKET_PREWARM_WINDOWS=2
current + next
8 CLOB token subscriptions
```

Both samples ran for 60 seconds and used the same metrics:

1. current-window book age
2. Chainlink age
3. hot `DecisionState` build time
4. dropped events
5. current-window orderbook event-to-observed lag

The sampler also recorded all-window book age and all-window event-to-observed
lag to show how future quiet books affect headline latency.

## Deployment Change

Spoon was updated to run the two-window experiment:

| Item | Value |
|---|---|
| Deployed commit | `9217157 Allow two-window state-manager health checks` |
| Env change | `POLYMARKET_PREWARM_WINDOWS=2` in `/home/spoon/polymarket/deploy/collector/.env` |
| State-manager verifier | `current=2 next=2 next_next=0 orderbooks=8 subscriptions=8 health_flags=0` |
| Collector health | passing with `--expected-prewarm-windows 2` |

The verifier and Docker healthcheck now accept an explicit expected prewarm
window count. The default remains strict for 3 windows.

## Results

| Metric | 12-token baseline p50 | 12-token baseline p95 | 12-token max | 8-token p50 | 8-token p95 | 8-token max |
|---|---:|---:|---:|---:|---:|---:|
| Current-window book age ms | 5.765 | 728.603 | 1553.433 | 172.161 | 1349.700 | 2576.898 |
| Chainlink age ms | 512.026 | 1087.474 | 1209.531 | 534.546 | 1548.944 | 2373.434 |
| Hot `DecisionState` build us | 788.000 | 1601.000 | 1742.000 | 35.000 | 176.000 | 211.000 |
| Dropped events | 0 | 0 | 0 | 0 | 0 | 0 |
| Current-window orderbook event-to-observed ms | 74.793 | 158.427 | 232.287 | 69.431 | 92.381 | 190.750 |
| All-window book age ms | 5.765 | 8549.883 | 13960.754 | 1048.035 | 12598.794 | 13721.665 |
| All-window orderbook event-to-observed ms | 221.020 | 29301.168 | 40907.793 | 83.532 | 9284.381 | 9284.381 |

## Interpretation

The reduced 8-token setup did improve the measurements that are most tied to
the hot state path:

- current-window orderbook event-to-observed p95 improved from about 158 ms to
  about 92 ms;
- hot `DecisionState` build p95 improved from about 1601 us to about 176 us;
- dropped events stayed at zero;
- all-window event-to-observed lag improved sharply because `next_next` quiet
  books were removed from the headline set.

The reduced setup did not improve every freshness number:

- current-window book age p95 worsened from about 729 ms to about 1350 ms;
- Chainlink age p95 worsened from about 1087 ms to about 1549 ms.

Those two worsened metrics are not enough to reject the two-window setup. They
are driven by upstream event cadence and sample timing, not just local
subscription count. The important result is that the hot build path became
cleaner and no drops appeared.

## Decision

Keep Spoon on current+next only for a longer trial.

The current two-window setup is the better experimental posture because it
removes future-window noise from the hot set while preserving rollover safety.
The system should not use all-window max latency as a trading readiness signal.
It should separate:

```text
current-window readiness
future-window prewarm readiness
all-window operator diagnostics
```

## Next Fix

Update the monitor/status report to display:

1. current-window book age,
2. next-window book age,
3. next-next-window book age when enabled,
4. current-window event-to-observed lag,
5. all-window headline diagnostics as a separate non-decision metric.

This will stop quiet future books from making the live decision path look
slower than it is.

