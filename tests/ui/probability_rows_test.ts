import assert from "node:assert/strict";
import {
  filterGraphableProbabilityRows,
  mergeProbabilityEventsIntoPayload,
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
