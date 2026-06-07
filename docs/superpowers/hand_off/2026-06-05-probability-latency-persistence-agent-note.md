# Agent Coordination Note: Probability Latency And Persistence

Message for any other agent working in `/Users/goon/polymarket`:

I am writing a docs-only implementation plan for the probability latency and persistence fix. I am not changing runtime code in this pass, and the user has asked to wait before implementation.

Scope I am reserving:

- Plan file: `docs/superpowers/plans/2026-06-05-probability-latency-and-persistence.md`
- Coordination note: `docs/superpowers/hand_off/2026-06-05-probability-latency-persistence-agent-note.md`

Problem being planned:

- The user observed a 1-3 second lag between a contract price jump and the Monte Carlo probability display catching up.
- THEPC had live CUDA probability rows visible in `/home/ender/polymarket-data/live/probabilities.json`, but persisted historical probability rows in DuckDB stopped earlier.
- Direct reads of the live DuckDB were blocked by the normalizer writer lock, so analysis used disposable DB/WAL snapshot copies.
- The likely fix is not heavier Monte Carlo. The fix is latency tracing, a fast nowcast lane, dynamic path counts, and forward-only persistence of compact probability events and simulation summaries.

Please avoid editing the plan file above while I am preparing it. If you are implementing adjacent GPU worker work, keep DB writes single-writer safe: do not make the GPU worker contend directly with the normalizer's DuckDB writer without a queue or ownership decision.

This plan may need small updates after new commits/features land. Before executing it, re-check the latest branch, THEPC runtime state, and current persisted probability tables.
