import assert from "node:assert/strict";
import {
  filterGraphableProbabilityRows,
  mergeProbabilityEventsIntoPayload,
  probabilityRuntimeStateLabel,
  probabilityRowsWithRolloverHold,
  visibleProbabilityDiagnosticRows,
} from "../../ui/src/probabilityRows";

const nowMs = Date.parse("2026-06-05T13:20:00Z");

const fresh = {
  contract_id: "btc-up",
  expiry_ts: "2026-06-05T13:25:00Z",
  valid_until: "2026-06-05T13:20:30Z",
};
const expired = {
  contract_id: "btc-down",
  expiry_ts: "2026-06-05T13:19:59Z",
  valid_until: "2026-06-05T13:20:30Z",
};
const stale = {
  contract_id: "eth-up",
  expiry_ts: "2026-06-05T13:25:00Z",
  valid_until: "2026-06-05T13:19:59Z",
};

assert.deepEqual(
  filterGraphableProbabilityRows(
    {
      ok: false,
      state: "PARTIAL",
      rows: [fresh, expired, stale],
      errors: ["eth-down failed"],
    },
    nowMs,
  ).map((row) => row.contract_id),
  ["btc-up"],
);

assert.deepEqual(
  filterGraphableProbabilityRows(
    {
      ok: false,
      state: "PARTIAL",
      rows: [],
      last_good_rows: [fresh],
      errors: ["all cuda paths failed"],
    },
    nowMs,
  ),
  [],
);

assert.deepEqual(
  filterGraphableProbabilityRows(
    {
      ok: true,
      state: "OK",
      rows: [fresh, expired, stale],
    },
    nowMs,
  ).map((row) => row.contract_id),
  ["btc-up"],
);

assert.deepEqual(
  filterGraphableProbabilityRows(
    {
      ok: true,
      state: "STALE_INPUTS",
      previous_mc_retained: true,
      retained_mc_rows: 1,
      rows: [stale, expired],
    },
    nowMs,
  ).map((row) => row.contract_id),
  ["eth-up"],
);

assert.deepEqual(
  filterGraphableProbabilityRows(
    {
      ok: true,
      state: "OK",
      previous_mc_retained: true,
      retained_mc_rows: 1,
      rows: [stale, expired],
    },
    nowMs,
  ).map((row) => row.contract_id),
  ["eth-up"],
);

const preview = {
  sampled_paths: [{ index: 0, terminal_win: true, no_touch_win: true, points: [1, 2] }],
};
const merged = mergeProbabilityEventsIntoPayload(
  {
    ok: true,
    state: "OK",
    generated_at: "2026-06-05T13:20:00Z",
    rows: [
      {
        contract_id: "btc-up",
        asset: "BTC",
        side: "UP",
        start_ts: "2026-06-05T13:20:00Z",
        expiry_ts: "2026-06-05T13:25:00Z",
        valid_until: "2026-06-05T13:25:00Z",
        probability_kind: "MC",
        p_finish: 0.51,
        simulation_preview: preview,
      },
    ],
    nowcast_rows: [
      {
        contract_id: "btc-up",
        asset: "BTC",
        side: "UP",
        start_ts: "2026-06-05T13:20:00Z",
        expiry_ts: "2026-06-05T13:25:00Z",
        valid_until: "2026-06-05T13:25:00Z",
        probability_kind: "NOWCAST",
        p_finish: 0.52,
      },
    ],
  },
  [
    {
      event_id: "mc-2",
      contract_id: "btc-up",
      asset: "BTC",
      side: "UP",
      start_ts: "2026-06-05T13:20:00Z",
      expiry_ts: "2026-06-05T13:25:00Z",
      valid_until: "2026-06-05T13:25:00Z",
      probability_kind: "MC",
      p_finish: 0.61,
      path_count: 30000,
      generated_at: "2026-06-05T13:20:01Z",
    },
    {
      event_id: "nowcast-2",
      contract_id: "btc-up",
      asset: "BTC",
      side: "UP",
      start_ts: "2026-06-05T13:20:00Z",
      expiry_ts: "2026-06-05T13:25:00Z",
      valid_until: "2026-06-05T13:25:00Z",
      probability_kind: "NOWCAST",
      p_finish: 0.72,
      generated_at: "2026-06-05T13:20:02Z",
    },
  ],
);

assert.equal(merged.rows?.length, 1);
assert.equal(merged.rows?.[0]?.p_finish, 0.61);
assert.equal(merged.rows?.[0]?.path_count, 30000);
assert.deepEqual(merged.rows?.[0]?.simulation_preview, preview);
assert.equal(merged.nowcast_rows?.length, 1);
assert.equal(merged.nowcast_rows?.[0]?.p_finish, 0.72);
assert.equal(merged.generated_at, "2026-06-05T13:20:02Z");

const currentBtcUp = {
  contract_id: "btc-current-up",
  asset: "BTC",
  side: "UP",
  start_ts: "2026-06-05T13:15:00Z",
  expiry_ts: "2026-06-05T13:25:00Z",
};
const currentBtcDown = {
  contract_id: "btc-current-down",
  asset: "BTC",
  side: "DOWN",
  start_ts: "2026-06-05T13:15:00Z",
  expiry_ts: "2026-06-05T13:25:00Z",
};
const currentBtcUpThinDuplicate = {
  contract_id: "btc-current-up",
  asset: "BTC",
  side: "UP",
  start_ts: "2026-06-05T13:15:00Z",
  expiry_ts: "2026-06-05T13:25:00Z",
  decision_hint: "PENDING",
  path_count: 120000,
};
const currentBtcUpCompleteDuplicate = {
  contract: "BTC 5m UP",
  contract_id: "btc-current-up",
  asset: "BTC",
  side: "UP",
  start_ts: "2026-06-05T13:15:00Z",
  expiry_ts: "2026-06-05T13:25:00Z",
  age_ms: 384,
  decision_hint: "BLOCK",
  path_count: 250000,
  simulation_preview: preview,
};
const nextBtcUp = {
  contract_id: "btc-next-up",
  asset: "BTC",
  side: "UP",
  start_ts: "2026-06-05T13:25:00Z",
  expiry_ts: "2026-06-05T13:30:00Z",
};
const currentEthUp = {
  contract_id: "eth-current-up",
  asset: "ETH",
  side: "UP",
  start_ts: "2026-06-05T13:15:00Z",
  expiry_ts: "2026-06-05T13:25:00Z",
};
const currentEthDown = {
  contract_id: "eth-current-down",
  asset: "ETH",
  side: "DOWN",
  start_ts: "2026-06-05T13:15:00Z",
  expiry_ts: "2026-06-05T13:25:00Z",
};
const nextEthDown = {
  contract_id: "eth-next-down",
  asset: "ETH",
  side: "DOWN",
  start_ts: "2026-06-05T13:25:00Z",
  expiry_ts: "2026-06-05T13:30:00Z",
};
const currentBtc15mUp = {
  contract_id: "btc-current-15m-up",
  market_slug: "btc-updown-15m-1780722000",
  asset: "BTC",
  side: "UP",
  start_ts: "2026-06-05T13:10:00Z",
  expiry_ts: "2026-06-05T13:25:00Z",
};
const currentBtc15mDown = {
  contract_id: "btc-current-15m-down",
  market_slug: "btc-updown-15m-1780722000",
  asset: "BTC",
  side: "DOWN",
  start_ts: "2026-06-05T13:10:00Z",
  expiry_ts: "2026-06-05T13:25:00Z",
};
const currentBtc5mNoStartUp = {
  contract_id: "btc-current-5m-no-start-up",
  market_slug: "btc-updown-5m-1780722300",
  asset: "BTC",
  side: "UP",
  expiry_ts: "2026-06-05T13:25:00Z",
};
const currentBtc5mNoStartDown = {
  contract_id: "btc-current-5m-no-start-down",
  market_slug: "btc-updown-5m-1780722300",
  asset: "BTC",
  side: "DOWN",
  expiry_ts: "2026-06-05T13:25:00Z",
};
const currentBtc15mNoStartUp = {
  contract_id: "btc-current-15m-no-start-up",
  market_slug: "btc-updown-15m-1780722000",
  asset: "BTC",
  side: "UP",
  expiry_ts: "2026-06-05T13:25:00Z",
};
const currentBtc15mNoStartDown = {
  contract_id: "btc-current-15m-no-start-down",
  market_slug: "btc-updown-15m-1780722000",
  asset: "BTC",
  side: "DOWN",
  expiry_ts: "2026-06-05T13:25:00Z",
};

assert.deepEqual(
  visibleProbabilityDiagnosticRows(
    [nextBtcUp, currentBtcDown, currentEthUp, nextEthDown, currentBtcUp, currentEthDown],
    nowMs,
  ).map((row) => row.contract_id),
  ["btc-current-up", "btc-current-down", "eth-current-up", "eth-current-down"],
);

assert.deepEqual(
  visibleProbabilityDiagnosticRows([nextBtcUp, nextEthDown], nowMs).map(
    (row) => row.contract_id,
  ),
  ["btc-next-up", "eth-next-down"],
);

assert.deepEqual(
  visibleProbabilityDiagnosticRows(
    [currentBtc15mUp, currentBtcUp, currentBtc15mDown, currentBtcDown],
    nowMs,
  ).map((row) => row.contract_id),
  ["btc-current-up", "btc-current-down"],
);

const dedupedCurrentRows = visibleProbabilityDiagnosticRows(
  [currentBtcUpThinDuplicate, currentBtcDown, currentBtcUpCompleteDuplicate],
  nowMs,
);
assert.deepEqual(
  dedupedCurrentRows.map((row) => row.contract_id),
  ["btc-current-up", "btc-current-down"],
);
assert.equal(dedupedCurrentRows[0]?.decision_hint, "BLOCK");
assert.equal(dedupedCurrentRows[0]?.path_count, 250000);

assert.deepEqual(
  visibleProbabilityDiagnosticRows(
    [
      currentBtc15mNoStartUp,
      currentBtc5mNoStartUp,
      currentBtc15mNoStartDown,
      currentBtc5mNoStartDown,
    ],
    nowMs,
  ).map((row) => row.contract_id),
  ["btc-current-5m-no-start-up", "btc-current-5m-no-start-down"],
);

assert.deepEqual(
  probabilityRowsWithRolloverHold([], [currentBtcUp, currentBtcDown], {
    nowMs: nowMs + 18_000,
    heldAtMs: nowMs,
    holdMs: 30_000,
  }).map((row) => row.contract_id),
  ["btc-current-up", "btc-current-down"],
);

assert.deepEqual(
  probabilityRowsWithRolloverHold([], [currentBtcUp], {
    nowMs: nowMs + 45_000,
    heldAtMs: nowMs,
    holdMs: 30_000,
  }),
  [],
);

assert.equal(probabilityRuntimeStateLabel("NOWCAST"), "Fast estimate updating");
assert.equal(probabilityRuntimeStateLabel("OK"), "Monte Carlo ready");
assert.equal(probabilityRuntimeStateLabel("STALE_INPUTS"), "Holding last Monte Carlo");
assert.equal(probabilityRuntimeStateLabel("PARTIAL"), "Partial Monte Carlo");
