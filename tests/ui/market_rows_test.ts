import assert from "node:assert/strict";
import {
  marketGroupKey,
  marketSideKey,
  visibleMarketMonitorRows,
} from "../../ui/src/marketRows";

assert.equal(
  marketGroupKey(
    "btc-updown-5m-1780723800",
    "BTC",
    "2026-06-06T05:35:00+00:00",
    "2026-06-06T05:30:00+00:00",
  ),
  marketGroupKey(
    "btc-updown-5m-1780723800",
    "BTC",
    "2026-06-06T05:35:00Z",
    "2026-06-06T05:30:00Z",
  ),
);

assert.equal(
  marketSideKey(
    "eth-updown-5m-1780723800",
    "ETH",
    "2026-06-06T05:35:00+00:00",
    "UP",
    "2026-06-06T05:30:00+00:00",
  ),
  marketSideKey(
    "eth-updown-5m-1780723800",
    "ETH",
    "2026-06-06T05:35:00Z",
    "UP",
    "2026-06-06T05:30:00Z",
  ),
);

const marketRows = [
  { key: "btc-current", upProbability: { p_finish: 0.57 } },
  { key: "btc-current-orderbook-only" },
  { key: "btc-next-orderbook-only" },
  { key: "eth-current", downProbability: { p_finish: 0.44 } },
];

assert.deepEqual(
  visibleMarketMonitorRows(marketRows, 4).map((row) => row.key),
  ["btc-current", "eth-current"],
);

assert.deepEqual(
  visibleMarketMonitorRows(marketRows, 0).map((row) => row.key),
  ["btc-current", "btc-current-orderbook-only", "btc-next-orderbook-only", "eth-current"],
);
