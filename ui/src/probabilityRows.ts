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
  probability_kind?: string;
  path_count?: number;
  paths_per_seed?: number;
  seed_count?: number;
  generated_at?: string;
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
  const allowExpiredValidity =
    payload.previous_mc_retained === true && Number(payload.retained_mc_rows ?? 0) > 0;
  return payload.rows
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
  if (!expiryIsFresh) {
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
    pathsPerSeed: isFiniteNumber(row.paths_per_seed) ? row.paths_per_seed : undefined,
    seedCount: isFiniteNumber(row.seed_count) ? row.seed_count : undefined,
    previewPathCount: Array.isArray(preview?.sampled_paths)
      ? preview.sampled_paths.length
      : undefined,
  };
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
  return nowMs - heldAtMs <= holdMs ? heldRows : liveRows;
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

function parsePreview(value: unknown): { sampled_paths?: unknown[] } | null {
  return isRecord(value) ? value : null;
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
