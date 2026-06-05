import assert from "node:assert/strict";
import { filterGraphableProbabilityRows } from "../../ui/src/probabilityRows";

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
