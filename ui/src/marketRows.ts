type ProbabilityBackedMarketRow = {
  upProbability?: unknown;
  downProbability?: unknown;
};

export function visibleMarketMonitorRows<Row extends ProbabilityBackedMarketRow>(
  rows: Row[],
  probabilityCount: number,
): Row[] {
  if (probabilityCount <= 0) {
    return rows;
  }
  const probabilityRows = rows.filter(hasMarketProbability);
  return probabilityRows.length > 0 ? probabilityRows : rows;
}

export function marketSideKey(
  marketSlug?: string,
  asset?: string,
  expiryTs?: string,
  side?: string,
  startTs?: string,
): string | null {
  const normalizedSide = normalizeSide(side);
  if (!normalizedSide) {
    return null;
  }
  const normalizedExpiry = normalizeTimestampKey(expiryTs);
  const normalizedStart = normalizeTimestampKey(startTs);
  if (marketSlug?.trim()) {
    return `${marketSlug.trim().toLowerCase()}|${normalizedStart ?? ""}|${
      normalizedExpiry ?? ""
    }|${normalizedSide}`;
  }
  const normalizedAsset = normalizeAsset(asset);
  if (!normalizedAsset || !normalizedExpiry) {
    return null;
  }
  return `${normalizedAsset}|${normalizedStart ?? ""}|${normalizedExpiry}|${normalizedSide}`;
}

export function marketGroupKey(
  marketSlug?: string,
  asset?: string,
  expiryTs?: string,
  startTs?: string,
) {
  const normalizedAsset = normalizeAsset(asset);
  const normalizedExpiry = normalizeTimestampKey(expiryTs);
  const normalizedStart = normalizeTimestampKey(startTs);
  if (marketSlug?.trim()) {
    return `${marketSlug.trim().toLowerCase()}|${normalizedStart ?? ""}|${
      normalizedExpiry ?? ""
    }`;
  }
  if (!normalizedAsset || !normalizedExpiry) {
    return null;
  }
  return `${normalizedAsset}|${normalizedStart ?? ""}|${normalizedExpiry}`;
}

function hasMarketProbability(row: ProbabilityBackedMarketRow) {
  return Boolean(row.upProbability || row.downProbability);
}

function normalizeTimestampKey(value?: string) {
  const trimmed = value?.trim();
  if (!trimmed) {
    return null;
  }
  const parsed = new Date(trimmed).getTime();
  return Number.isNaN(parsed) ? trimmed : new Date(parsed).toISOString();
}

function normalizeAsset(value?: string) {
  return value?.trim().toUpperCase() ?? "";
}

function normalizeSide(value?: string) {
  return value?.trim().toUpperCase() ?? "";
}
