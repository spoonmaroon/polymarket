export type ProbabilityRowForGraph = {
  expiry_ts?: string;
  cache_expiry_ts?: string;
  valid_until?: string;
  [key: string]: unknown;
};

export type ProbabilityPayloadForGraph<Row extends ProbabilityRowForGraph> = {
  ok?: boolean;
  state?: string;
  rows?: Row[];
};

export type ProbabilityValueRow = ProbabilityRowForGraph & {
  contract_id?: string;
  output_id?: string;
  asset?: string;
  side?: string;
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
  const expiryIsFresh = Number.isFinite(expiryMs) && expiryMs > nowMs;
  if (!expiryIsFresh) {
    return false;
  }
  const validUntilMs = timestampMs(row.valid_until);
  return !Number.isFinite(validUntilMs) || validUntilMs > nowMs;
}

export function probabilityDisplayValue(row?: ProbabilityValueRow | null) {
  if (!row) {
    return undefined;
  }
  return isFiniteNumber(row.p_hat) ? row.p_hat : row.p_finish;
}

export function probabilityRowKey(row: ProbabilityValueRow) {
  return [
    row.output_id,
    row.contract_id,
    row.asset,
    row.side,
    row.expiry_ts,
    row.asof_ts,
  ]
    .filter((value) => value !== undefined && value !== null && value !== "")
    .join("|");
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
