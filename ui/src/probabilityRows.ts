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
  path_count?: number;
  paths_per_seed?: number;
  seed_count?: number;
  simulation_preview?: unknown;
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
    totalPaths: isFiniteNumber(row.path_count) ? row.path_count : undefined,
    pathsPerSeed: isFiniteNumber(row.paths_per_seed) ? row.paths_per_seed : undefined,
    seedCount: isFiniteNumber(row.seed_count) ? row.seed_count : undefined,
    previewPathCount: Array.isArray(preview?.sampled_paths)
      ? preview.sampled_paths.length
      : undefined,
  };
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
