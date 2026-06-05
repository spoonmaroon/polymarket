import { useEffect, useMemo, useState } from "react";

const LIVE_LIMIT = 12;
const PROBABILITY_LIMIT = 24;
const POLL_INTERVAL_MS = 2500;

type JsonRecord = Record<string, unknown>;

type ApiState<T> = {
  status: "loading" | "ready" | "error";
  payload: T | null;
  error: string | null;
  notice: string | null;
  updatedAt: number | null;
};

type RuntimeStatus = {
  ok?: boolean;
  state?: string;
  generated_at?: string;
  schema_kind?: string;
  mode?: string;
  error?: string | null;
  counts?: Record<string, number>;
};

type RuntimeGates = {
  ok?: boolean;
  state?: string;
  generated_at?: string;
  failures?: unknown[];
  errors?: unknown[];
  checks?: unknown[];
  reasons?: unknown[];
};

type RuntimeMonitor = {
  ok?: boolean;
  state?: string;
  generated_at?: string;
  orderbooks?: RuntimeOrderbookRow[];
  contracts?: unknown[];
  health_flags?: unknown[];
  latency_marks?: unknown[];
  source_errors?: JsonRecord;
  websocket_status?: unknown[];
  hot_decision_telemetry?: unknown;
};

type RuntimeOrderbookRow = {
  market_slug?: string;
  contract_id?: string;
  token_id?: string;
  asset?: string;
  side?: string;
  start_ts?: string;
  expiry_ts?: string;
  threshold_price?: string | number | null;
  best_bid?: number | string | null;
  best_ask?: number | string | null;
  spread?: number | string | null;
  bid_size_top?: number | string | null;
  ask_size_top?: number | string | null;
  event_ts?: string;
  observed_ts?: string;
};

type RuntimeVolatility = {
  state?: string;
  generated_at?: string;
  source_key?: string;
  lookback_limit?: number;
  rows?: RuntimeVolatilityRow[];
  errors?: unknown[];
};

type RuntimeVolatilityRow = {
  asset?: string;
  asof_ts?: string;
  sigma_tau?: number | null;
  short_realized_vol?: number | null;
  medium_realized_vol?: number | null;
  long_realized_vol?: number | null;
  volatility_regime?: string;
  age_ms?: number;
  flags?: string[];
};

type RuntimeLivePayload = {
  ok?: boolean;
  server_sent_at?: string;
  status?: RuntimeStatus;
  gates?: RuntimeGates;
  monitor?: RuntimeMonitor;
  volatility?: RuntimeVolatility;
  latency?: JsonRecord;
};

type SimulationPath = {
  index: number;
  terminal_win: boolean;
  no_touch_win: boolean;
  points: number[];
};

type HistogramBucket = {
  lower: number;
  upper: number;
  count: number;
};

type SimulationPreview = {
  path_count: number;
  steps: number;
  start_price: number;
  threshold: number;
  comparison_operator: string;
  terminal_win_count: number;
  no_touch_win_count: number;
  sampled_paths: SimulationPath[];
  terminal_histogram: HistogramBucket[];
};

type ProbabilityRow = {
  contract?: string;
  contract_id?: string;
  market_slug?: string;
  output_id?: string;
  asset?: string;
  side?: string;
  start_ts?: string;
  asof_ts?: string;
  expiry_ts?: string;
  p_finish?: number;
  p_no_touch?: number;
  z_path?: number;
  sigma_tau?: number;
  age_ms?: number;
  flags?: string[];
  model_version?: string;
  mc_dispersion?: number | null;
  uncertainty_buffer?: number | null;
  path_diagnosis?: string[];
  effective_weights?: Record<string, number>;
  decision_hint?: string | null;
  edge_after_costs?: number | null;
  required_edge?: number | null;
  gate_reasons?: string[];
  cache_key?: string;
  cache_status?: string;
  cache_market_slug?: string;
  cache_start_ts?: string;
  cache_expiry_ts?: string;
  cache_asof_ts?: string;
  generated_at?: string;
  valid_from?: string;
  valid_until?: string;
  time_bucket?: string;
  z_path_bucket?: string;
  sigma_bucket?: string;
  volatility_regime?: string;
  generator_version?: string;
  path_count?: number;
  generator_metadata?: JsonRecord;
  cache_metadata?: JsonRecord;
  grid_cache?: JsonRecord;
  simulation_preview?: unknown;
};

type ProbabilityPayload = {
  schema_version?: string;
  ok?: boolean;
  state?: string;
  error?: string | null;
  generated_at?: string;
  cached?: boolean;
  model_version?: string | null;
  rows?: ProbabilityRow[];
  skipped?: number;
  errors?: unknown[];
  cache_metadata?: JsonRecord;
  grid_cache?: JsonRecord;
  cache?: JsonRecord;
};

type MarketMonitorRow = {
  key: string;
  asset: string;
  marketSlug: string;
  expiryTs?: string;
  threshold?: string | number | null;
  up?: RuntimeOrderbookRow;
  down?: RuntimeOrderbookRow;
  upProbability?: ProbabilityRow;
  downProbability?: ProbabilityRow;
  volatility?: RuntimeVolatilityRow;
};

const emptyLive: ApiState<RuntimeLivePayload> = {
  status: "loading",
  payload: null,
  error: null,
  notice: null,
  updatedAt: null,
};

const emptyProbabilities: ApiState<ProbabilityPayload> = {
  status: "loading",
  payload: null,
  error: null,
  notice: null,
  updatedAt: null,
};

export function App() {
  const [live, setLive] = useState<ApiState<RuntimeLivePayload>>(emptyLive);
  const [probabilities, setProbabilities] =
    useState<ApiState<ProbabilityPayload>>(emptyProbabilities);
  const rows = safeRows(probabilities.payload);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const marketRows = useMemo(
    () =>
      buildMarketMonitorRows(
        live.payload?.monitor?.orderbooks,
        rows,
        live.payload?.volatility?.rows,
      ),
    [live.payload?.monitor?.orderbooks, live.payload?.volatility?.rows, rows],
  );
  const selectedRow = useMemo(
    () => rows.find((row) => rowKey(row) === selectedId) ?? rows[0] ?? null,
    [rows, selectedId],
  );

  useEffect(() => {
    let active = true;

    async function poll() {
      const [liveResult, probabilityResult] = await Promise.all([
        fetchJson<RuntimeLivePayload>(`/api/runtime/live?limit=${LIVE_LIMIT}`),
        fetchJson<ProbabilityPayload>(
          `/api/runtime/probabilities?limit=${PROBABILITY_LIMIT}`,
        ),
      ]);
      if (!active) {
        return;
      }
      setLive(toApiState(liveResult));
      setProbabilities((previous) => toProbabilityApiState(probabilityResult, previous));
    }

    void poll();
    const intervalId = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
    return () => {
      active = false;
      window.clearInterval(intervalId);
    };
  }, []);

  useEffect(() => {
    if (rows.length === 0) {
      setSelectedId(null);
      return;
    }
    if (!selectedId || !rows.some((row) => rowKey(row) === selectedId)) {
      setSelectedId(rowKey(rows[0]));
    }
  }, [rows, selectedId]);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>Runtime Monitor</h1>
          <p>Read-only live status, probability outputs, gates, and cache health.</p>
        </div>
        <div className="mode-lock">Paper / read-only</div>
      </header>

      <StatusStrip
        live={live}
        probabilities={probabilities}
        rowCount={rows.length}
        rows={rows}
      />

      <section className="runtime-grid" aria-label="Runtime dashboard">
        <section className="main-stack">
          <MarketMonitor
            rows={marketRows}
            selectedProbabilityKey={selectedRow ? rowKey(selectedRow) : null}
            onSelectProbability={setSelectedId}
          />
          <SelectedDetails row={selectedRow} probabilities={probabilities.payload} />
          <ProbabilityTable
            rows={rows}
            selectedKey={selectedRow ? rowKey(selectedRow) : null}
            onSelect={setSelectedId}
            state={probabilities}
          />
        </section>
        <aside className="side-stack">
          <SystemHealth live={live} probabilities={probabilities} />
          <CompactVolatility volatility={live.payload?.volatility ?? null} />
          <GateAndWeights row={selectedRow} />
        </aside>
      </section>
    </main>
  );
}

function StatusStrip({
  live,
  probabilities,
  rowCount,
  rows,
}: {
  live: ApiState<RuntimeLivePayload>;
  probabilities: ApiState<ProbabilityPayload>;
  rowCount: number;
  rows: ProbabilityRow[];
}) {
  const livePayload = live.payload;
  const probabilityPayload = probabilities.payload;
  const liveState =
    livePayload?.status?.state ??
    livePayload?.monitor?.state ??
    (live.status === "loading" ? "LOADING" : livePayload?.ok ? "OK" : "OFFLINE");
  const generatedAt =
    probabilityPayload?.generated_at ??
    livePayload?.status?.generated_at ??
    livePayload?.server_sent_at;
  return (
    <section className="status-strip" aria-label="Runtime status">
      <Metric label="Live state" value={liveState} tone={liveTone(livePayload?.ok, live.status)} />
      <Metric label="Generated" value={formatTimestamp(generatedAt)} />
      <Metric
        label="Probability"
        value={probabilityPayload?.state ?? statusLabel(probabilities.status)}
        tone={probabilityTone(probabilityPayload)}
      />
      <Metric label="Rows" value={formatInteger(rowCount)} />
      <Metric label="Cache" value={formatCacheState(probabilityPayload, rows)} />
      <Metric label="API build" value={formatLatency(livePayload?.latency)} />
      {live.error ? <div className="status-note">Live API: {live.error}</div> : null}
      {probabilities.error ? (
        <div className="status-note">Probability API: {probabilities.error}</div>
      ) : null}
      {probabilities.notice ? (
        <div className="status-note status-note-info">{probabilities.notice}</div>
      ) : null}
    </section>
  );
}

function MarketMonitor({
  rows,
  selectedProbabilityKey,
  onSelectProbability,
}: {
  rows: MarketMonitorRow[];
  selectedProbabilityKey: string | null;
  onSelectProbability: (key: string) => void;
}) {
  return (
    <section className="panel market-panel">
      <PanelHeader
        title="Market Monitor"
        subtitle="Orderbook, target, probability, and volatility context by market."
      />
      {rows.length === 0 ? (
        <EmptyState title="No markets" body="Waiting for live orderbooks." />
      ) : (
        <div className="market-table" role="table" aria-label="Market probabilities">
          <div className="market-row market-head" role="row">
            <span>Market</span>
            <span>Expiry</span>
            <span>K</span>
            <span>UP bid/ask</span>
            <span>P UP</span>
            <span>DOWN bid/ask</span>
            <span>P DOWN</span>
            <span>Vol</span>
          </div>
          {rows.map((row) => (
            <div className="market-row" key={row.key} role="row">
              <span className="market-name">
                <strong>{row.asset}</strong>
                <small>{shortSlug(row.marketSlug)}</small>
              </span>
              <span>{formatTimestamp(row.expiryTs)}</span>
              <span>{formatThreshold(row.threshold)}</span>
              <span>{formatQuote(row.up)}</span>
              <ProbabilityButton
                row={row.upProbability}
                selectedKey={selectedProbabilityKey}
                onSelect={onSelectProbability}
              />
              <span>{formatQuote(row.down)}</span>
              <ProbabilityButton
                row={row.downProbability}
                selectedKey={selectedProbabilityKey}
                onSelect={onSelectProbability}
              />
              <span>{formatSmall(row.volatility?.sigma_tau ?? undefined)}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function ProbabilityButton({
  row,
  selectedKey,
  onSelect,
}: {
  row?: ProbabilityRow;
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  if (!row) {
    return <span>-</span>;
  }
  const key = rowKey(row);
  return (
    <button
      className={
        key === selectedKey ? "probability-chip selected-probability" : "probability-chip"
      }
      onClick={() => onSelect(key)}
      type="button"
    >
      {formatProbability(row.p_finish)}
    </button>
  );
}

function ProbabilityTable({
  rows,
  selectedKey,
  onSelect,
  state,
}: {
  rows: ProbabilityRow[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
  state: ApiState<ProbabilityPayload>;
}) {
  return (
    <section className="panel probability-panel">
      <PanelHeader
        title="Probability Diagnostics"
        subtitle="Full cached-grid row diagnostics from /api/runtime/probabilities."
      />
      {rows.length === 0 ? (
        <EmptyState
          title="No probability rows"
          body={probabilityEmptyBody(state)}
        />
      ) : (
        <div className="probability-table" role="table" aria-label="Probability outputs">
          <div className="probability-row table-head" role="row">
            <span>Contract</span>
            <span>p_finish</span>
            <span>p_no_touch</span>
            <span>Gate</span>
            <span>Cache</span>
            <span>Age</span>
          </div>
          {rows.map((row) => {
            const key = rowKey(row);
            return (
              <button
                className={key === selectedKey ? "probability-row selected-row" : "probability-row"}
                key={key}
                onClick={() => onSelect(key)}
                type="button"
                role="row"
              >
                <span className="contract-cell">
                  <strong>{contractLabel(row)}</strong>
                  <small>{compactList([row.asset, row.side, row.contract_id])}</small>
                </span>
                <span>{formatProbability(row.p_finish)}</span>
                <span>{formatProbability(row.p_no_touch)}</span>
                <span>
                  <GatePill value={row.decision_hint} />
                </span>
                <span>{formatRowCache(row)}</span>
                <span>{formatAge(row.age_ms)}</span>
              </button>
            );
          })}
        </div>
      )}
    </section>
  );
}

function SelectedDetails({
  row,
  probabilities,
}: {
  row: ProbabilityRow | null;
  probabilities: ProbabilityPayload | null;
}) {
  if (!row) {
    return (
      <section className="panel detail-panel">
        <PanelHeader title="Selected Contract" subtitle="Waiting for a probability row." />
        <EmptyState title="Nothing selected" body="The details panel appears when rows arrive." />
      </section>
    );
  }

  const preview = parseSimulationPreview(row.simulation_preview);
  return (
    <section className="panel detail-panel">
      <div className="simulation-head">
        <div>
          <p className="panel-kicker">Selected contract</p>
          <h2>{contractLabel(row)}</h2>
          <p>{row.output_id ?? row.contract_id ?? "runtime probability row"}</p>
        </div>
        <GatePill value={row.decision_hint} />
      </div>

      <div className="hero-metrics">
        <Metric label="p_finish" value={formatProbability(row.p_finish)} />
        <Metric label="p_no_touch" value={formatProbability(row.p_no_touch)} />
        <Metric label="edge / required" value={formatEdge(row)} />
        <Metric label="sigma_tau" value={formatSmall(row.sigma_tau)} />
      </div>

      <div className="details-layout">
        <MonteCarloCanvas preview={preview} row={row} />
      </div>

      <div className="simulation-footer">
        <Metric label="as-of" value={formatTimestamp(row.asof_ts)} />
        <Metric label="expiry" value={formatTimestamp(row.expiry_ts)} />
        <Metric label="model" value={row.model_version ?? probabilities?.model_version ?? "-"} />
        <Metric label="skipped" value={formatInteger(probabilities?.skipped)} />
      </div>
    </section>
  );
}

function SystemHealth({
  live,
  probabilities,
}: {
  live: ApiState<RuntimeLivePayload>;
  probabilities: ApiState<ProbabilityPayload>;
}) {
  const payload = live.payload;
  const monitor = payload?.monitor;
  const gates = payload?.gates;
  const volatility = payload?.volatility;
  return (
    <section className="panel health-panel">
      <PanelHeader title="Live Health" subtitle="Polling /api/runtime/live." />
      <div className="detail-grid">
        <Metric label="Status" value={payload?.status?.state ?? statusLabel(live.status)} />
        <Metric label="Gates" value={gates?.ok === false ? "BLOCKED" : gates?.state ?? "OK"} />
        <Metric label="Orderbooks" value={formatInteger(arrayLength(monitor?.orderbooks))} />
        <Metric label="Volatility" value={volatility?.state ?? "-"} />
      </div>

      <section className="detail-section">
        <h3>Gate / Diagnosis</h3>
        <ChipRow
          values={[
            ...unknownList(gates?.failures),
            ...unknownList(gates?.errors),
            ...unknownList(gates?.reasons),
            ...unknownList(monitor?.health_flags),
            ...unknownList(probabilities.payload?.errors),
          ]}
          fallback={gates?.ok === false ? "BLOCKED" : "CLEAR"}
        />
      </section>

      <section className="detail-section">
        <h3>Runtime Metadata</h3>
        <KeyValueList
          entries={[
            ["server_sent_at", payload?.server_sent_at],
            ["status_generated_at", payload?.status?.generated_at],
            ["monitor_generated_at", monitor?.generated_at],
            ["schema", payload?.status?.schema_kind],
            ["mode", payload?.status?.mode],
            ["vol_source", volatility?.source_key],
            ["vol_lookback", volatility?.lookback_limit],
          ]}
        />
      </section>
    </section>
  );
}

function CompactVolatility({ volatility }: { volatility: RuntimeVolatility | null }) {
  const rows = Array.isArray(volatility?.rows) ? volatility.rows : [];
  return (
    <section className="panel compact-volatility">
      <PanelHeader
        title="Volatility"
        subtitle={compactList([
          volatility?.state,
          volatility?.source_key ? shortSource(volatility.source_key) : undefined,
        ])}
      />
      {rows.length === 0 ? (
        <EmptyState title="Vol pending" body="Waiting for BTC/ETH volatility rows." />
      ) : (
        <div className="volatility-strip">
          {rows.map((row) => (
            <div className="volatility-card" key={row.asset ?? JSON.stringify(row)}>
              <span>{row.asset ?? "-"}</span>
              <strong>{formatSmall(row.sigma_tau ?? undefined)}</strong>
              <small>{compactList([row.volatility_regime, formatAge(row.age_ms)])}</small>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function GateAndWeights({ row }: { row: ProbabilityRow | null }) {
  if (!row) {
    return null;
  }
  return (
    <section className="panel compact-detail">
      <PanelHeader title="Gate + Weights" subtitle={row.contract_id ?? "selected row"} />
      <div className="detail-grid">
        <Metric label="mc_dispersion" value={formatOptional(row.mc_dispersion)} />
        <Metric label="uncertainty" value={formatOptional(row.uncertainty_buffer)} />
        <Metric label="required_edge" value={formatOptional(row.required_edge)} />
        <Metric label="edge_after" value={formatOptional(row.edge_after_costs)} />
      </div>
      <section className="detail-section">
        <h3>Weights</h3>
        <WeightBars weights={row.effective_weights ?? {}} />
      </section>
      <section className="detail-section">
        <h3>Row Diagnosis</h3>
        <ChipRow
          values={[
            ...unknownList(row.gate_reasons),
            ...unknownList(row.path_diagnosis),
            ...unknownList(row.flags),
          ]}
          fallback="CLEAR"
        />
      </section>
    </section>
  );
}

function MonteCarloCanvas({
  preview,
  row,
}: {
  preview: SimulationPreview | null;
  row: ProbabilityRow;
}) {
  const geometry = useMemo(() => (preview ? buildPathGeometry(preview) : null), [preview]);
  if (!preview || !geometry) {
    return (
      <div className="chart-empty">
        <strong>Path preview not in this poll</strong>
        <span>Cached probabilities are live; sampled paths render when preview data is attached.</span>
      </div>
    );
  }

  return (
    <div className="path-chart">
      <div className="chart-labels">
        <span>Threshold {formatPrice(preview.threshold)}</span>
        <span>{formatInteger(preview.path_count)} paths</span>
      </div>
      <svg viewBox="0 0 760 310" role="img" aria-label="Monte Carlo sampled path fan">
        <line
          className="threshold-line"
          x1="28"
          x2="732"
          y1={geometry.thresholdY}
          y2={geometry.thresholdY}
        />
        {geometry.paths.map((path) => (
          <path
            className={path.terminalWin ? "mc-path path-win" : "mc-path path-loss"}
            d={path.d}
            key={path.index}
          />
        ))}
        <circle className="start-dot" cx="28" cy={geometry.startY} r="4" />
        <text className="axis-label" x="28" y="300">
          t0
        </text>
        <text className="axis-label" x="705" y="300">
          expiry
        </text>
      </svg>
      <div className="chart-caption">
        <span>{formatPreviewWinCounts(preview)}</span>
        <span>z {formatSigned(row.z_path)}</span>
      </div>
    </div>
  );
}

function MetadataPanel({ title, entries }: { title: string; entries: Array<[string, unknown]> }) {
  return (
    <div className="metadata-panel">
      <h3>{title}</h3>
      <KeyValueList entries={entries} />
    </div>
  );
}

function PanelHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="panel-heading">
      <div>
        <h2>{title}</h2>
        <p>{subtitle}</p>
      </div>
    </div>
  );
}

function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <span>{body}</span>
    </div>
  );
}

function Metric({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "good" | "warn";
}) {
  return (
    <div className={`metric metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function WeightBars({ weights }: { weights: Record<string, number> }) {
  const entries = Object.entries(weights)
    .filter(([, value]) => Number.isFinite(value))
    .sort(([left], [right]) => left.localeCompare(right));
  if (entries.length === 0) {
    return <p className="quiet">No generator weights attached.</p>;
  }
  return (
    <div className="weight-bars">
      {entries.map(([name, value]) => (
        <div className="weight-row" key={name}>
          <div className="weight-label">
            <span>{shortWeightLabel(name)}</span>
            <strong>{Math.round(value * 100)}%</strong>
          </div>
          <div className="bar-track" aria-hidden="true">
            <div className="bar-fill" style={{ width: `${clamp01(value) * 100}%` }} />
          </div>
        </div>
      ))}
    </div>
  );
}

function GatePill({ value }: { value?: string | null }) {
  const label = formatGate(value);
  return <span className={`gate-pill gate-${gateTone(label)}`}>{label}</span>;
}

function ChipRow({ values, fallback }: { values: unknown[]; fallback: string }) {
  const chips = values.map(compactValue).filter(Boolean);
  return (
    <div className="chip-row">
      {(chips.length > 0 ? chips : [fallback]).map((value) => (
        <span className="chip" key={value}>
          {sanitizeOperatorLabel(value)}
        </span>
      ))}
    </div>
  );
}

function KeyValueList({ entries }: { entries: Array<[string, unknown]> }) {
  const visible = entries
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([key, value]) => [key, compactValue(value)] as [string, string])
    .filter(([, value]) => value !== "");
  if (visible.length === 0) {
    return <p className="quiet">No metadata attached.</p>;
  }
  return (
    <dl className="kv-list">
      {visible.map(([key, value]) => (
        <div className="kv-row" key={key}>
          <dt>{key}</dt>
          <dd>{sanitizeOperatorLabel(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

async function fetchJson<T>(url: string): Promise<{ payload: T | null; error: string | null }> {
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    return { payload: (await response.json()) as T, error: null };
  } catch (error) {
    return {
      payload: null,
      error: error instanceof Error ? error.message : "runtime API unavailable",
    };
  }
}

function toApiState<T>(result: { payload: T | null; error: string | null }): ApiState<T> {
  return {
    status: result.error ? "error" : "ready",
    payload: result.payload,
    error: result.error,
    notice: null,
    updatedAt: Date.now(),
  };
}

function toProbabilityApiState(
  result: { payload: ProbabilityPayload | null; error: string | null },
  previous: ApiState<ProbabilityPayload>,
): ApiState<ProbabilityPayload> {
  const next = toApiState(result);
  const previousRows = safeRows(previous.payload);
  const nextRows = safeRows(next.payload);
  if (!result.error && next.payload && nextRows.length === 0 && previousRows.length > 0) {
    return {
      ...previous,
      status: "ready",
      error: null,
      notice: `Probability rollover returned 0 rows; showing last ${previousRows.length}.`,
      updatedAt: Date.now(),
    };
  }
  if (!result.error && next.payload && previousRows.length > 0 && nextRows.length > 0) {
    return {
      ...next,
      payload: mergeProbabilityPreviews(previous.payload, next.payload),
    };
  }
  return next;
}

function safeRows(payload: ProbabilityPayload | null): ProbabilityRow[] {
  return Array.isArray(payload?.rows) ? payload.rows.filter(isRecord) : [];
}

function mergeProbabilityPreviews(
  previous: ProbabilityPayload | null,
  next: ProbabilityPayload,
): ProbabilityPayload {
  const previousRowsByKey = new Map<string, ProbabilityRow>();
  for (const row of safeRows(previous)) {
    if (row.simulation_preview) {
      previousRowsByKey.set(probabilityIdentityKey(row), row);
    }
  }
  if (previousRowsByKey.size === 0 || !Array.isArray(next.rows)) {
    return next;
  }
  return {
    ...next,
    rows: next.rows.map((row) => {
      if (row.simulation_preview) {
        return row;
      }
      const previousRow = previousRowsByKey.get(probabilityIdentityKey(row));
      if (!previousRow?.simulation_preview) {
        return row;
      }
      return {
        ...row,
        simulation_preview: previousRow.simulation_preview,
      };
    }),
  };
}

function probabilityIdentityKey(row: ProbabilityRow) {
  return (
    row.contract_id ??
    row.output_id ??
    marketSideKey(row.market_slug, row.asset, row.expiry_ts, row.side) ??
    `${contractLabel(row)}|${row.expiry_ts ?? ""}`
  );
}

function buildMarketMonitorRows(
  orderbooks: RuntimeOrderbookRow[] | undefined,
  probabilities: ProbabilityRow[],
  volatilityRows: RuntimeVolatilityRow[] | undefined,
): MarketMonitorRow[] {
  const probabilityByMarketSide = new Map<string, ProbabilityRow>();
  for (const row of probabilities) {
    const key = marketSideKey(row.market_slug, row.asset, row.expiry_ts, row.side);
    if (key) {
      probabilityByMarketSide.set(key, row);
    }
  }

  const volatilityByAsset = new Map<string, RuntimeVolatilityRow>();
  for (const row of volatilityRows ?? []) {
    const asset = row.asset?.trim().toUpperCase();
    if (asset) {
      volatilityByAsset.set(asset, row);
    }
  }

  const grouped = new Map<string, MarketMonitorRow>();
  for (const orderbook of orderbooks ?? []) {
    if (!isRecord(orderbook)) {
      continue;
    }
    const asset = orderbook.asset?.trim().toUpperCase() || assetFromSlug(orderbook.market_slug);
    const key = orderbook.market_slug ?? `${asset}-${orderbook.expiry_ts ?? ""}`;
    if (!key) {
      continue;
    }
    const group =
      grouped.get(key) ??
      ({
        key,
        asset,
        marketSlug: orderbook.market_slug ?? key,
        expiryTs: orderbook.expiry_ts,
        threshold: orderbook.threshold_price,
        volatility: volatilityByAsset.get(asset),
      } satisfies MarketMonitorRow);
    group.expiryTs ||= orderbook.expiry_ts;
    group.threshold ??= orderbook.threshold_price;
    if (orderbook.side?.toUpperCase() === "UP") {
      group.up = orderbook;
    } else if (orderbook.side?.toUpperCase() === "DOWN") {
      group.down = orderbook;
    }
    grouped.set(key, group);
  }

  return [...grouped.values()].map((row) => ({
    ...row,
    upProbability: probabilityByMarketSide.get(
      marketSideKey(row.marketSlug, row.asset, row.expiryTs, "UP") ?? "",
    ),
    downProbability: probabilityByMarketSide.get(
      marketSideKey(row.marketSlug, row.asset, row.expiryTs, "DOWN") ?? "",
    ),
  }));
}

function rowKey(row: ProbabilityRow) {
  return row.output_id ?? row.contract_id ?? `${contractLabel(row)}-${row.asof_ts ?? ""}`;
}

function contractLabel(row: ProbabilityRow) {
  const assetSide = compactList([row.asset, row.side]);
  return (row.contract ?? assetSide) || "Unknown contract";
}

function probabilityEmptyBody(state: ApiState<ProbabilityPayload>) {
  if (state.status === "loading") {
    return "Waiting for the probability endpoint.";
  }
  if (state.error) {
    return `Endpoint unavailable: ${state.error}`;
  }
  if (state.payload?.state === "DISABLED") {
    return "Runtime probability generation is disabled. The browser stays read-only.";
  }
  return state.payload?.error ?? "The endpoint returned an empty row set.";
}

function cacheMetadataEntries(
  payload: ProbabilityPayload | null,
  row: ProbabilityRow,
): Array<[string, unknown]> {
  return [
    ["payload_cached", payload?.cached],
    ["payload_state", payload?.state],
    ["schema_version", payload?.schema_version],
    ["payload_model", payload?.model_version],
    ["payload_generated_at", payload?.generated_at],
    ["payload_skipped", payload?.skipped],
    ["payload_cache", payload?.cache],
    ["payload_cache_metadata", payload?.cache_metadata],
    ["payload_grid_cache", payload?.grid_cache],
    ["row_cache_status", row.cache_status],
    ["row_cache_key", row.cache_key],
    ["row_cache_market", row.cache_market_slug],
    ["row_cache_start", row.cache_start_ts],
    ["row_cache_expiry", row.cache_expiry_ts],
    ["row_cache_asof", row.cache_asof_ts],
    ["row_generated_at", row.generated_at],
    ["row_valid_from", row.valid_from],
    ["row_valid_until", row.valid_until],
    ["row_path_count", row.path_count],
    ["row_time_bucket", row.time_bucket],
    ["row_z_bucket", row.z_path_bucket],
    ["row_sigma_bucket", row.sigma_bucket],
    ["row_vol_regime", row.volatility_regime],
    ["row_cache_metadata", row.cache_metadata],
    ["row_grid_cache", row.grid_cache],
    ["generator_metadata", row.generator_metadata],
  ];
}

function parseSimulationPreview(value: unknown): SimulationPreview | null {
  if (!isRecord(value)) {
    return null;
  }
  const sampledPaths = Array.isArray(value.sampled_paths)
    ? value.sampled_paths.filter(isSimulationPath)
    : [];
  const histogram = Array.isArray(value.terminal_histogram)
    ? value.terminal_histogram.filter(isHistogramBucket)
    : [];
  const pathCount = value.path_count;
  const steps = value.steps;
  const startPrice = value.start_price;
  const threshold = value.threshold;
  const terminalWinCount = value.terminal_win_count;
  const noTouchWinCount = value.no_touch_win_count;
  const comparisonOperator = value.comparison_operator;
  if (
    !isFiniteNumber(pathCount) ||
    !isFiniteNumber(steps) ||
    !isFiniteNumber(startPrice) ||
    !isFiniteNumber(threshold) ||
    !isFiniteNumber(terminalWinCount) ||
    !isFiniteNumber(noTouchWinCount) ||
    typeof comparisonOperator !== "string"
  ) {
    return null;
  }
  return {
    path_count: pathCount,
    steps,
    start_price: startPrice,
    threshold,
    comparison_operator: comparisonOperator,
    terminal_win_count: terminalWinCount,
    no_touch_win_count: noTouchWinCount,
    sampled_paths: sampledPaths,
    terminal_histogram: histogram,
  };
}

function isSimulationPath(value: unknown): value is SimulationPath {
  return (
    isRecord(value) &&
    typeof value.index === "number" &&
    typeof value.terminal_win === "boolean" &&
    typeof value.no_touch_win === "boolean" &&
    Array.isArray(value.points) &&
    value.points.every(isFiniteNumber)
  );
}

function isHistogramBucket(value: unknown): value is HistogramBucket {
  return (
    isRecord(value) &&
    isFiniteNumber(value.lower) &&
    isFiniteNumber(value.upper) &&
    isFiniteNumber(value.count)
  );
}

function buildPathGeometry(preview: SimulationPreview) {
  const width = 704;
  const height = 246;
  const left = 28;
  const top = 28;
  const allPrices = [
    preview.threshold,
    preview.start_price,
    ...preview.sampled_paths.flatMap((path) => path.points),
  ];
  const low = Math.min(...allPrices);
  const high = Math.max(...allPrices);
  const padding = Math.max((high - low) * 0.12, 0.0001);
  const min = low - padding;
  const max = high + padding;
  const yFor = (price: number) => top + height - ((price - min) / (max - min)) * height;
  return {
    thresholdY: yFor(preview.threshold),
    startY: yFor(preview.start_price),
    paths: preview.sampled_paths.map((path) => {
      const maxIndex = Math.max(1, path.points.length - 1);
      const d = path.points
        .map((price, index) => {
          const x = left + (index / maxIndex) * width;
          const y = yFor(price);
          return `${index === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
        })
        .join(" ");
      return { d, index: path.index, terminalWin: path.terminal_win };
    }),
  };
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function unknownList(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function arrayLength(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

function compactList(values: Array<string | undefined>) {
  return values.filter(Boolean).join(" / ");
}

function marketSideKey(
  marketSlug?: string,
  asset?: string,
  expiryTs?: string,
  side?: string,
): string | null {
  const normalizedSide = side?.trim().toUpperCase();
  if (!normalizedSide) {
    return null;
  }
  if (marketSlug?.trim()) {
    return `${marketSlug.trim().toLowerCase()}|${normalizedSide}`;
  }
  const normalizedAsset = asset?.trim().toUpperCase();
  const normalizedExpiry = expiryTs?.trim();
  if (!normalizedAsset || !normalizedExpiry) {
    return null;
  }
  return `${normalizedAsset}|${normalizedExpiry}|${normalizedSide}`;
}

function assetFromSlug(value?: string) {
  return value?.split("-")[0]?.toUpperCase() || "UNKNOWN";
}

function shortSlug(value: string) {
  return value.replace("-updown-", " ");
}

function compactValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map(compactValue).filter(Boolean).join(", ");
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function sanitizeOperatorLabel(value: string) {
  return value.replaceAll("TRADE", "PAPER").replaceAll("ORDER", "PAPER");
}

function formatProbability(value?: number) {
  return isFiniteNumber(value) ? value.toFixed(3) : "-";
}

function formatSmall(value?: number) {
  return isFiniteNumber(value) ? value.toFixed(5) : "-";
}

function formatSigned(value?: number) {
  return isFiniteNumber(value) ? `${value >= 0 ? "+" : ""}${value.toFixed(3)}` : "-";
}

function formatOptional(value?: number | null) {
  return isFiniteNumber(value) ? value.toFixed(3) : "-";
}

function formatQuote(row?: RuntimeOrderbookRow) {
  if (!row) {
    return "-";
  }
  return `${formatPriceLevel(row.best_bid)}/${formatPriceLevel(row.best_ask)}`;
}

function formatPriceLevel(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value.toFixed(2);
  }
  if (typeof value === "string" && value.trim()) {
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(2) : value;
  }
  return "-";
}

function formatThreshold(value: unknown) {
  const number = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  if (!Number.isFinite(number)) {
    return "pending";
  }
  return formatPrice(number);
}

function formatEdge(row: ProbabilityRow) {
  if (!isFiniteNumber(row.edge_after_costs) || !isFiniteNumber(row.required_edge)) {
    return "-";
  }
  return `${row.edge_after_costs.toFixed(3)} / ${row.required_edge.toFixed(3)}`;
}

function formatAge(ageMs?: number) {
  if (!isFiniteNumber(ageMs)) {
    return "-";
  }
  if (ageMs >= 1000) {
    return `${(ageMs / 1000).toFixed(1)}s`;
  }
  return `${ageMs}ms`;
}

function formatCacheState(payload: ProbabilityPayload | null, rows: ProbabilityRow[]) {
  if (!payload) {
    return "pending";
  }
  if (payload.state === "DISABLED") {
    return "disabled";
  }
  const cacheRows = rows.filter((row) => row.cache_status || row.cache_key);
  if (cacheRows.length > 0) {
    const hits = cacheRows.filter((row) => row.cache_status === "HIT").length;
    const pathCount = cacheRows.find((row) => isFiniteNumber(row.path_count))?.path_count;
    const suffix = isFiniteNumber(pathCount) ? ` n=${formatInteger(pathCount)}` : "";
    return `${hits}/${rows.length} grid${suffix}`;
  }
  return payload.cached ? "api hit" : "no grid";
}

function formatRowCache(row: ProbabilityRow) {
  if (row.cache_status) {
    const pathCount = isFiniteNumber(row.path_count) ? ` n=${formatInteger(row.path_count)}` : "";
    return `${row.cache_status}${pathCount}`;
  }
  return row.output_id ? "persisted" : "-";
}

function formatLatency(latency?: JsonRecord) {
  const value = latency?.api_build_ms;
  return isFiniteNumber(value) ? `${value}ms` : "-";
}

function formatPreviewWinCounts(preview: SimulationPreview) {
  return `terminal ${formatInteger(preview.terminal_win_count)} / no-touch ${formatInteger(
    preview.no_touch_win_count,
  )}`;
}

function formatGate(value?: string | null) {
  if (!value) {
    return "PENDING";
  }
  return sanitizeOperatorLabel(value);
}

function gateTone(value: string) {
  if (value === "BLOCK") {
    return "block";
  }
  if (value === "WAIT" || value === "DEMAND_MORE_EDGE") {
    return "wait";
  }
  if (value.includes("CANDIDATE")) {
    return "candidate";
  }
  return "neutral";
}

function liveTone(ok: boolean | undefined, status: ApiState<RuntimeLivePayload>["status"]) {
  if (status === "error" || ok === false) {
    return "warn";
  }
  return ok === true ? "good" : "neutral";
}

function probabilityTone(payload: ProbabilityPayload | null) {
  if (!payload) {
    return "neutral";
  }
  if (payload.ok === false || payload.state === "DISABLED") {
    return "warn";
  }
  return "good";
}

function statusLabel(status: ApiState<unknown>["status"]) {
  if (status === "loading") {
    return "LOADING";
  }
  if (status === "error") {
    return "ERROR";
  }
  return "OK";
}

function shortWeightLabel(value: string) {
  if (value === "empirical_conditional") {
    return "empirical";
  }
  if (value === "lognormal_baseline") {
    return "lognormal";
  }
  if (value === "stress_overlay") {
    return "stress";
  }
  return value.replaceAll("_", " ");
}

function clamp01(value: number) {
  return Math.max(0, Math.min(1, value));
}

function formatTimestamp(value?: string) {
  if (!value) {
    return "pending";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function formatInteger(value?: number) {
  if (!isFiniteNumber(value)) {
    return "0";
  }
  return new Intl.NumberFormat("en-US").format(value);
}

function formatPrice(value: number) {
  if (Math.abs(value) >= 1000) {
    return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
  }
  return value.toFixed(2);
}

function shortSource(value: string) {
  return value.replace(/^polymarket_/, "");
}
