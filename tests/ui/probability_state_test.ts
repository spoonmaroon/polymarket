import assert from "node:assert/strict";
import { toProbabilityApiState, toProbabilityEventApiState } from "../../ui/src/App";
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

const initialNowcast = toProbabilityApiState(
  {
    payload: {
      ok: true,
      state: "NOWCAST",
      generated_at: "2099-06-05T20:55:01Z",
      rows: [],
      last_good_rows: [
        {
          contract_id: "btc-up",
          asset: "BTC",
          side: "UP",
          expiry_ts: "2099-06-05T21:00:00Z",
          valid_until: "2099-06-05T20:55:30Z",
          p_hat: 0.58,
        },
      ],
    },
    error: null,
  },
  {
    status: "loading" as const,
    payload: null,
    error: null,
    notice: null,
    updatedAt: null,
  },
);

assert.equal(initialNowcast.payload?.rows?.length, 1);
assert.equal(filterGraphableProbabilityRows(initialNowcast.payload).length, 1);

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

const streamPrevious = {
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

const streamNext = toProbabilityEventApiState(
  [
    {
      event_id: "nowcast-only",
      contract_id: "btc-up",
      asset: "BTC",
      side: "UP",
      expiry_ts: "2099-06-05T21:00:00Z",
      valid_until: "2099-06-05T20:55:30Z",
      probability_kind: "NOWCAST",
      p_hat: 0.55,
      generated_at: "2099-06-05T20:55:02Z",
    },
  ],
  streamPrevious,
);

assert.equal(streamNext.payload?.rows?.length, 1);
assert.equal(filterGraphableProbabilityRows(streamNext.payload).length, 1);
assert.equal(streamNext.notice, "Monte Carlo refresh pending; keeping last populated grid.");

const streamMcNext = toProbabilityEventApiState(
  [
    {
      event_id: "mc-update",
      contract_id: "btc-up",
      asset: "BTC",
      side: "UP",
      expiry_ts: "2099-06-05T21:00:00Z",
      valid_until: "2099-06-05T20:55:30Z",
      probability_kind: "MC",
      p_hat: 0.72,
      generated_at: "2099-06-05T20:55:03Z",
    },
  ],
  previous,
);

assert.equal(streamMcNext.payload?.rows?.length, 1);
assert.equal(streamMcNext.payload?.rows?.[0]?.p_hat, 0.72);
