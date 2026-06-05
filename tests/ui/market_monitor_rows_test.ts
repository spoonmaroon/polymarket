import assert from "node:assert/strict";
import { buildMarketMonitorRows } from "../../ui/src/App";

const startZ = "2026-06-05T20:20:00Z";
const expiryZ = "2026-06-05T20:25:00Z";
const startOffset = "2026-06-05T20:20:00+00:00";
const expiryOffset = "2026-06-05T20:25:00+00:00";

const rows = buildMarketMonitorRows(
  [
    {
      asset: "BTC",
      market_slug: "btc-updown-5m-1780690800",
      start_ts: startZ,
      expiry_ts: expiryZ,
      side: "UP",
      threshold_price: 60817,
      best_bid: 0.25,
      best_ask: 0.26,
    },
    {
      asset: "BTC",
      market_slug: "btc-updown-5m-1780690800",
      start_ts: startZ,
      expiry_ts: expiryZ,
      side: "DOWN",
      threshold_price: 60817,
      best_bid: 0.74,
      best_ask: 0.75,
    },
  ],
  [
    {
      asset: "BTC",
      market_slug: "btc-updown-5m-1780690800",
      start_ts: startOffset,
      expiry_ts: expiryOffset,
      side: "UP",
      p_finish: 0.111,
    },
    {
      asset: "BTC",
      market_slug: "btc-updown-5m-1780690800",
      start_ts: startOffset,
      expiry_ts: expiryOffset,
      side: "DOWN",
      p_finish: 0.886,
    },
  ],
  [
    {
      asset: "BTC",
      sigma_tau: 0.0012,
    },
  ],
  Date.parse("2026-06-05T20:22:00Z"),
);

assert.equal(rows.length, 1);
assert.equal(rows[0].asset, "BTC");
assert.equal(rows[0].threshold, 60817);
assert.equal(rows[0].up?.best_bid, 0.25);
assert.equal(rows[0].down?.best_ask, 0.75);
assert.equal(rows[0].upProbability?.p_finish, 0.111);
assert.equal(rows[0].downProbability?.p_finish, 0.886);
