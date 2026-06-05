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
