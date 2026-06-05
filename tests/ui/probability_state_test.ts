import assert from "node:assert/strict";
import { toProbabilityApiState } from "../../ui/src/App";
import { filterGraphableProbabilityRows } from "../../ui/src/probabilityRows";

const previous = {
  status: "ready" as const,
  payload: {
    ok: true,
    state: "OK",
    generated_at: "2099-06-05T20:54:59Z",
    rows: [
      {
        contract_id: "btc-up",
        asset: "BTC",
        side: "UP",
        expiry_ts: "2099-06-05T21:00:00Z",
        valid_until: "2099-06-05T20:55:30Z",
        p_hat: 0.61,
      },
    ],
  },
  error: null,
  notice: null,
  updatedAt: Date.parse("2099-06-05T20:54:59Z"),
};

const next = toProbabilityApiState(
  {
    payload: {
      ok: true,
      state: "NOWCAST",
      generated_at: "2099-06-05T20:55:01Z",
      rows: [],
    },
    error: null,
  },
  previous,
);

assert.equal(next.status, "ready");
assert.equal(next.error, null);
assert.equal(next.payload?.rows?.length, 1);
assert.equal(next.payload?.rows?.[0]?.contract_id, "btc-up");
assert.equal(next.payload?.state, "OK");
assert.equal(next.notice, "Monte Carlo refresh pending; keeping last populated grid.");

const rolloverPrevious = {
  ...previous,
  updatedAt: Date.now(),
  payload: {
    ...previous.payload,
    rows: [
      {
        ...previous.payload.rows[0],
        expiry_ts: "2000-01-01T00:00:00Z",
        valid_until: "2000-01-01T00:00:00Z",
      },
    ],
  },
};

const rolloverNext = toProbabilityApiState(
  {
    payload: {
      ok: true,
      state: "NOWCAST",
      generated_at: "2099-06-05T21:00:01Z",
      rows: [],
    },
    error: null,
  },
  rolloverPrevious,
);

assert.equal(rolloverNext.payload?.rows?.length, 1);
assert.equal(filterGraphableProbabilityRows(rolloverNext.payload).length, 1);
