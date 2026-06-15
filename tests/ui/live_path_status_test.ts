import assert from "node:assert/strict";
import { livePathStatus } from "../../ui/src/probabilityRows";

assert.equal(
  livePathStatus({
    rows: [
      {
        contract_id: "eth-up",
        probability_kind: "MC",
        simulation_preview: {
          sampled_paths: [
            { index: 0, terminal_win: true, no_touch_win: true, points: [1, 2] },
          ],
        },
      },
    ],
    offload: {
      offload_allowed: true,
      input_count: 1,
      mc_eligible_input_count: 1,
      blocked_input_count: 0,
    },
  }).state,
  "LIVE_PATHS",
);

assert.equal(
  livePathStatus({
    rows: [
      {
        contract_id: "eth-up",
        probability_kind: "MC",
        simulation_preview: {
          sampled_paths: [
            { index: 0, terminal_win: true, no_touch_win: true, points: [1, 2] },
          ],
        },
      },
      { contract_id: "btc-up", probability_kind: "NOWCAST" },
    ],
    offload: {
      offload_allowed: true,
      input_count: 2,
      mc_eligible_input_count: 1,
      blocked_input_count: 1,
    },
  }).state,
  "PARTIAL_PATHS",
);

assert.equal(
  livePathStatus({
    rows: [
      {
        contract_id: "eth-up",
        probability_kind: "MC",
        simulation_preview: {
          sampled_paths: [
            { index: 0, terminal_win: true, no_touch_win: true, points: [1, 2] },
          ],
        },
      },
      { contract_id: "eth-down", probability_kind: "MC" },
    ],
    offload: {
      offload_allowed: true,
      input_count: 2,
      mc_eligible_input_count: 2,
      blocked_input_count: 0,
    },
  }).state,
  "PARTIAL_PATHS",
);

assert.deepEqual(
  livePathStatus({
    probabilityState: "OFFLOAD_BLOCKED",
    rows: [{ contract_id: "btc-up", probability_kind: "NOWCAST" }],
    offload: { offload_allowed: false, reason_codes: ["probability_inputs_stale"] },
  }),
  {
    state: "PATHS_BLOCKED",
    label: "Live paths blocked",
    detail: "probability_inputs_stale",
    reasons: ["probability_inputs_stale"],
  },
);

assert.deepEqual(
  livePathStatus({
    probabilityState: "OFFLOAD_BLOCKED",
    rows: [
      { contract_id: "btc-up", probability_kind: "MC" },
      { contract_id: "btc-down", probability_kind: "MC" },
    ],
    offload: { offload_allowed: false, reason_codes: ["probability_inputs_stale"] },
  }),
  {
    state: "PARTIAL_PATHS",
    label: "Partial paths",
    detail: "2 MC rows, preview pending",
    reasons: ["probability_inputs_stale"],
  },
);

assert.deepEqual(
  livePathStatus({
    probabilityState: "OFFLOAD_BLOCKED",
    rows: [{ contract_id: "btc-up", probability_kind: "NOWCAST" }],
    offload: {
      offload_allowed: true,
      reason_codes: ["runtime_not_ready", "sigma_invalid"],
      mc_eligible_input_count: 0,
      blocked_input_count: 4,
    },
  }),
  {
    state: "NOWCAST_ONLY",
    label: "Nowcast only",
    detail: "runtime_not_ready, sigma_invalid",
    reasons: ["runtime_not_ready", "sigma_invalid"],
  },
);

assert.equal(
  livePathStatus({
    rows: [{ contract_id: "btc-up", probability_kind: "NOWCAST" }],
    offload: { offload_allowed: true, mc_eligible_input_count: 0, blocked_input_count: 1 },
  }).state,
  "PATHS_BLOCKED",
);

assert.equal(
  livePathStatus({
    rows: [{ contract_id: "btc-up", probability_kind: "NOWCAST" }],
    offload: { offload_allowed: true, mc_eligible_input_count: 1, blocked_input_count: 0 },
  }).state,
  "NOWCAST_ONLY",
);

assert.equal(
  livePathStatus({
    rows: [],
    offload: { offload_allowed: true, mc_eligible_input_count: 1, blocked_input_count: 0 },
  }).state,
  "PATHS_PENDING",
);
