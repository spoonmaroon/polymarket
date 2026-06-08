import assert from "node:assert/strict";
import {
  generatorBreakdownRows,
  pairCoherenceLabel,
  probabilityDisplayValue,
  probabilityMetadata,
  probabilityPriorSummary,
  probabilityRowKey,
  probabilitySelectionKey,
  riskAdjustedDisplayValue,
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
  paths_per_generator: 30000,
  generator_count: 4,
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
assert.equal(riskAdjustedDisplayValue({ p_finish: 0.62, risk_adjusted_p_finish: 0.56 }), 0.56);
assert.equal(riskAdjustedDisplayValue({ p_finish: 0.62 }), undefined);
assert.equal(
  pairCoherenceLabel({ pair_probability_sum_before: 0.9, pair_normalized: true }),
  "normalized from 0.900 pair sum",
);
assert.equal(
  pairCoherenceLabel({ pair_probability_sum_before: 1.0, pair_normalized: false }),
  "pair sum 1.000",
);
assert.notEqual(probabilityRowKey(upRow), probabilityRowKey(downRow));
assert.deepEqual(probabilityMetadata(upRow), {
  totalPaths: 120000,
  pathsPerGenerator: 30000,
  generatorCount: 4,
  pathsPerSeed: 30000,
  seedCount: 4,
  previewPathCount: 24,
});
assert.equal(
  probabilityPriorSummary({
    prior_fragment_count: 12,
    prior_fragment_reason: "exact",
    prior_fragment_sparse: false,
  }),
  "12 fragments, exact",
);
assert.equal(
  probabilityPriorSummary({
    prior_fragment_count: 1,
    prior_fragment_reason: "coarse",
    prior_fragment_sparse: true,
  }),
  "1 fragment, coarse, sparse",
);

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

assert.deepEqual(
  generatorBreakdownRows({
    generator_summary: {
      empirical_conditional: {
        p_finish: 0.61,
        p_no_touch: 0.7,
        weight: 0.4,
        sparse: false,
      },
      stress_overlay: {
        p_finish: 0.52,
        p_no_touch: 0.58,
        weight: 0.1,
        sparse: true,
      },
    },
  }),
  [
    {
      id: "empirical_conditional",
      p_finish: 0.61,
      p_no_touch: 0.7,
      weight: 0.4,
      sparse: false,
    },
    {
      id: "stress_overlay",
      p_finish: 0.52,
      p_no_touch: 0.58,
      weight: 0.1,
      sparse: true,
    },
  ],
);

assert.deepEqual(
  generatorBreakdownRows({
    generator_metadata: {
      generator_summary: {
        block_bootstrap: {
          p_finish: 0.57,
          p_no_touch: 0.63,
          sparse: true,
        },
      },
      effective_weights: {
        block_bootstrap: 0.25,
      },
    },
  }),
  [
    {
      id: "block_bootstrap",
      p_finish: 0.57,
      p_no_touch: 0.63,
      weight: 0.25,
      sparse: true,
    },
  ],
);

assert.deepEqual(
  generatorBreakdownRows({
    effective_weights: {
      empirical_conditional: 0.4,
      block_bootstrap: 0.25,
    },
  }),
  [
    {
      id: "empirical_conditional",
      p_finish: undefined,
      p_no_touch: undefined,
      weight: 0.4,
      sparse: undefined,
    },
    {
      id: "block_bootstrap",
      p_finish: undefined,
      p_no_touch: undefined,
      weight: 0.25,
      sparse: undefined,
    },
  ],
);

assert.deepEqual(
  generatorBreakdownRows({
    generator_metadata: {
      effective_weights: {
        filtered_historical: 0.25,
      },
    },
  }),
  [
    {
      id: "filtered_historical",
      p_finish: undefined,
      p_no_touch: undefined,
      weight: 0.25,
      sparse: undefined,
    },
  ],
);

assert.deepEqual(
  generatorBreakdownRows({
    path_count: 80_000,
    generator_summary: {
      empirical_conditional: { p_finish: 0.61, p_no_touch: 0.55, weight: 0.4, sparse: false },
      block_bootstrap: { p_finish: 0.58, p_no_touch: 0.52, weight: 0.25, sparse: false },
      filtered_historical: { p_finish: 0.63, p_no_touch: 0.57, weight: 0.25, sparse: false },
      stress_overlay: { p_finish: 0.53, p_no_touch: 0.49, weight: 0.1, sparse: false },
    },
  }).map((row) => row.id),
  ["empirical_conditional", "block_bootstrap", "filtered_historical", "stress_overlay"],
);

assert.deepEqual(generatorBreakdownRows({ generator_summary: undefined }), []);
