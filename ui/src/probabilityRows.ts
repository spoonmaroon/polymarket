export type ProbabilityRowForGraph = {
  expiry_ts?: string;
  cache_expiry_ts?: string;
  valid_until?: string;
  refresh_display_until?: string;
  [key: string]: unknown;
};

export type ProbabilityPayloadForGraph<Row extends ProbabilityRowForGraph> = {
  ok?: boolean;
  state?: string;
  rows?: Row[];
  nowcast_rows?: Row[];
  previous_mc_retained?: boolean;
  retained_mc_rows?: number;
};

export type ProbabilityValueRow = ProbabilityRowForGraph & {
  contract?: string;
  contract_id?: string;
  event_id?: string;
  output_id?: string;
  market_slug?: string;
  cache_market_slug?: string;
  asset?: string;
  side?: string;
  start_ts?: string;
  cache_start_ts?: string;
  cache_expiry_ts?: string;
  asof_ts?: string;
  p_finish?: number;
  p_hat?: number;
  p_no_touch?: number;
  risk_adjusted_p_finish?: number;
  risk_adjusted_p_no_touch?: number;
  risk_adjustment?: number;
  terminal_probability_source?: string;
  pair_probability_sum_before?: number;
  pair_complement_gap?: number;
  pair_normalized?: boolean;
  counterparty_p_finish?: number;
  probability_kind?: string;
  path_count?: number;
  paths_per_generator?: number;
  generator_count?: number;
  paths_per_seed?: number;
  seed_count?: number;
  prior_fragment_count?: number;
  prior_fragment_reason?: string;
  prior_fragment_sparse?: boolean;
  prior_fragment_ids?: string[];
  prior_fragment_error?: string;
  prior_fragment_generators?: string[];
  cache_status?: string;
  generated_at?: string;
  effective_weights?: Record<string, number>;
  generator_metadata?: unknown;
  generator_summary?: unknown;
  block_reasons?: unknown[];
  simulation_preview?: unknown;
};

export type GeneratorBreakdownRow = {
  id: string;
  p_finish?: number;
  p_no_touch?: number;
  weight?: number;
  sparse?: boolean;
};

const ENSEMBLE_GENERATOR_ORDER = [
  "empirical_conditional",
  "block_bootstrap",
  "filtered_historical",
  "stress_overlay",
];

export type ProbabilityPayloadForEvents<Row extends ProbabilityValueRow> =
  ProbabilityPayloadForGraph<Row> & {
    generated_at?: string;
    cached?: boolean;
    nowcast_rows?: Row[];
  };

export function filterGraphableProbabilityRows<Row extends ProbabilityRowForGraph>(
  payload: ProbabilityPayloadForGraph<Row> | null,
  nowMs = Date.now(),
): Row[] {
  if (!payload) {
    return [];
  }
  const rows = Array.isArray(payload.rows) ? payload.rows : [];
  const allowExpiredValidity =
    payload.previous_mc_retained === true && Number(payload.retained_mc_rows ?? 0) > 0;
  return graphableRowsFrom(rows, nowMs, allowExpiredValidity);
}

export function filterGraphableProbabilityRowsIncludingNowcast<
  Row extends ProbabilityRowForGraph,
>(
  payload: ProbabilityPayloadForGraph<Row> | null,
  nowMs = Date.now(),
): Row[] {
  if (!payload) {
    return [];
  }
  const nowcastRows = Array.isArray(payload.nowcast_rows) ? payload.nowcast_rows : [];
  return [
    ...filterGraphableProbabilityRows(payload, nowMs),
    ...graphableRowsFrom(nowcastRows, nowMs, false),
  ];
}

function graphableRowsFrom<Row extends ProbabilityRowForGraph>(
  rows: Row[],
  nowMs: number,
  allowExpiredValidity: boolean,
) {
  return rows
    .filter((row): row is Row => isRecord(row))
    .filter((row) => isGraphableProbabilityRow(row, nowMs, allowExpiredValidity));
}

export function isGraphableProbabilityRow(
  row: ProbabilityRowForGraph,
  nowMs: number,
  allowExpiredValidity = false,
) {
  const expiryMs = timestampMs(row.expiry_ts ?? row.cache_expiry_ts);
  const expiryIsFresh = Number.isFinite(expiryMs) && expiryMs > nowMs;
  const refreshHoldMs = timestampMs(row.refresh_display_until);
  const refreshHoldIsFresh =
    allowExpiredValidity && Number.isFinite(refreshHoldMs) && refreshHoldMs > nowMs;
  if (!expiryIsFresh && !refreshHoldIsFresh) {
    return false;
  }
  const validUntilMs = timestampMs(row.valid_until);
  return allowExpiredValidity || !Number.isFinite(validUntilMs) || validUntilMs > nowMs;
}

export function probabilityDisplayValue(row?: ProbabilityValueRow | null) {
  if (!row) {
    return undefined;
  }
  return isFiniteNumber(row.p_hat) ? row.p_hat : row.p_finish;
}

export function riskAdjustedDisplayValue(row?: ProbabilityValueRow | null) {
  if (!row) {
    return undefined;
  }
  return isFiniteNumber(row.risk_adjusted_p_finish) ? row.risk_adjusted_p_finish : undefined;
}

export function pairCoherenceLabel(row?: ProbabilityValueRow | null) {
  if (!row || !isFiniteNumber(row.pair_probability_sum_before)) {
    return undefined;
  }
  const sum = row.pair_probability_sum_before.toFixed(3);
  return row.pair_normalized === true ? `normalized from ${sum} pair sum` : `pair sum ${sum}`;
}

export function probabilityRowKey(row: ProbabilityValueRow) {
  return keyParts([
    ["output", row.output_id],
    ["contract", row.contract_id],
    ["market", row.market_slug ?? row.cache_market_slug],
    ["contract_label", row.contract],
    ["asset", row.asset],
    ["side", row.side],
    ["start", row.start_ts ?? row.cache_start_ts],
    ["expiry", row.expiry_ts ?? row.cache_expiry_ts],
    ["asof", row.asof_ts],
  ]);
}

export function probabilitySelectionKey(row: ProbabilityValueRow) {
  const contractWindowKey = keyParts([
    ["contract", row.contract_id],
    ["market", row.market_slug ?? row.cache_market_slug],
    ["asset", row.asset],
    ["side", row.side],
    ["start", row.start_ts ?? row.cache_start_ts],
    ["expiry", row.expiry_ts ?? row.cache_expiry_ts],
  ]);
  if (contractWindowKey) {
    return contractWindowKey;
  }
  return keyParts([
    ["contract_label", row.contract],
    ["asset", row.asset],
    ["side", row.side],
    ["start", row.start_ts ?? row.cache_start_ts],
    ["expiry", row.expiry_ts ?? row.cache_expiry_ts],
  ]);
}

export function probabilityMetadata(row: ProbabilityValueRow) {
  const preview = parsePreview(row.simulation_preview);
  return {
    totalPaths: isFiniteNumber(row.path_count) ? row.path_count : undefined,
    pathsPerGenerator: isFiniteNumber(row.paths_per_generator)
      ? row.paths_per_generator
      : undefined,
    generatorCount: isFiniteNumber(row.generator_count) ? row.generator_count : undefined,
    pathsPerSeed: isFiniteNumber(row.paths_per_seed) ? row.paths_per_seed : undefined,
    seedCount: isFiniteNumber(row.seed_count) ? row.seed_count : undefined,
    previewPathCount: Array.isArray(preview?.sampled_paths)
      ? preview.sampled_paths.length
      : undefined,
  };
}

export function probabilityPriorSummary(row: ProbabilityValueRow) {
  const count = isFiniteNumber(row.prior_fragment_count)
    ? row.prior_fragment_count
    : undefined;
  const reason =
    typeof row.prior_fragment_reason === "string" && row.prior_fragment_reason.trim()
      ? row.prior_fragment_reason.trim()
      : undefined;
  const parts = [
    count !== undefined
      ? `${count} fragment${count === 1 ? "" : "s"}`
      : undefined,
    reason,
    row.prior_fragment_sparse === true ? "sparse" : undefined,
  ].filter((part): part is string => Boolean(part));
  return parts.length > 0 ? parts.join(", ") : undefined;
}

export function generatorBreakdownRows(row: ProbabilityValueRow): GeneratorBreakdownRow[] {
  const metadata = isRecord(row.generator_metadata) ? row.generator_metadata : {};
  const summarySource = isRecord(row.generator_summary)
    ? row.generator_summary
    : isRecord(metadata.generator_summary)
      ? metadata.generator_summary
      : {};
  const weightsSource = isRecord(row.effective_weights)
    ? row.effective_weights
    : isRecord(metadata.effective_weights)
      ? metadata.effective_weights
      : {};
  const generatorIds = orderedGeneratorIds([
    ...Object.keys(summarySource),
    ...Object.keys(weightsSource),
  ]);

  return generatorIds.flatMap((id) => {
    const summary = summarySource[id];
    const summaryRow = isRecord(summary) ? summary : {};
    const weight = isFiniteNumber(summaryRow.weight)
      ? summaryRow.weight
      : isFiniteNumber(weightsSource[id])
        ? weightsSource[id]
        : undefined;
    if (!isRecord(summary) && !isFiniteNumber(weight)) {
      return [];
    }
    return [
      {
        id,
        p_finish: isFiniteNumber(summaryRow.p_finish) ? summaryRow.p_finish : undefined,
        p_no_touch: isFiniteNumber(summaryRow.p_no_touch) ? summaryRow.p_no_touch : undefined,
        weight,
        sparse: typeof summaryRow.sparse === "boolean" ? summaryRow.sparse : undefined,
      },
    ];
  });
}

function orderedGeneratorIds(ids: string[]) {
  return [...new Set(ids)].sort((left, right) => {
    const leftIndex = ENSEMBLE_GENERATOR_ORDER.indexOf(left);
    const rightIndex = ENSEMBLE_GENERATOR_ORDER.indexOf(right);
    if (leftIndex !== -1 || rightIndex !== -1) {
      return (leftIndex === -1 ? ENSEMBLE_GENERATOR_ORDER.length : leftIndex) -
        (rightIndex === -1 ? ENSEMBLE_GENERATOR_ORDER.length : rightIndex);
    }
    return left.localeCompare(right);
  });
}

export function visibleProbabilityDiagnosticRows<Row extends ProbabilityValueRow>(
  rows: Row[],
  nowMs = Date.now(),
): Row[] {
  const groups = new Map<string, Row[]>();
  for (const row of rows) {
    const asset = normalizedAsset(row.asset) || assetFromMarketSlug(row.market_slug);
    if (!asset) {
      continue;
    }
    groups.set(asset, [...(groups.get(asset) ?? []), row]);
  }

  return [...groups.entries()]
    .sort(([left], [right]) => compareAsset(left, right))
    .flatMap(([, assetRows]) => activeWindowRows(assetRows, nowMs));
}

export function probabilityRowsWithRolloverHold<Row extends ProbabilityValueRow>(
  liveRows: Row[],
  heldRows: Row[],
  options: {
    nowMs?: number;
    heldAtMs?: number;
    holdMs?: number;
  } = {},
): Row[] {
  const nowMs = options.nowMs ?? Date.now();
  const heldAtMs = options.heldAtMs ?? 0;
  const holdMs = options.holdMs ?? 12_000;
  if (liveRows.length > 0) {
    return liveRows;
  }
  if (heldRows.length === 0 || heldAtMs <= 0) {
    return liveRows;
  }
  return nowMs - heldAtMs <= holdMs
    ? heldRows.filter((row) => isGraphableProbabilityRow(row, nowMs, true))
    : liveRows;
}

export function probabilityRuntimeStateLabel(value?: string | null) {
  const normalized = value?.trim().toUpperCase();
  if (!normalized) {
    return "Pending";
  }
  switch (normalized) {
    case "OK":
      return "Monte Carlo ready";
    case "NOWCAST":
      return "Fast estimate updating";
    case "STALE_INPUTS":
      return "Holding last Monte Carlo";
    case "PARTIAL":
      return "Partial Monte Carlo";
    case "DISABLED":
      return "Monte Carlo off";
    case "LOADING":
      return "Loading";
    case "ERROR":
      return "Runtime error";
    default:
      return normalized
        .toLowerCase()
        .split("_")
        .filter(Boolean)
        .map((part) => `${part.slice(0, 1).toUpperCase()}${part.slice(1)}`)
        .join(" ");
  }
}

export type LivePathStatusInput = {
  probabilityState?: string;
  rows?: ProbabilityValueRow[];
  offload?: {
    offload_allowed?: boolean;
    reason_codes?: unknown[];
    recommended_worker_mode?: string;
    input_count?: number;
    mc_eligible_input_count?: number;
    blocked_input_count?: number;
    max_input_state_lag_ms?: number;
  };
};

export type LivePathStatus = {
  state: "LIVE_PATHS" | "PARTIAL_PATHS" | "PATHS_BLOCKED" | "NOWCAST_ONLY" | "PATHS_PENDING";
  label: string;
  detail: string;
  reasons: string[];
};

export function livePathStatus(input: LivePathStatusInput): LivePathStatus {
  const rows = Array.isArray(input.rows) ? input.rows : [];
  const mcRows = rows.filter((row) => probabilityKind(row) !== "NOWCAST");
  const previewRows = mcRows.filter((row) => hasSampledPathPreview(row.simulation_preview));
  const nowcastRows = rows.filter((row) => probabilityKind(row) === "NOWCAST");
  const reasons = Array.isArray(input.offload?.reason_codes)
    ? input.offload.reason_codes.map(String).filter(Boolean)
    : [];
  const eligibleCount = numericCount(input.offload?.mc_eligible_input_count);
  const blockedCount = numericCount(input.offload?.blocked_input_count);
  const inputCount = numericCount(input.offload?.input_count);
  const hasBlockedInputs = blockedCount !== undefined && blockedCount > 0;
  const hasEligibleInputs = eligibleCount !== undefined && eligibleCount > 0;

  if (previewRows.length > 0) {
    const partial =
      previewRows.length < mcRows.length ||
      nowcastRows.length > 0 ||
      hasBlockedInputs ||
      (inputCount !== undefined &&
        eligibleCount !== undefined &&
        eligibleCount < inputCount);
    return {
      state: partial ? "PARTIAL_PATHS" : "LIVE_PATHS",
      label: partial ? "Partial paths" : "Live paths",
      detail: `${previewRows.length} preview row${previewRows.length === 1 ? "" : "s"}`,
      reasons,
    };
  }
  if (mcRows.length > 0) {
    return {
      state: "PARTIAL_PATHS",
      label: "Partial paths",
      detail: `${mcRows.length} MC row${mcRows.length === 1 ? "" : "s"}, preview pending`,
      reasons,
    };
  }
  const hasTransientNowcastBlock =
    nowcastRows.length > 0 &&
    input.offload?.offload_allowed !== false &&
    isTransientNowcastBlock(reasons);
  if (
    !hasTransientNowcastBlock &&
    (input.offload?.offload_allowed === false ||
      input.probabilityState === "OFFLOAD_BLOCKED" ||
      (hasBlockedInputs && !hasEligibleInputs))
  ) {
    return {
      state: "PATHS_BLOCKED",
      label: "Live paths blocked",
      detail: reasons.length > 0 ? reasons.join(", ") : "offload blocked",
      reasons,
    };
  }
  if (nowcastRows.length > 0) {
    return {
      state: "NOWCAST_ONLY",
      label: "Nowcast only",
      detail:
        reasons.length > 0
          ? reasons.join(", ")
          : hasEligibleInputs
            ? "MC preview pending"
            : "no sampled paths yet",
      reasons,
    };
  }
  return {
    state: "PATHS_PENDING",
    label: "Paths pending",
    detail: "waiting for probability rows",
    reasons,
  };
}

export function mergeProbabilityEventsIntoPayload<Row extends ProbabilityValueRow>(
  payload: ProbabilityPayloadForEvents<Row> | null,
  events: Row[],
): ProbabilityPayloadForEvents<Row> {
  const mcEvents = events.filter((event) => probabilityKind(event) !== "NOWCAST");
  const nowcastEvents = events.filter((event) => probabilityKind(event) === "NOWCAST");
  return {
    ...(payload ?? { ok: true, state: "OK" }),
    cached: false,
    generated_at: newestPayloadGeneratedAt(payload?.generated_at, events),
    rows: mergeProbabilityLaneRows(payload?.rows, mcEvents),
    nowcast_rows: mergeProbabilityLaneRows(payload?.nowcast_rows, nowcastEvents),
  };
}

export function mergeGraphableProbabilityPayloadRows<Row extends ProbabilityValueRow>(
  previous: ProbabilityPayloadForEvents<Row> | null,
  next: ProbabilityPayloadForEvents<Row>,
  nowMs = Date.now(),
): ProbabilityPayloadForEvents<Row> {
  if (next.state === "DISABLED") {
    return next;
  }
  const nextRows = Array.isArray(next.rows)
    ? next.rows.filter((row): row is Row => isRecord(row))
    : [];
  const previousRowsForPreview = Array.isArray(previous?.rows)
    ? previous.rows.filter((row): row is Row => isRecord(row))
    : [];
  if (!shouldRetainPreviousProbabilityRows(next)) {
    if (nextRows.length === 0 || previousRowsForPreview.length === 0) {
      return next;
    }
    return {
      ...next,
      rows: mergeCurrentProbabilityRowsWithPreviousPreview(
        previousRowsForPreview,
        nextRows,
      ),
    };
  }
  const previousRows = Array.isArray(previous?.rows)
    ? previous.rows
        .filter((row): row is Row => isRecord(row))
        .filter((row) => isGraphableProbabilityRow(row, nowMs, true))
    : [];
  if (previousRows.length === 0) {
    return next;
  }
  const previousKeys = new Set(previousRows.map(stableProbabilityMergeKey));
  const nextKeys = new Set(nextRows.map(stableProbabilityMergeKey));
  const rows = mergeProbabilityLaneRows(previousRows, nextRows);
  const retainedRows = previousRows.filter(
    (row) => !nextKeys.has(stableProbabilityMergeKey(row)),
  );
  const sameKeyRetainedRows = rows.filter((row) => {
    const key = stableProbabilityMergeKey(row);
    return (
      previousKeys.has(key) &&
      nextKeys.has(key) &&
      !isGraphableProbabilityRow(row, nowMs) &&
      isGraphableProbabilityRow(row, nowMs, true)
    );
  });
  const retainedRowCount = retainedRows.length + sameKeyRetainedRows.length;
  if (retainedRowCount === 0) {
    return {
      ...next,
      rows,
    };
  }
  return {
    ...next,
    previous_mc_retained: true,
    retained_mc_rows: retainedRowCount,
    rows,
  };
}

function shouldRetainPreviousProbabilityRows<Row extends ProbabilityValueRow>(
  payload: ProbabilityPayloadForEvents<Row>,
) {
  const rowCount = Array.isArray(payload.rows) ? payload.rows.length : 0;
  if (rowCount === 0) {
    return true;
  }
  const state = payload.state?.trim().toUpperCase();
  return state === "PARTIAL" || state === "STALE_INPUTS";
}

function mergeProbabilityLaneRows<Row extends ProbabilityValueRow>(
  rows: Row[] | undefined,
  events: Row[],
) {
  if (events.length === 0) {
    return rows ?? [];
  }
  const byKey = new Map<string, Row>();
  for (const row of rows ?? []) {
    byKey.set(stableProbabilityMergeKey(row), row);
  }
  for (const event of events) {
    const key = stableProbabilityMergeKey(event);
    const previous = byKey.get(key);
    if (previous && isOlderProbabilityRow(event, previous)) {
      continue;
    }
    byKey.set(key, { ...previous, ...event });
  }
  return [...byKey.values()];
}

function mergeCurrentProbabilityRowsWithPreviousPreview<Row extends ProbabilityValueRow>(
  previousRows: Row[],
  nextRows: Row[],
) {
  const previousByKey = new Map<string, Row>();
  for (const row of previousRows) {
    previousByKey.set(stableProbabilityMergeKey(row), row);
  }
  return nextRows.map((row) => {
    const previous = previousByKey.get(stableProbabilityMergeKey(row));
    if (!previous?.simulation_preview || row.simulation_preview) {
      return row;
    }
    return {
      ...row,
      simulation_preview: previous.simulation_preview,
    };
  });
}

function isTransientNowcastBlock(reasons: string[]) {
  if (reasons.length === 0) {
    return false;
  }
  const transientReasons = new Set([
    "runtime_not_ready",
    "sigma_invalid",
    "warming",
  ]);
  return reasons.every((reason) => transientReasons.has(reason));
}

function isOlderProbabilityRow(candidate: ProbabilityValueRow, existing: ProbabilityValueRow) {
  const candidateMs = rowFreshnessMs(candidate);
  const existingMs = rowFreshnessMs(existing);
  return Number.isFinite(candidateMs) && Number.isFinite(existingMs) && candidateMs < existingMs;
}

function rowFreshnessMs(row: ProbabilityValueRow) {
  const generatedAtMs = timestampMs(row.generated_at);
  const asofMs = timestampMs(row.asof_ts);
  if (Number.isFinite(generatedAtMs) && Number.isFinite(asofMs)) {
    return Math.max(generatedAtMs, asofMs);
  }
  if (Number.isFinite(generatedAtMs)) {
    return generatedAtMs;
  }
  return asofMs;
}

function stableProbabilityMergeKey(row: ProbabilityValueRow) {
  return (
    probabilitySelectionKey(row) ||
    probabilityRowKey(row) ||
    (typeof row.event_id === "string" ? row.event_id : JSON.stringify(row))
  );
}

function probabilityKind(row: ProbabilityValueRow) {
  return typeof row.probability_kind === "string"
    ? row.probability_kind.toUpperCase()
    : "MC";
}

function newestGeneratedAt(rows: ProbabilityValueRow[]) {
  let newestMs = Number.NEGATIVE_INFINITY;
  let newest: string | undefined;
  for (const row of rows) {
    const generatedAt = row.generated_at;
    const ms = timestampMs(generatedAt);
    if (generatedAt && Number.isFinite(ms) && ms > newestMs) {
      newestMs = ms;
      newest = generatedAt;
    }
  }
  return newest;
}

function newestPayloadGeneratedAt(
  payloadGeneratedAt: string | undefined,
  rows: ProbabilityValueRow[],
) {
  const eventGeneratedAt = newestGeneratedAt(rows);
  const payloadMs = timestampMs(payloadGeneratedAt);
  const eventMs = timestampMs(eventGeneratedAt);
  if (Number.isFinite(payloadMs) && Number.isFinite(eventMs)) {
    return eventMs >= payloadMs ? eventGeneratedAt : payloadGeneratedAt;
  }
  return eventGeneratedAt ?? payloadGeneratedAt;
}

function parsePreview(value: unknown): { sampled_paths?: unknown[] } | null {
  return isRecord(value) ? value : null;
}

function hasSampledPathPreview(value: unknown) {
  const preview = parsePreview(value);
  return Array.isArray(preview?.sampled_paths) && preview.sampled_paths.length > 0;
}

function numericCount(value: unknown) {
  return isFiniteNumber(value) ? value : undefined;
}

function activeWindowRows<Row extends ProbabilityValueRow>(rows: Row[], nowMs: number) {
  const ranked = rows
    .map((row) => ({ row, rank: contractTimingRank(row, nowMs) }))
    .sort((left, right) => {
      if (left.rank !== right.rank) {
        return left.rank - right.rank;
      }
      return (
        compareFiniteTimestamp(contractWindowDurationMs(left.row), contractWindowDurationMs(right.row)) ||
        compareFiniteTimestamp(contractStartMs(left.row), contractStartMs(right.row)) ||
        compareFiniteTimestamp(contractExpiryMs(left.row), contractExpiryMs(right.row)) ||
        compareSide(left.row.side, right.row.side)
      );
    });
  const preferred = ranked[0]?.row;
  if (!preferred) {
    return [];
  }
  const preferredStart = contractStartMs(preferred);
  const preferredExpiry = contractExpiryMs(preferred);
  const preferredDuration = contractWindowDurationMs(preferred);
  const dedupedRows = rows
    .filter(
      (row) =>
        sameTimestamp(contractStartMs(row), preferredStart) &&
        sameTimestamp(contractExpiryMs(row), preferredExpiry) &&
        sameTimestamp(contractWindowDurationMs(row), preferredDuration),
    )
    .reduce((deduped, row) => mergePreferredRow(deduped, row), new Map<string, Row>());
  return [...dedupedRows.values()].sort((left, right) => compareSide(left.side, right.side));
}

function mergePreferredRow<Row extends ProbabilityValueRow>(rows: Map<string, Row>, row: Row) {
  const key = probabilitySelectionKey(row) || probabilityRowKey(row) || JSON.stringify(row);
  const previous = rows.get(key);
  if (!previous || compareProbabilityRowCompleteness(row, previous) > 0) {
    rows.set(key, row);
  }
  return rows;
}

function compareProbabilityRowCompleteness(
  left: ProbabilityValueRow,
  right: ProbabilityValueRow,
) {
  const scoreDelta = probabilityRowCompletenessScore(left) - probabilityRowCompletenessScore(right);
  if (scoreDelta !== 0) {
    return scoreDelta;
  }
  return timestampMs(left.generated_at) - timestampMs(right.generated_at);
}

function probabilityRowCompletenessScore(row: ProbabilityValueRow) {
  let score = 0;
  if (typeof row.contract === "string" && row.contract.trim()) {
    score += 64;
  }
  if (
    typeof row.decision_hint === "string" &&
    row.decision_hint.trim() &&
    row.decision_hint !== "PENDING"
  ) {
    score += 32;
  }
  if (row.simulation_preview) {
    score += 16;
  }
  if (isFiniteNumber(row.age_ms)) {
    score += 8;
  }
  if (isFiniteNumber(row.path_count)) {
    score += 4;
  }
  if (typeof row.generated_at === "string" && row.generated_at.trim()) {
    score += 2;
  }
  if (typeof row.output_id === "string" && row.output_id.trim()) {
    score += 1;
  }
  return score;
}

function contractTimingRank(row: ProbabilityValueRow, nowMs: number) {
  const start = contractStartMs(row);
  const expiry = contractExpiryMs(row);
  if (Number.isFinite(start) && Number.isFinite(expiry) && start <= nowMs && nowMs < expiry) {
    return 0;
  }
  if (
    (Number.isFinite(start) && nowMs < start) ||
    (!Number.isFinite(start) && Number.isFinite(expiry) && nowMs < expiry)
  ) {
    return 1;
  }
  if (Number.isFinite(expiry)) {
    return 2;
  }
  return 3;
}

function contractStartMs(row: ProbabilityValueRow) {
  return timestampMs(row.start_ts ?? row.cache_start_ts);
}

function contractExpiryMs(row: ProbabilityValueRow) {
  return timestampMs(row.expiry_ts ?? row.cache_expiry_ts);
}

function contractWindowDurationMs(row: ProbabilityValueRow) {
  const start = contractStartMs(row);
  const expiry = contractExpiryMs(row);
  if (Number.isFinite(start) && Number.isFinite(expiry) && expiry > start) {
    return expiry - start;
  }
  return intervalMsFromText(row.market_slug) ??
    intervalMsFromText(row.cache_market_slug) ??
    intervalMsFromText(row.contract_id) ??
    intervalMsFromText(row.contract) ??
    Number.POSITIVE_INFINITY;
}

function intervalMsFromText(value: unknown) {
  const text = typeof value === "string" ? value.toLowerCase() : "";
  const match = text.match(/(?:^|[-_])(\d+)(m|h)(?:[-_]|$)/);
  if (!match) {
    return null;
  }
  const amount = Number(match[1]);
  if (!Number.isFinite(amount)) {
    return null;
  }
  return match[2] === "h" ? amount * 60 * 60_000 : amount * 60_000;
}

function compareFiniteTimestamp(left: number, right: number) {
  if (Number.isFinite(left) && Number.isFinite(right)) {
    return left - right;
  }
  if (Number.isFinite(left)) {
    return -1;
  }
  if (Number.isFinite(right)) {
    return 1;
  }
  return 0;
}

function sameTimestamp(left: number, right: number) {
  if (!Number.isFinite(left) && !Number.isFinite(right)) {
    return true;
  }
  return left === right;
}

function normalizedAsset(value: unknown) {
  const text = typeof value === "string" ? value.toUpperCase() : "";
  return text === "BTC" || text === "ETH" ? text : "";
}

function assetFromMarketSlug(value: unknown) {
  const text = typeof value === "string" ? value.toLowerCase() : "";
  if (text.includes("btc")) {
    return "BTC";
  }
  if (text.includes("eth")) {
    return "ETH";
  }
  return "";
}

function compareAsset(left: string, right: string) {
  return assetRank(left) - assetRank(right) || left.localeCompare(right);
}

function assetRank(asset: string) {
  if (asset === "BTC") {
    return 0;
  }
  if (asset === "ETH") {
    return 1;
  }
  return 2;
}

function compareSide(left: unknown, right: unknown) {
  return sideRank(left) - sideRank(right) || String(left ?? "").localeCompare(String(right ?? ""));
}

function sideRank(side: unknown) {
  const value = typeof side === "string" ? side.toUpperCase() : "";
  if (value === "UP") {
    return 0;
  }
  if (value === "DOWN") {
    return 1;
  }
  return 2;
}

function keyParts(parts: Array<[string, unknown]>) {
  return parts
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([label, value]) => `${label}=${String(value)}`)
    .join("|");
}

function timestampMs(value?: string | number | null) {
  if (value === undefined || value === null || value === "") {
    return Number.POSITIVE_INFINITY;
  }
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : Number.POSITIVE_INFINITY;
  }
  const parsed = new Date(value).getTime();
  return Number.isNaN(parsed) ? Number.POSITIVE_INFINITY : parsed;
}

function isRecord(value: unknown): value is ProbabilityRowForGraph {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}
