import assert from "node:assert/strict";
import {
  probabilityDisplayValue,
  probabilityMetadata,
  probabilityRowKey,
  probabilitySelectionKey,
} from "../../ui/src/probabilityRows";

const upRow = {
  contract_id: "btc-up",
  output_id: "out-up",
  asset: "BTC",
  side: "UP",
  expiry_ts: "2026-06-05T13:25:00Z",
  asof_ts: "2026-06-05T13:20:00Z",
  p_finish: 0.55,
  p_hat: 0.56,
  path_count: 120000,
  paths_per_seed: 30000,
  seed_count: 4,
  simulation_preview: {
    path_count: 120000,
    sampled_paths: new Array(24).fill({ points: [1, 2, 3] }),
  },
};

const downRow = {
  ...upRow,
  contract_id: "btc-down",
  output_id: "out-down",
  side: "DOWN",
  p_finish: 0.44,
  p_hat: 0.45,
};

assert.equal(probabilityDisplayValue(upRow), 0.56);
assert.equal(probabilityDisplayValue({ ...upRow, p_hat: undefined }), 0.55);
assert.equal(probabilityDisplayValue({ p_hat: 0, p_finish: 0.55 }), 0);
assert.notEqual(probabilityRowKey(upRow), probabilityRowKey(downRow));
assert.deepEqual(probabilityMetadata(upRow), {
  totalPaths: 120000,
  pathsPerSeed: 30000,
  seedCount: 4,
  previewPathCount: 24,
});

assert.notEqual(
  probabilityRowKey({ ...upRow, output_id: undefined }),
  probabilityRowKey({ ...downRow, output_id: undefined }),
);

assert.notEqual(
  probabilityRowKey({
    ...upRow,
    output_id: undefined,
    contract_id: undefined,
    market_slug: "btc-10am",
    start_ts: "2026-06-05T13:00:00Z",
  }),
  probabilityRowKey({
    ...upRow,
    output_id: undefined,
    contract_id: undefined,
    market_slug: "btc-11am",
    start_ts: "2026-06-05T13:00:00Z",
  }),
);

assert.notEqual(
  probabilityRowKey({
    ...upRow,
    output_id: undefined,
    contract_id: undefined,
    market_slug: "btc-10am",
    start_ts: "2026-06-05T13:00:00Z",
  }),
  probabilityRowKey({
    ...upRow,
    output_id: undefined,
    contract_id: undefined,
    market_slug: "btc-10am",
    start_ts: "2026-06-05T14:00:00Z",
  }),
);

assert.equal(
  probabilitySelectionKey(upRow),
  probabilitySelectionKey({
    ...upRow,
    output_id: "out-up-refresh",
    asof_ts: "2026-06-05T13:21:00Z",
  }),
);
assert.notEqual(probabilitySelectionKey(upRow), probabilitySelectionKey(downRow));
