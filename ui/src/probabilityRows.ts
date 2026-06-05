export type ProbabilityRowForGraph = {
  expiry_ts?: string;
  cache_expiry_ts?: string;
  refresh_display_until?: string;
  valid_until?: string;
  [key: string]: unknown;
};

export type ProbabilityPayloadForGraph<Row extends ProbabilityRowForGraph> = {
  ok?: boolean;
  state?: string;
  rows?: Row[];
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
  probability_kind?: string;
  generator_version?: string;
  path_count?: number;
  paths_per_seed?: number;
  seed_count?: number;
  cache_status?: string;
  mc_display_status?: string;
  generated_at?: string;
  latency?: {
    runtime_ms?: number;
    total_lag_ms?: number;
  };
  simulation_preview?: unknown;
};

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
  if (!Array.isArray(payload?.rows)) {
    return [];
  }
  return payload.rows
    .filter((row): row is Row => isRecord(row))
    .filter((row) => isGraphableProbabilityRow(row, nowMs));
}

export function isGraphableProbabilityRow(row: ProbabilityRowForGraph, nowMs: number) {
  const expiryMs = timestampMs(row.expiry_ts ?? row.cache_expiry_ts);
  const refreshDisplayUntilMs = timestampMs(row.refresh_display_until);
  const heldForRefresh =
    Number.isFinite(refreshDisplayUntilMs) && refreshDisplayUntilMs > nowMs;
  const expiryIsFresh = Number.isFinite(expiryMs) && expiryMs > nowMs;
  if (!expiryIsFresh && !heldForRefresh) {
    return false;
  }
  const validUntilMs = timestampMs(row.valid_until);
  return heldForRefresh || !Number.isFinite(validUntilMs) || validUntilMs > nowMs;
}

export function probabilityDisplayValue(row?: ProbabilityValueRow | null) {
  if (!row) {
    return undefined;
  }
  return isFiniteNumber(row.p_hat) ? row.p_hat : row.p_finish;
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
  return keyParts([
    ["contract", row.contract_id],
    ["market", row.market_slug ?? row.cache_market_slug],
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
    lane: probabilityKind(row),
    displayStatus: probabilityDisplayStatus(row),
    totalPaths: isFiniteNumber(row.path_count) ? row.path_count : undefined,
    pathsPerSeed: isFiniteNumber(row.paths_per_seed) ? row.paths_per_seed : undefined,
    seedCount: isFiniteNumber(row.seed_count) ? row.seed_count : undefined,
    previewPathCount: Array.isArray(preview?.sampled_paths)
      ? preview.sampled_paths.length
      : undefined,
    runtimeMs: isFiniteNumber(row.latency?.runtime_ms) ? row.latency.runtime_ms : undefined,
    totalLagMs: isFiniteNumber(row.latency?.total_lag_ms)
      ? row.latency.total_lag_ms
      : undefined,
    generatorVersion: row.generator_version,
    cacheStatus: row.cache_status,
  };
}

export function probabilityDisplayStatus(row: ProbabilityValueRow, nowMs = Date.now()) {
  if (row.mc_display_status === "held") {
    return "held";
  }
  const refreshDisplayUntilMs = timestampMs(row.refresh_display_until);
  if (Number.isFinite(refreshDisplayUntilMs) && refreshDisplayUntilMs > nowMs) {
    return "held";
  }
  return "live";
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
    generated_at: newestGeneratedAt(events) ?? payload?.generated_at,
    rows: mergeProbabilityLaneRows(payload?.rows, mcEvents),
    nowcast_rows: mergeProbabilityLaneRows(payload?.nowcast_rows, nowcastEvents),
  };
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
    byKey.set(key, { ...previous, ...event });
  }
  return [...byKey.values()];
}

function stableProbabilityMergeKey(row: ProbabilityValueRow) {
  return (
    probabilitySelectionKey(row) ||
    probabilityRowKey(row) ||
    (typeof row.event_id === "string" ? row.event_id : JSON.stringify(row))
  );
}

export function probabilityKind(row: ProbabilityValueRow) {
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

function parsePreview(value: unknown): { sampled_paths?: unknown[] } | null {
  return isRecord(value) ? value : null;
}

function keyParts(parts: Array<[string, unknown]>) {
  return parts
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([label, value]) => `${label}=${String(value)}`)
    .join("|");
}

function timestampMs(value?: string | number | null) {
  if (!value) {
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
