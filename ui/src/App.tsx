import { useEffect, useMemo, useRef, useState } from "react";
import {
  filterGraphableProbabilityRows,
  mergeProbabilityEventsIntoPayload,
  probabilityDisplayStatus,
  probabilityDisplayValue,
  probabilityKind,
  probabilityMetadata,
  probabilityRowKey,
  probabilitySelectionKey,
} from "./probabilityRows";

const LIVE_LIMIT = 12;
const PROBABILITY_LIMIT = 24;
const POLL_INTERVAL_MS = 2500;
const MC_REFRESH_HOLD_MS = 15000;

type JsonRecord = Record<string, unknown>;

type ApiState<T> = {
  status: "loading" | "ready" | "error";
  payload: T | null;
  error: string | null;
  notice: string | null;
  updatedAt: number | null;
  source?: "poll" | "stream";
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
  p_hat?: number;
  p_hat_std?: number;
  p_hat_ci_low?: number;
  p_hat_ci_high?: number;
  p_no_touch?: number;
  probability_kind?: string;
  mc_display_status?: string;
  z_path?: number;
  sigma_tau?: number;
  age_ms?: number;
  source_age_ms?: number | null;
  book_age_ms?: number | null;
  flags?: string[];
  model_version?: string;
  seed?: number | null;
  u_gen?: number | null;
  mc_dispersion?: number | null;
  uncertainty_buffer?: number | null;
  path_diagnosis?: string[];
  effective_weights?: Record<string, number>;
  decision_hint?: string | null;
  edge_after_costs?: number | null;
  required_edge?: number | null;
  path_risk_buffer?: number | null;
  gate_reasons?: string[];
  wave_score?: number | null;
  wave_phase?: string | null;
  wave_reasons?: string[];
  wave_markers?: string[];
  dynamic_edge?: number | null;
  dynamic_required_edge?: number | null;
  cache_key?: string;
  cache_status?: string;
  cache_market_slug?: string;
  cache_start_ts?: string;
  cache_expiry_ts?: string;
  cache_asof_ts?: string;
  generated_at?: string;
  valid_from?: string;
  valid_until?: string;
  refresh_display_until?: string;
  latency?: {
    runtime_ms?: number | null;
    total_lag_ms?: number | null;
  };
  time_bucket?: string;
  z_path_bucket?: string;
  sigma_bucket?: string;
  volatility_regime?: string;
  generator_version?: string;
  path_count?: number;
  paths_per_seed?: number;
  seed_count?: number;
  prior_sensitivity?: unknown[];
  generator_metadata?: JsonRecord;
  cache_metadata?: JsonRecord;
  grid_cache?: JsonRecord;
  simulation_preview?: unknown;
};

type PriorSensitivityRow = {
  dimension?: string;
  time_fraction?: number;
  quantile_low?: number;
  quantile_high?: number;
  sample_count?: number;
  price_quantile?: number;
  log_return_quantile?: number;
  p_hat?: number;
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
  last_good_rows?: ProbabilityRow[];
  nowcast_rows?: ProbabilityRow[];
  skipped?: number;
  errors?: unknown[];
  cache_metadata?: JsonRecord;
  grid_cache?: JsonRecord;
  cache?: JsonRecord;
};

type ProbabilityEventPayload = {
  schema_version?: string;
  ok?: boolean;
  state?: string;
  error?: string | null;
  generated_at?: string;
  events?: ProbabilityRow[];
  errors?: unknown[];
};

type MarketMonitorRow = {
  key: string;
  asset: string;
  marketSlug: string;
  startTs?: string;
  expiryTs?: string;
  threshold?: string | number | null;
  up?: RuntimeOrderbookRow;
  down?: RuntimeOrderbookRow;
  upProbability?: ProbabilityRow;
  downProbability?: ProbabilityRow;
  volatility?: RuntimeVolatilityRow;
};

type RuntimeLogEntry = {
  at: number;
  source: string;
  level: "info" | "warn";
  message: string;
  sequence: number;
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
  const requestSequenceRef = useRef(0);
  const marketRows = useMemo(
    () =>
      buildMarketMonitorRows(
        live.payload?.monitor?.orderbooks,
        rows,
        live.payload?.volatility?.rows,
        Date.now(),
      ),
    [live.payload?.monitor?.orderbooks, live.payload?.volatility?.rows, rows],
  );
  const selectedRow = selectProbabilityRow(rows, selectedId, Date.now());

  useEffect(() => {
    let active = true;

    async function poll() {
      const requestSequence = requestSequenceRef.current + 1;
      requestSequenceRef.current = requestSequence;
      const [liveResult, probabilityResult] = await Promise.all([
        fetchJson<RuntimeLivePayload>(`/api/runtime/live?limit=${LIVE_LIMIT}`),
        fetchJson<ProbabilityPayload>(
          `/api/runtime/probabilities?limit=${PROBABILITY_LIMIT}`,
        ),
      ]);
      if (!active || requestSequence !== requestSequenceRef.current) {
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
    if (typeof window.EventSource !== "function") {
      return undefined;
    }

    const stream = new window.EventSource(
      `/api/runtime/probability-events/stream?limit=${PROBABILITY_LIMIT}&interval_ms=100`,
    );
    const handleProbability = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as ProbabilityEventPayload;
        const events = Array.isArray(payload.events) ? payload.events : [];
        if (events.length === 0) {
          return;
        }
        setProbabilities((previous) => toProbabilityEventApiState(events, previous));
      } catch (error) {
        setProbabilities((previous) => ({
          ...previous,
          notice:
            error instanceof Error
              ? `Probability event stream parse error: ${error.message}`
              : "Probability event stream parse error.",
          updatedAt: Date.now(),
        }));
      }
    };
    const handleError = () => {
      setProbabilities((previous) => ({
        ...previous,
        notice: "Probability event stream disconnected; polling fallback is active.",
        updatedAt: Date.now(),
      }));
    };
    stream.addEventListener("probability", handleProbability as EventListener);
    stream.addEventListener("error", handleError);
    return () => {
      stream.removeEventListener("probability", handleProbability as EventListener);
      stream.removeEventListener("error", handleError);
      stream.close();
    };
  }, []);

  useEffect(() => {
    if (rows.length === 0) {
      if (selectedId !== null) {
        setSelectedId(null);
      }
      return;
    }
    const preferredRow = selectProbabilityRow(rows, selectedId, Date.now());
    const preferredKey = preferredRow ? selectionKey(preferredRow) : null;
    if (preferredKey !== selectedId) {
      setSelectedId(preferredKey);
    }
  }, [rows, selectedId]);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>Runtime Monitor</h1>
          <p>Live status, Monte Carlo outputs, decision checks, and cache health.</p>
        </div>
        <CompactLiveHealth live={live} probabilities={probabilities} rows={rows} />
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
            selectedProbabilityKey={selectedRow ? selectionKey(selectedRow) : null}
            onSelectProbability={setSelectedId}
          />
          <SelectedDetails
            row={selectedRow}
            probabilities={probabilities.payload}
            marketRows={marketRows}
            onSelectProbability={setSelectedId}
          />
          <CompactVolatility volatility={live.payload?.volatility ?? null} />
          <ProbabilityTable
            rows={rows}
            selectedKey={selectedRow ? selectionKey(selectedRow) : null}
            onSelect={setSelectedId}
            state={probabilities}
          />
          <MonteCarloInputsPanel
            row={selectedRow}
            probabilities={probabilities}
            marketRows={marketRows}
          />
          <RuntimeLogPanel live={live} probabilities={probabilities} selectedRow={selectedRow} />
        </section>
      </section>
    </main>
  );
}

function CompactLiveHealth({
  live,
  probabilities,
  rows,
}: {
  live: ApiState<RuntimeLivePayload>;
  probabilities: ApiState<ProbabilityPayload>;
  rows: ProbabilityRow[];
}) {
  const payload = live.payload;
  const orderbookCount = arrayLength(payload?.monitor?.orderbooks);
  const nowcastCount = safeNowcastRows(probabilities.payload).length;
  const latestMetadata = latestProbabilityMetadata(rows);
  return (
    <div className="topbar-health" aria-label="Compact live health">
      <span>{payload?.status?.state ?? statusLabel(live.status)}</span>
      <span>{formatInteger(orderbookCount)} books</span>
      <span>{probabilities.payload?.state ?? statusLabel(probabilities.status)}</span>
      <span>{formatInteger(rows.length)} MC</span>
      <span>{formatInteger(nowcastCount)} NOW</span>
      <span>{formatInteger(latestMetadata?.totalPaths)} paths</span>
      <span>{formatAge(latestMetadata?.runtimeMs)}</span>
      <span>{probabilities.source ?? "poll"}</span>
    </div>
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
  const nowcastCount = safeNowcastRows(probabilityPayload).length;
  const latestMetadata = latestProbabilityMetadata(rows);
  return (
    <section className="status-strip" aria-label="Runtime status">
      <Metric label="Live state" value={liveState} tone={liveTone(livePayload?.ok, live.status)} />
      <Metric
        label="MC state"
        value={probabilityPayload?.state ?? statusLabel(probabilities.status)}
        tone={probabilityTone(probabilityPayload)}
      />
      <Metric label="MC rows" value={formatInteger(rowCount)} />
      <Metric label="NOWCAST rows" value={formatInteger(nowcastCount)} />
      <Metric label="CUDA paths" value={formatInteger(latestMetadata?.totalPaths)} />
      <Metric label="GPU runtime" value={formatAge(latestMetadata?.runtimeMs)} />
      <Metric label="Total lag" value={formatAge(latestMetadata?.totalLagMs)} />
      <Metric label="Source" value={probabilities.source ?? "poll"} />
      <Metric label="Generated" value={formatTimestamp(generatedAt)} />
      <Metric label="Cache" value={formatCacheState(probabilityPayload, rows)} />
      {live.error ? <div className="status-note">Live API: {live.error}</div> : null}
      {probabilities.error ? (
        <div className="status-note">Monte Carlo API: {probabilities.error}</div>
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
        subtitle="Orderbook, target, Monte Carlo, and volatility context by market."
      />
      {rows.length === 0 ? (
        <EmptyState title="No markets" body="Waiting for live orderbooks." />
      ) : (
        <div className="market-table" role="table" aria-label="Market Monte Carlo outputs">
          <div className="market-row market-head" role="row">
            <span>Market</span>
            <span>Expiry</span>
            <span>K</span>
            <span>UP bid/ask</span>
            <span>UP MC</span>
            <span>DOWN bid/ask</span>
            <span>DOWN MC</span>
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
  const key = selectionKey(row);
  const metadata = probabilityMetadata(row);
  return (
    <button
      className={
        key === selectedKey ? "probability-chip selected-probability" : "probability-chip"
      }
      onClick={() => onSelect(key)}
      type="button"
    >
      <strong>{formatProbability(probabilityDisplayValue(row))}</strong>
      <small>{compactList([formatLaneStatus(row), formatNoTouch(row)])}</small>
      <small>
        {compactList([formatCompactPathCount(metadata.totalPaths), formatAgeIfPresent(metadata.totalLagMs)])}
      </small>
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
        title="Monte Carlo Diagnostics"
        subtitle="Cached-grid Monte Carlo rows from /api/runtime/probabilities."
      />
      {rows.length === 0 ? (
        <EmptyState
          title="No Monte Carlo rows"
          body={probabilityEmptyBody(state)}
        />
      ) : (
        <div className="probability-table" role="table" aria-label="Monte Carlo outputs">
          <div className="probability-row table-head" role="row">
            <span>Contract</span>
            <span>Monte Carlo</span>
            <span>Decision check</span>
            <span>Wave</span>
            <span>Cache</span>
            <span>Age</span>
          </div>
          {rows.map((row) => {
            const key = rowKey(row);
            const selectedRowKey = selectionKey(row);
            return (
              <button
                className={
                  selectedRowKey === selectedKey
                    ? "probability-row selected-row"
                    : "probability-row"
                }
                key={key}
                onClick={() => onSelect(selectedRowKey)}
                type="button"
                role="row"
              >
                <span className="contract-cell">
                  <strong>{contractLabel(row)}</strong>
                  <small>{compactList([row.asset, row.side, row.contract_id])}</small>
                </span>
                <span className="probability-value-cell">
                  <strong>{formatProbability(probabilityDisplayValue(row))}</strong>
                  <small>{compactList([formatLaneStatus(row), formatNoTouch(row)])}</small>
                </span>
                <span>
                  <GatePill value={row.decision_hint} />
                </span>
                <span>
                  <WaveBadge row={row} />
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
  marketRows,
  onSelectProbability,
}: {
  row: ProbabilityRow | null;
  probabilities: ProbabilityPayload | null;
  marketRows: MarketMonitorRow[];
  onSelectProbability: (key: string) => void;
}) {
  if (!row) {
    return (
      <section className="panel detail-panel">
        <PanelHeader title="Selected Contract" subtitle="Waiting for a Monte Carlo row." />
        <EmptyState title="Nothing selected" body="The details panel appears when rows arrive." />
      </section>
    );
  }

  const preview = parseSimulationPreview(row.simulation_preview);
  const pairRows = currentMarketRows(marketRows, Date.now());
  const selectedMarketRow = marketRowForProbability(row, marketRows);
  const timingLabel = contractTimingLabel(row, Date.now());
  const metadata = probabilityMetadata(row);
  return (
    <section className="panel detail-panel">
      <div className="simulation-head">
        <div>
          <p className="panel-kicker">Selected contract</p>
          <h2>{contractLabel(row)}</h2>
          <p>{selectedContractSubtitle(row, timingLabel)}</p>
        </div>
        <GatePill value={row.decision_hint} />
      </div>

      <div className="hero-metrics">
        <Metric label="MC p_finish" value={formatProbability(probabilityDisplayValue(row))} />
        <Metric label="p no touch" value={formatProbability(row.p_no_touch)} />
        <Metric label={timingLabel} value={formatTimestamp(row.expiry_ts)} />
        <Metric label="edge / required" value={formatEdge(row)} />
        <Metric label="Total CUDA paths" value={formatInteger(metadata.totalPaths)} />
        <Metric label="GPU runtime" value={formatAge(metadata.runtimeMs)} />
        <Metric label="Total lag" value={formatAge(metadata.totalLagMs)} />
        <Metric label="Lane" value={formatLaneStatus(row)} />
        <Metric label="dynamic edge" value={formatDynamicEdge(row)} />
        <Metric label="wave" value={formatWave(row)} />
      </div>

      <ContractPairSelector
        rows={pairRows}
        selectedKey={selectionKey(row)}
        onSelectProbability={onSelectProbability}
      />

      <MonteCarloComparisonGrid
        fallbackRow={row}
        marketRow={selectedMarketRow}
        onSelectProbability={onSelectProbability}
        selectedKey={selectionKey(row)}
      />

      <div className="simulation-footer">
        <Metric label="as-of" value={formatTimestamp(row.asof_ts)} />
        <Metric label="expiry" value={formatTimestamp(row.expiry_ts)} />
        <Metric label="model" value={row.model_version ?? probabilities?.model_version ?? "-"} />
        <Metric label="skipped" value={formatInteger(probabilities?.skipped)} />
      </div>
    </section>
  );
}

function ContractPairSelector({
  rows,
  selectedKey,
  onSelectProbability,
}: {
  rows: MarketMonitorRow[];
  selectedKey: string;
  onSelectProbability: (key: string) => void;
}) {
  if (rows.length === 0) {
    return null;
  }
  return (
    <section className="pair-selector" aria-label="Current UP / DOWN comparison">
      <div className="pair-selector-heading">
        <h3>Current UP / DOWN</h3>
        <span>Click a side to focus details</span>
      </div>
      <div className="pair-selector-grid">
        {rows.map((marketRow) => (
          <div className="pair-card" key={marketRow.key}>
            <div>
              <strong>{marketRow.asset}</strong>
              <span>
                {capitalizeLabel(marketTimingLabel(marketRow, Date.now()))} / Expires{" "}
                {formatTimestamp(marketRow.expiryTs)}
              </span>
            </div>
            <div className="pair-actions">
              <PairProbabilityButton
                label="UP"
                row={marketRow.upProbability}
                quote={formatQuote(marketRow.up)}
                selectedKey={selectedKey}
                onSelect={onSelectProbability}
              />
              <PairProbabilityButton
                label="DOWN"
                row={marketRow.downProbability}
                quote={formatQuote(marketRow.down)}
                selectedKey={selectedKey}
                onSelect={onSelectProbability}
              />
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function PairProbabilityButton({
  label,
  row,
  quote,
  selectedKey,
  onSelect,
}: {
  label: string;
  row?: ProbabilityRow;
  quote: string;
  selectedKey: string;
  onSelect: (key: string) => void;
}) {
  if (!row) {
    return (
      <span className="pair-button pair-empty">
        <strong>{label}</strong>
        <span>pending</span>
      </span>
    );
  }
  const key = selectionKey(row);
  return (
    <button
      className={key === selectedKey ? "pair-button pair-selected" : "pair-button"}
      onClick={() => onSelect(key)}
      type="button"
    >
      <strong>{label}</strong>
      <span>{formatProbability(probabilityDisplayValue(row))}</span>
      <small>{compactList([formatNoTouch(row), quote])}</small>
    </button>
  );
}

function MonteCarloComparisonGrid({
  fallbackRow,
  marketRow,
  selectedKey,
  onSelectProbability,
}: {
  fallbackRow: ProbabilityRow;
  marketRow?: MarketMonitorRow;
  selectedKey: string;
  onSelectProbability: (key: string) => void;
}) {
  const upValue = normalizedProbability(probabilityDisplayValue(marketRow?.upProbability));
  const downValue = normalizedProbability(probabilityDisplayValue(marketRow?.downProbability));
  const leader =
    upValue === null || downValue === null
      ? null
      : upValue > downValue
        ? "UP"
        : downValue > upValue
          ? "DOWN"
          : null;
  const sides = marketRow
    ? [
        { label: "UP", row: marketRow.upProbability, quote: formatQuote(marketRow.up) },
        { label: "DOWN", row: marketRow.downProbability, quote: formatQuote(marketRow.down) },
      ]
    : [{ label: fallbackRow.side ?? "Selected", row: fallbackRow, quote: "-" }];

  return (
    <section className="comparison-grid" aria-label="UP and DOWN Monte Carlo comparison">
      {sides.map((side) => (
        <MonteCarloComparisonCard
          key={side.label}
          label={side.label}
          quote={side.quote}
          row={side.row}
          selectedKey={selectedKey}
          isLeader={leader === side.label}
          onSelectProbability={onSelectProbability}
        />
      ))}
    </section>
  );
}

function MonteCarloComparisonCard({
  label,
  row,
  quote,
  selectedKey,
  isLeader,
  onSelectProbability,
}: {
  label: string;
  row?: ProbabilityRow;
  quote: string;
  selectedKey: string;
  isLeader: boolean;
  onSelectProbability: (key: string) => void;
}) {
  if (!row) {
    return (
      <section className="comparison-card">
        <div className="comparison-card-head">
          <div>
            <h3>{label} Monte Carlo</h3>
            <span>Waiting for row</span>
          </div>
        </div>
        <EmptyState title={`${label} pending`} body="Waiting for the matching Monte Carlo row." />
      </section>
    );
  }
  const key = selectionKey(row);
  const preview = parseSimulationPreview(row.simulation_preview);
  const metadata = probabilityMetadata(row);
  const className = compactList([
    "comparison-card",
    key === selectedKey ? "selected-comparison" : undefined,
    isLeader ? "leader-comparison" : undefined,
  ]);
  return (
    <section className={className}>
      <button
        className="comparison-card-head"
        onClick={() => onSelectProbability(key)}
        type="button"
      >
        <div>
          <h3>{label} Monte Carlo</h3>
          <span>
            {compactList([
              `p_finish ${formatProbability(probabilityDisplayValue(row))}`,
              formatNoTouch(row),
              quote,
              isLeader ? "leading" : undefined,
            ])}
          </span>
        </div>
        <GatePill value={row.decision_hint} />
      </button>
      <MonteCarloCanvas preview={preview} row={row} />
      <div className="comparison-stats">
        <Metric label="status" value={formatLaneStatus(row)} />
        <Metric label="CUDA paths" value={formatInteger(metadata.totalPaths)} />
        <Metric label="runtime / lag" value={formatRuntimeAndLag(metadata)} />
      </div>
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

function MonteCarloInputsPanel({
  row,
  probabilities,
  marketRows,
}: {
  row: ProbabilityRow | null;
  probabilities: ApiState<ProbabilityPayload>;
  marketRows: MarketMonitorRow[];
}) {
  if (!row) {
    return (
      <section className="panel mc-input-panel">
        <PanelHeader
          title="Monte Carlo Conditioning + CUDA Cache"
          subtitle="Prior-driven state, cache identity, and GPU path-count targets."
        />
        <EmptyState title="No selected row" body="Select a Monte Carlo row to inspect inputs." />
      </section>
    );
  }

  const preview = parseSimulationPreview(row.simulation_preview);
  const metadata = probabilityMetadata(row);
  const marketRow = marketRowForProbability(row, marketRows);
  const selectedBook = orderbookForSide(row, marketRow);
  return (
    <section className="panel mc-input-panel">
      <PanelHeader
        title="Monte Carlo Conditioning + CUDA Cache"
        subtitle="Prior-driven state, cache identity, and GPU path-count targets."
      />
      <div className="mc-metric-grid">
        <Metric label="UI updated" value={formatLocalTimestamp(probabilities.updatedAt)} />
        <Metric label="row generated" value={formatTimestamp(row.generated_at)} />
        <Metric label="valid until" value={formatTimestamp(row.valid_until)} />
        <Metric label="lane" value={formatLaneStatus(row)} />
        <Metric label="cache" value={formatRowCache(row)} />
        <Metric label="Total CUDA paths" value={formatInteger(metadata.totalPaths)} />
        <Metric label="Paths / seed" value={formatInteger(metadata.pathsPerSeed)} />
        <Metric label="Seeds" value={formatInteger(metadata.seedCount)} />
        <Metric label="Preview paths" value={formatInteger(metadata.previewPathCount)} />
        <Metric label="GPU runtime" value={formatAge(metadata.runtimeMs)} />
        <Metric label="Total lag" value={formatAge(metadata.totalLagMs)} />
        <Metric label="UI source" value={probabilities.source ?? "poll"} />
      </div>
      <div className="mc-input-grid">
        <section className="mc-input-section">
          <h3>Contract State</h3>
          <KeyValueList
            entries={[
              ["asset", row.asset],
              ["side", row.side],
              ["expires", formatTimestamp(row.expiry_ts)],
              ["threshold_k", formatThreshold(marketRow?.threshold ?? preview?.threshold)],
              ["settlement_start", preview?.start_price ? formatPrice(preview.start_price) : undefined],
              ["comparison", preview?.comparison_operator],
              ["p_finish", formatProbability(probabilityDisplayValue(row))],
              ["p_no_touch", formatProbability(row.p_no_touch)],
              ["z_path", formatSigned(row.z_path)],
              ["sigma_tau", formatSmall(row.sigma_tau)],
            ]}
          />
          <PriorSensitivityGrid row={row} />
        </section>
        <section className="mc-input-section">
          <h3>Market Data</h3>
          <KeyValueList
            entries={[
              ["up_bid_ask", formatQuote(marketRow?.up)],
              ["down_bid_ask", formatQuote(marketRow?.down)],
              ["selected_quote", formatQuote(selectedBook)],
              ["book_event_ts", selectedBook?.event_ts],
              ["book_observed_ts", selectedBook?.observed_ts],
              ["source_age", formatAge(row.source_age_ms ?? undefined)],
              ["book_age", formatAge(row.book_age_ms ?? undefined)],
              ["row_age", formatAge(row.age_ms)],
              ["flags", compactList(row.flags)],
            ]}
          />
        </section>
        <section className="mc-input-section">
          <h3>Grid Dimensions</h3>
          <KeyValueList
            entries={[
              ["model", row.model_version ?? probabilities.payload?.model_version],
              ["generator", row.generator_version],
              ["cache_status", row.cache_status],
              ["display_status", probabilityDisplayStatus(row)],
              ["seed", row.seed],
              ["time_bucket", row.time_bucket],
              ["z_bucket", row.z_path_bucket],
              ["sigma_bucket", row.sigma_bucket],
              ["vol_regime", row.volatility_regime],
              ["u_gen", row.u_gen],
            ]}
          />
        </section>
        <section className="mc-input-section">
          <h3>Path Preview</h3>
          <KeyValueList
            entries={[
              ["preview_paths", metadata.previewPathCount],
              ["steps", preview?.steps],
              ["wins", preview?.terminal_win_count],
              ["no_touch_wins", preview?.no_touch_win_count],
              ["mc_dispersion", row.mc_dispersion],
              ["uncertainty", row.uncertainty_buffer],
              ["decision_check", formatGate(row.decision_hint)],
              ["path_risk_buffer", row.path_risk_buffer],
              ["wave", formatWave(row)],
              ["dynamic_edge", formatDynamicEdge(row)],
              ["decision_reasons", compactList(row.gate_reasons)],
              ["wave_reasons", compactList(row.wave_reasons)],
            ]}
          />
        </section>
      </div>
      <section className="mc-input-section cache-key-section">
        <h3>Cache Key</h3>
        <KeyValueList
          entries={[
            ["cache_key", row.cache_key],
            ["cache_asof", row.cache_asof_ts],
            ["cache_start", row.cache_start_ts],
            ["cache_expiry", row.cache_expiry_ts],
            ["valid_from", row.valid_from],
            ["refresh_display_until", row.refresh_display_until],
          ]}
        />
      </section>
    </section>
  );
}

function PriorSensitivityGrid({ row }: { row: ProbabilityRow }) {
  const rows = parsePriorSensitivity(row.prior_sensitivity).slice(0, 6);
  if (rows.length === 0) {
    return (
      <div className="sensitivity-section">
        <h3>Prior Sensitivity</h3>
        <p className="quiet">Waiting for prior-derived sensitivity rows.</p>
      </div>
    );
  }
  return (
    <div className="sensitivity-section">
      <h3>Prior Sensitivity</h3>
      <div className="sensitivity-grid">
        {rows.map((item, index) => (
          <div className="sensitivity-row" key={`${item.time_fraction}-${item.quantile_low}-${index}`}>
            <span>Prior quantile {formatQuantileBand(item)}</span>
            <strong>{formatProbability(item.p_hat)}</strong>
            <small>
              {compactList([
                formatTimeFraction(item.time_fraction),
                item.sample_count !== undefined ? `n=${formatInteger(item.sample_count)}` : undefined,
                item.price_quantile !== undefined ? `price=${formatPrice(item.price_quantile)}` : undefined,
              ])}
            </small>
          </div>
        ))}
      </div>
    </div>
  );
}

function parsePriorSensitivity(value: unknown): PriorSensitivityRow[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter(isRecord).map((row) => ({
    dimension: typeof row.dimension === "string" ? row.dimension : undefined,
    time_fraction: numberOrUndefined(row.time_fraction),
    quantile_low: numberOrUndefined(row.quantile_low),
    quantile_high: numberOrUndefined(row.quantile_high),
    sample_count: numberOrUndefined(row.sample_count),
    price_quantile: numberOrUndefined(row.price_quantile),
    log_return_quantile: numberOrUndefined(row.log_return_quantile),
    p_hat: numberOrUndefined(row.p_hat),
  }));
}

function numberOrUndefined(value: unknown) {
  return isFiniteNumber(value) ? value : undefined;
}

function formatQuantileBand(row: PriorSensitivityRow) {
  if (row.quantile_low === undefined || row.quantile_high === undefined) {
    return "-";
  }
  return `${Math.round(row.quantile_low * 100)}-${Math.round(row.quantile_high * 100)}%`;
}

function formatTimeFraction(value?: number) {
  return value === undefined ? undefined : `t=${Math.round(value * 100)}%`;
}

function RuntimeLogPanel({
  live,
  probabilities,
  selectedRow,
}: {
  live: ApiState<RuntimeLivePayload>;
  probabilities: ApiState<ProbabilityPayload>;
  selectedRow: ProbabilityRow | null;
}) {
  const entries = buildRuntimeLogEntries(live, probabilities, selectedRow);
  return (
    <section className="panel runtime-log-panel">
      <PanelHeader title="Runtime Log" subtitle="Oldest at top; newest at bottom." />
      {entries.length === 0 ? (
        <EmptyState title="No log entries" body="Runtime diagnostics will append downward." />
      ) : (
        <div className="runtime-log-list" aria-label="Runtime log entries ordered top to bottom">
          {entries.map((entry) => (
            <div className={`runtime-log-row runtime-log-${entry.level}`} key={entry.sequence}>
              <span>{formatLogTimestamp(entry.at)}</span>
              <strong>{entry.source}</strong>
              <p>{sanitizeOperatorLabel(entry.message)}</p>
            </div>
          ))}
        </div>
      )}
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
    return <ProbabilityFallbackChart row={row} />;
  }

  return (
    <div className="path-chart">
      <div className="chart-labels">
        <span>Threshold {formatPrice(preview.threshold)}</span>
        <span>{formatInteger(preview.sampled_paths.length)} preview paths</span>
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

function ProbabilityFallbackChart({ row }: { row: ProbabilityRow }) {
  const finishProbability = normalizedProbability(probabilityDisplayValue(row));
  const bars = [
    {
      label: "Monte Carlo",
      value: finishProbability,
      className: "fallback-finish",
    },
  ];
  return (
    <div className="path-chart fallback-chart">
      <div className="chart-labels">
        <span>Cached Monte Carlo snapshot</span>
        <span>{formatTimestamp(row.asof_ts)}</span>
      </div>
      <svg viewBox="0 0 760 310" role="img" aria-label="Cached Monte Carlo fallback chart">
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => {
          const x = 150 + tick * 540;
          return (
            <g key={tick}>
              <line className="fallback-tick" x1={x} x2={x} y1="58" y2="232" />
              <text className="axis-label" x={x - 10} y="258">
                {tick.toFixed(2)}
              </text>
            </g>
          );
        })}
        {bars.map((bar, index) => {
          const y = 82 + index * 82;
          const width = bar.value === null ? 0 : bar.value * 540;
          return (
            <g key={bar.label}>
              <text className="fallback-label" x="28" y={y + 22}>
                {bar.label}
              </text>
              <rect className="fallback-track" x="150" y={y} width="540" height="34" rx="6" />
              <rect
                className={`fallback-bar ${bar.className}`}
                x="150"
                y={y}
                width={width}
                height="34"
                rx="6"
              />
              <text className="fallback-value" x="704" y={y + 22}>
                {formatProbability(bar.value ?? undefined)}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="chart-caption">
        <span>Sampled path preview not attached in this poll</span>
        <span>z {formatSigned(row.z_path)}</span>
      </div>
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

function GatePill({ value }: { value?: string | null }) {
  const label = formatGate(value);
  return <span className={`gate-pill gate-${gateTone(label)}`}>{label}</span>;
}

function WaveBadge({ row }: { row: ProbabilityRow }) {
  const phase = cleanString(row.wave_phase) ?? "none";
  const score = isFiniteNumber(row.wave_score) ? row.wave_score.toFixed(2) : "--";
  const markers = unknownList(row.wave_markers).map(compactValue).filter(Boolean).join("/");
  return (
    <span className={`wave-badge wave-${phaseTone(phase)}`}>
      <span>{phase}</span>
      <strong>{score}</strong>
      {markers ? <small>{markers}</small> : null}
    </span>
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
    source: "poll",
  };
}

export function toProbabilityApiState(
  result: { payload: ProbabilityPayload | null; error: string | null },
  previous: ApiState<ProbabilityPayload>,
): ApiState<ProbabilityPayload> {
  const next = toApiState({
    ...result,
    payload: materializeLastGoodProbabilityRows(result.payload),
  });
  if (result.error || !next.payload) {
    return next;
  }
  return retainPreviousProbabilityRows(next, previous);
}

export function toProbabilityEventApiState(
  events: ProbabilityRow[],
  previous: ApiState<ProbabilityPayload>,
): ApiState<ProbabilityPayload> {
  const next: ApiState<ProbabilityPayload> = {
    status: "ready",
    payload: mergeProbabilityEventsIntoPayload(previous.payload, events),
    error: null,
    notice: null,
    updatedAt: Date.now(),
    source: "stream",
  };
  return retainPreviousProbabilityRows(next, previous);
}

function retainPreviousProbabilityRows(
  next: ApiState<ProbabilityPayload>,
  previous: ApiState<ProbabilityPayload>,
): ApiState<ProbabilityPayload> {
  const previousRows = safeRows(previous.payload);
  const nextRows = safeRows(next.payload);
  const previousRawRows = Array.isArray(previous.payload?.rows) ? previous.payload.rows : [];
  const nowMs = Date.now();
  const holdUntilMs = (previous.updatedAt ?? nowMs) + MC_REFRESH_HOLD_MS;
  if (
    next.payload &&
    previousRawRows.length > 0 &&
    nextRows.length === 0 &&
    holdUntilMs > nowMs
  ) {
    return {
      ...next,
      payload: markProbabilityRowsHeldForRefresh(previous.payload, holdUntilMs),
      notice: "Monte Carlo refresh pending; keeping last populated grid.",
      updatedAt: previous.updatedAt,
    };
  }
  if (next.payload && previousRows.length > 0 && nextRows.length > 0) {
    return {
      ...next,
      payload: mergeProbabilityPreviews(previous.payload, next.payload),
    };
  }
  return next;
}

function materializeLastGoodProbabilityRows(
  payload: ProbabilityPayload | null,
): ProbabilityPayload | null {
  if (!payload || (Array.isArray(payload.rows) && payload.rows.length > 0)) {
    return payload;
  }
  const lastGoodRows = Array.isArray(payload.last_good_rows) ? payload.last_good_rows : [];
  if (lastGoodRows.length === 0) {
    return payload;
  }
  return {
    ...payload,
    rows: lastGoodRows.map((row) => ({
      ...row,
      mc_display_status: "held",
    })),
  };
}

function markProbabilityRowsHeldForRefresh(
  payload: ProbabilityPayload | null,
  holdUntilMs: number,
): ProbabilityPayload | null {
  if (!payload || !Array.isArray(payload.rows)) {
    return payload;
  }
  const refreshDisplayUntil = new Date(holdUntilMs).toISOString();
  return {
    ...payload,
    rows: payload.rows.map((row) => ({
      ...row,
      refresh_display_until: refreshDisplayUntil,
    })),
  };
}

function safeRows(payload: ProbabilityPayload | null, nowMs = Date.now()): ProbabilityRow[] {
  return filterGraphableProbabilityRows(payload, nowMs);
}

function safeNowcastRows(payload: ProbabilityPayload | null, nowMs = Date.now()): ProbabilityRow[] {
  if (!Array.isArray(payload?.nowcast_rows)) {
    return [];
  }
  return filterGraphableProbabilityRows({ rows: payload.nowcast_rows }, nowMs);
}

function latestProbabilityMetadata(rows: ProbabilityRow[]) {
  const row = rows.find((candidate) => isFiniteNumber(candidate.path_count)) ?? rows[0];
  return row ? probabilityMetadata(row) : null;
}

function selectProbabilityRow(
  rows: ProbabilityRow[],
  selectedId: string | null,
  nowMs: number,
) {
  if (rows.length === 0) {
    return null;
  }
  const selectedRow = selectedId
    ? rows.find((row) => selectionKey(row) === selectedId) ?? null
    : null;
  const preferredPool = selectedRow?.asset
    ? rows.filter((row) => normalizeAsset(row.asset) === normalizeAsset(selectedRow.asset))
    : rows;
  const globalPreferredRow = preferredProbabilityRow(rows, nowMs);
  const preferredRow =
    preferredProbabilityRow(preferredPool, nowMs) ?? globalPreferredRow;
  if (!selectedRow) {
    return globalPreferredRow;
  }
  if (!preferredRow) {
    return selectedRow;
  }
  const selectedRank = contractTimingRank(selectedRow, nowMs);
  const globalPreferredRank = globalPreferredRow
    ? contractTimingRank(globalPreferredRow, nowMs)
    : Number.POSITIVE_INFINITY;
  if (globalPreferredRow && globalPreferredRank < selectedRank) {
    return globalPreferredRow;
  }
  const preferredRank = contractTimingRank(preferredRow, nowMs);
  if (selectedRank <= preferredRank && selectedRank <= 1) {
    return selectedRow;
  }
  return preferredRow;
}

function preferredProbabilityRow(rows: ProbabilityRow[], nowMs: number) {
  return [...rows].sort((left, right) => compareProbabilityPreference(left, right, nowMs))[0] ?? null;
}

function mergeProbabilityPreviews(
  previous: ProbabilityPayload | null,
  next: ProbabilityPayload,
): ProbabilityPayload {
  const previousRowsByKey = new Map<string, ProbabilityRow>();
  for (const row of safeRows(previous)) {
    if (row.simulation_preview) {
      for (const key of probabilityPreviewKeys(row)) {
        previousRowsByKey.set(key, row);
      }
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
      const previousRow = probabilityPreviewKeys(row)
        .map((key) => previousRowsByKey.get(key))
        .find((candidate) => candidate?.simulation_preview);
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

function probabilityPreviewKeys(row: ProbabilityRow) {
  return [
    currentNextIdentityKey(row),
    row.contract_id ? `contract|${row.contract_id}|${normalizeSide(row.side)}` : null,
    row.output_id ? `output|${row.output_id}` : null,
    marketSideKey(row.market_slug, row.asset, row.expiry_ts, row.side, row.start_ts),
    `${contractLabel(row)}|${row.start_ts ?? ""}|${row.expiry_ts ?? ""}|${normalizeSide(
      row.side,
    )}`,
  ].filter((key): key is string => Boolean(key));
}

function currentNextIdentityKey(row: ProbabilityRow) {
  const asset = normalizeAsset(row.asset);
  const side = normalizeSide(row.side);
  const startTs = row.start_ts ?? row.cache_start_ts;
  const expiryTs = row.expiry_ts ?? row.cache_expiry_ts;
  if (!asset || !side || !expiryTs) {
    return null;
  }
  return `window|${asset}|${side}|${startTs ?? ""}|${expiryTs}`;
}

export function buildMarketMonitorRows(
  orderbooks: RuntimeOrderbookRow[] | undefined,
  probabilities: ProbabilityRow[],
  volatilityRows: RuntimeVolatilityRow[] | undefined,
  nowMs: number,
): MarketMonitorRow[] {
  const volatilityByAsset = new Map<string, RuntimeVolatilityRow>();
  for (const row of volatilityRows ?? []) {
    const asset = normalizeAsset(row.asset);
    if (asset) {
      volatilityByAsset.set(asset, row);
    }
  }

  const grouped = new Map<string, MarketMonitorRow>();
  for (const orderbook of orderbooks ?? []) {
    if (!isRecord(orderbook)) {
      continue;
    }
    const asset = normalizeAsset(orderbook.asset) || assetFromSlug(orderbook.market_slug);
    const key = marketGroupKey(
      orderbook.market_slug,
      asset,
      orderbook.expiry_ts,
      orderbook.start_ts,
    );
    if (!key) {
      continue;
    }
    const group =
      grouped.get(key) ??
      ({
        key,
        asset,
        marketSlug: orderbook.market_slug ?? key,
        startTs: orderbook.start_ts,
        expiryTs: orderbook.expiry_ts,
        threshold: orderbook.threshold_price,
        volatility: volatilityByAsset.get(asset),
      } satisfies MarketMonitorRow);
    group.startTs ||= orderbook.start_ts;
    group.expiryTs ||= orderbook.expiry_ts;
    group.threshold ??= orderbook.threshold_price;
    if (normalizeSide(orderbook.side) === "UP") {
      group.up = orderbook;
    } else if (normalizeSide(orderbook.side) === "DOWN") {
      group.down = orderbook;
    }
    grouped.set(key, group);
  }

  for (const probability of probabilities) {
    const asset = normalizeAsset(probability.asset) || assetFromSlug(probability.market_slug);
    const key = marketGroupKey(
      probability.market_slug,
      asset,
      probability.expiry_ts,
      probability.start_ts,
    );
    if (!key) {
      continue;
    }
    const group =
      grouped.get(key) ??
      ({
        key,
        asset,
        marketSlug: probability.market_slug ?? key,
        startTs: probability.start_ts,
        expiryTs: probability.expiry_ts,
        volatility: volatilityByAsset.get(asset),
      } satisfies MarketMonitorRow);
    group.startTs ||= probability.start_ts;
    group.expiryTs ||= probability.expiry_ts;
    group.volatility ||= volatilityByAsset.get(asset);
    const side = normalizeSide(probability.side);
    if (side === "UP") {
      group.upProbability = preferProbabilitySide(group.upProbability, probability);
    } else if (side === "DOWN") {
      group.downProbability = preferProbabilitySide(group.downProbability, probability);
    }
    grouped.set(key, group);
  }

  return [...grouped.values()].sort((left, right) => compareMarketPreference(left, right, nowMs));
}

function currentMarketRows(rows: MarketMonitorRow[], nowMs: number) {
  const preferredAssets = ["BTC", "ETH"];
  return preferredAssets.flatMap((asset) => {
    const candidates = rows
      .filter(
        (row) =>
          row.asset === asset &&
          (row.upProbability || row.downProbability) &&
          row.expiryTs,
      )
      .sort((left, right) => compareMarketPreference(left, right, nowMs));
    const current = candidates[0];
    return current ? [current] : [];
  });
}

function compareMarketPreference(left: MarketMonitorRow, right: MarketMonitorRow, nowMs: number) {
  const timing = compareContractTiming(left, right, nowMs);
  if (timing !== 0) {
    return timing;
  }
  const completeness =
    probabilitySideCount(right.upProbability, right.downProbability) -
    probabilitySideCount(left.upProbability, left.downProbability);
  if (completeness !== 0) {
    return completeness;
  }
  return compareAssetPreference(left.asset, right.asset);
}

function compareProbabilityPreference(left: ProbabilityRow, right: ProbabilityRow, nowMs: number) {
  const timing = compareContractTiming(left, right, nowMs);
  if (timing !== 0) {
    return timing;
  }
  const asset = compareAssetPreference(left.asset, right.asset);
  if (asset !== 0) {
    return asset;
  }
  return compareOptionalTimestampDesc(left.asof_ts, right.asof_ts);
}

function compareContractTiming(
  left: Pick<ProbabilityRow, "start_ts" | "expiry_ts"> | MarketMonitorRow,
  right: Pick<ProbabilityRow, "start_ts" | "expiry_ts"> | MarketMonitorRow,
  nowMs: number,
) {
  const leftRank = contractTimingRank(left, nowMs);
  const rightRank = contractTimingRank(right, nowMs);
  if (leftRank !== rightRank) {
    return leftRank - rightRank;
  }
  if (leftRank === 2) {
    return contractExpiryMs(right) - contractExpiryMs(left);
  }
  const leftStart = contractStartMs(left);
  const rightStart = contractStartMs(right);
  const start =
    Number.isFinite(leftStart) && Number.isFinite(rightStart)
      ? leftStart - rightStart
      : 0;
  if (start !== 0) {
    return start;
  }
  return compareFiniteTimestamp(contractExpiryMs(left), contractExpiryMs(right));
}

function contractTimingRank(
  row: Pick<ProbabilityRow, "start_ts" | "expiry_ts"> | MarketMonitorRow,
  nowMs: number,
) {
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

function contractTimingLabel(
  row: Pick<ProbabilityRow, "start_ts" | "expiry_ts"> | MarketMonitorRow,
  nowMs: number,
) {
  const rank = contractTimingRank(row, nowMs);
  if (rank === 0) {
    return "current";
  }
  if (rank === 1) {
    return "next";
  }
  if (rank === 2) {
    return "expired";
  }
  return "pending";
}

function marketTimingLabel(row: MarketMonitorRow, nowMs: number) {
  return contractTimingLabel(row, nowMs);
}

function contractStartMs(row: Pick<ProbabilityRow, "start_ts"> | MarketMonitorRow) {
  return timestampMs("startTs" in row ? row.startTs : row.start_ts);
}

function contractExpiryMs(row: Pick<ProbabilityRow, "expiry_ts"> | MarketMonitorRow) {
  return timestampMs("expiryTs" in row ? row.expiryTs : row.expiry_ts);
}

function compareFiniteTimestamp(leftMs: number, rightMs: number) {
  if (Number.isFinite(leftMs) && Number.isFinite(rightMs)) {
    return leftMs - rightMs;
  }
  if (Number.isFinite(leftMs)) {
    return -1;
  }
  if (Number.isFinite(rightMs)) {
    return 1;
  }
  return 0;
}

function compareOptionalTimestampDesc(left?: string, right?: string) {
  const leftMs = timestampMs(left);
  const rightMs = timestampMs(right);
  if (Number.isFinite(leftMs) && Number.isFinite(rightMs)) {
    return rightMs - leftMs;
  }
  if (Number.isFinite(leftMs)) {
    return -1;
  }
  if (Number.isFinite(rightMs)) {
    return 1;
  }
  return 0;
}

function probabilitySideCount(up?: ProbabilityRow, down?: ProbabilityRow) {
  return (up ? 1 : 0) + (down ? 1 : 0);
}

function compareAssetPreference(left?: string, right?: string) {
  return assetPreferenceRank(left) - assetPreferenceRank(right);
}

function assetPreferenceRank(asset?: string) {
  const normalized = normalizeAsset(asset);
  const preferred = ["BTC", "ETH"].indexOf(normalized);
  return preferred === -1 ? Number.MAX_SAFE_INTEGER : preferred;
}

function preferProbabilitySide(existing: ProbabilityRow | undefined, next: ProbabilityRow) {
  if (!existing) {
    return next;
  }
  if (!existing.simulation_preview && next.simulation_preview) {
    return next;
  }
  return compareOptionalTimestampDesc(next.asof_ts, existing.asof_ts) <= 0 ? next : existing;
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

function marketRowForProbability(row: ProbabilityRow, marketRows: MarketMonitorRow[]) {
  const selectedKey = marketSideKey(
    row.market_slug,
    row.asset,
    row.expiry_ts,
    row.side,
    row.start_ts,
  );
  return (
    marketRows.find((marketRow) => {
      const upKey = marketSideKey(
        marketRow.marketSlug,
        marketRow.asset,
        marketRow.expiryTs,
        "UP",
        marketRow.startTs,
      );
      const downKey = marketSideKey(
        marketRow.marketSlug,
        marketRow.asset,
        marketRow.expiryTs,
        "DOWN",
        marketRow.startTs,
      );
      return selectedKey === upKey || selectedKey === downKey;
    }) ??
    marketRows.find(
      (marketRow) => marketRow.asset === row.asset && marketRow.expiryTs === row.expiry_ts,
    )
  );
}

function buildRuntimeLogEntries(
  live: ApiState<RuntimeLivePayload>,
  probabilities: ApiState<ProbabilityPayload>,
  selectedRow: ProbabilityRow | null,
) {
  const entries: RuntimeLogEntry[] = [];
  let sequence = 0;
  const push = (
    source: string,
    message: unknown,
    options: { at?: string | number | null; level?: RuntimeLogEntry["level"] } = {},
  ) => {
    const text = compactValue(message);
    if (!text) {
      return;
    }
    entries.push({
      at: timestampMs(options.at),
      source,
      level: options.level ?? "info",
      message: text,
      sequence: sequence++,
    });
  };

  push("live", live.error, { at: live.updatedAt, level: "warn" });
  push("monte_carlo", probabilities.error, { at: probabilities.updatedAt, level: "warn" });
  push("monte_carlo", probabilities.notice, { at: probabilities.updatedAt });

  const livePayload = live.payload;
  const liveAt =
    livePayload?.server_sent_at ??
    livePayload?.status?.generated_at ??
    livePayload?.monitor?.generated_at;
  push("status", livePayload?.status?.error, { at: liveAt, level: "warn" });
  for (const [source, message] of objectEntries(livePayload?.monitor?.source_errors)) {
    push(source, message, { at: livePayload?.monitor?.generated_at, level: "warn" });
  }
  for (const message of unknownList(livePayload?.monitor?.health_flags)) {
    push("health", message, { at: livePayload?.monitor?.generated_at, level: "warn" });
  }
  for (const message of [
    ...unknownList(livePayload?.gates?.failures),
    ...unknownList(livePayload?.gates?.errors),
    ...unknownList(livePayload?.gates?.reasons),
  ]) {
    push("decision", message, { at: livePayload?.gates?.generated_at, level: "warn" });
  }
  for (const message of unknownList(livePayload?.volatility?.errors)) {
    push("volatility", message, { at: livePayload?.volatility?.generated_at, level: "warn" });
  }

  const probabilityPayload = probabilities.payload;
  for (const message of unknownList(probabilityPayload?.errors)) {
    push("monte_carlo", message, { at: probabilityPayload?.generated_at, level: "warn" });
  }

  if (selectedRow) {
    push(
      "selected",
      compactList([
        selectedRow.asset,
        selectedRow.side,
        `${formatLaneStatus(selectedRow)} ${formatProbability(probabilityDisplayValue(selectedRow))}`,
        formatNoTouch(selectedRow),
        `wave ${formatWave(selectedRow)}`,
        `total paths=${formatInteger(selectedRow.path_count)}`,
        `runtime=${formatAge(probabilityMetadata(selectedRow).runtimeMs)}`,
        `lag=${formatAge(probabilityMetadata(selectedRow).totalLagMs)}`,
      ]),
      { at: selectedRow.asof_ts },
    );
    for (const message of [
      ...unknownList(selectedRow.wave_reasons),
      ...unknownList(selectedRow.wave_markers),
      ...unknownList(selectedRow.gate_reasons),
      ...unknownList(selectedRow.path_diagnosis),
      ...unknownList(selectedRow.flags),
    ]) {
      push("selected", message, { at: selectedRow.asof_ts, level: "warn" });
    }
  }

  return entries.sort((left, right) => left.at - right.at || left.sequence - right.sequence);
}

function orderbookForSide(row: ProbabilityRow, marketRow?: MarketMonitorRow) {
  if (row.side?.toUpperCase() === "UP") {
    return marketRow?.up;
  }
  if (row.side?.toUpperCase() === "DOWN") {
    return marketRow?.down;
  }
  return undefined;
}

function rowKey(row: ProbabilityRow) {
  return probabilityRowKey(row);
}

function selectionKey(row: ProbabilityRow) {
  return probabilitySelectionKey(row);
}

function contractLabel(row: ProbabilityRow) {
  const assetSide = compactList([row.asset, row.side]);
  return (row.contract ?? assetSide) || "Unknown contract";
}

function capitalizeLabel(value: string) {
  if (!value) {
    return value;
  }
  return `${value.slice(0, 1).toUpperCase()}${value.slice(1)}`;
}

function selectedContractSubtitle(row: ProbabilityRow, timingLabel: string) {
  const expiry = formatTimestamp(row.expiry_ts);
  const label = capitalizeLabel(timingLabel);
  return expiry === "pending" ? `${label} / expiry pending` : `${label} / expires ${expiry}`;
}

function probabilityEmptyBody(state: ApiState<ProbabilityPayload>) {
  if (state.status === "loading") {
    return "Waiting for the Monte Carlo endpoint.";
  }
  if (state.error) {
    return `Endpoint unavailable: ${state.error}`;
  }
  if (state.payload?.state === "DISABLED") {
    return "Runtime Monte Carlo generation is disabled.";
  }
  return state.payload?.error ?? "The endpoint returned an empty row set.";
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

function objectEntries(value: unknown): Array<[string, unknown]> {
  return isRecord(value) ? Object.entries(value) : [];
}

function cleanString(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : null;
}

function arrayLength(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

function compactList(values?: Array<string | number | boolean | null | undefined>) {
  return (values ?? [])
    .filter((value) => value !== undefined && value !== null && value !== "")
    .join(" / ");
}

function marketSideKey(
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
  const normalizedExpiry = identityTimestamp(expiryTs);
  const normalizedStart = identityTimestamp(startTs);
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

function marketGroupKey(
  marketSlug?: string,
  asset?: string,
  expiryTs?: string,
  startTs?: string,
) {
  const normalizedAsset = normalizeAsset(asset);
  const normalizedExpiry = identityTimestamp(expiryTs);
  const normalizedStart = identityTimestamp(startTs);
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

function normalizeAsset(value?: string) {
  return value?.trim().toUpperCase() ?? "";
}

function normalizeSide(value?: string) {
  return value?.trim().toUpperCase() ?? "";
}

function identityTimestamp(value?: string) {
  const trimmed = value?.trim();
  if (!trimmed) {
    return undefined;
  }
  const parsed = timestampMs(trimmed);
  return Number.isFinite(parsed) ? new Date(parsed).toISOString() : trimmed;
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
  return value.replaceAll("TRADE", "ENTRY").replaceAll("ORDER", "ENTRY");
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

function formatWave(row: ProbabilityRow) {
  const phase = cleanString(row.wave_phase) ?? "-";
  const score = isFiniteNumber(row.wave_score) ? row.wave_score.toFixed(2) : "--";
  const markers = unknownList(row.wave_markers).map(compactValue).filter(Boolean).join("/");
  return markers ? `${phase} ${score} ${markers}` : `${phase} ${score}`;
}

function formatDynamicEdge(row: ProbabilityRow) {
  if (!isFiniteNumber(row.dynamic_edge) || !isFiniteNumber(row.dynamic_required_edge)) {
    return "-";
  }
  return `${row.dynamic_edge.toFixed(3)} / ${row.dynamic_required_edge.toFixed(3)}`;
}

function formatLaneStatus(row: ProbabilityRow) {
  const lane = probabilityKind(row);
  return probabilityDisplayStatus(row) === "held" ? `held ${lane}` : lane;
}

function formatNoTouch(row: ProbabilityRow) {
  return `pNT ${formatProbability(row.p_no_touch)}`;
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

function formatAgeIfPresent(ageMs?: number) {
  return isFiniteNumber(ageMs) ? formatAge(ageMs) : undefined;
}

function formatCompactPathCount(value?: number) {
  return isFiniteNumber(value) ? `${formatInteger(value)} paths` : undefined;
}

function formatRuntimeAndLag(metadata: ReturnType<typeof probabilityMetadata>) {
  const value = compactList([
    formatAgeIfPresent(metadata.runtimeMs),
    metadata.totalLagMs !== undefined ? `lag ${formatAge(metadata.totalLagMs)}` : undefined,
  ]);
  return value || "-";
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
    const suffix = isFiniteNumber(pathCount) ? ` total paths=${formatInteger(pathCount)}` : "";
    return `${hits}/${rows.length} grid${suffix}`;
  }
  return payload.cached ? "api hit" : "no grid";
}

function formatRowCache(row: ProbabilityRow) {
  if (row.cache_status) {
    const pathCount = isFiniteNumber(row.path_count)
      ? ` total paths=${formatInteger(row.path_count)}`
      : "";
    return `${row.cache_status}${pathCount}`;
  }
  return row.output_id ? "persisted" : "-";
}

function formatLatency(latency?: JsonRecord) {
  const value = latency?.api_build_ms;
  return isFiniteNumber(value) ? `${value}ms` : "-";
}

function formatPreviewWinCounts(preview: SimulationPreview) {
  return `wins ${formatInteger(preview.terminal_win_count)} / total paths ${formatInteger(
    preview.path_count,
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

function phaseTone(value: string) {
  const normalized = value.toLowerCase();
  if (normalized === "breaking") {
    return "breaking";
  }
  if (normalized === "late") {
    return "late";
  }
  if (normalized === "missed") {
    return "missed";
  }
  if (normalized === "forming") {
    return "forming";
  }
  return "none";
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

function clamp01(value: number) {
  return Math.max(0, Math.min(1, value));
}

function normalizedProbability(value?: number) {
  return isFiniteNumber(value) ? clamp01(value) : null;
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

function formatLocalTimestamp(value?: number | null) {
  if (!isFiniteNumber(value)) {
    return "pending";
  }
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function formatLogTimestamp(value: number) {
  if (!Number.isFinite(value)) {
    return "--:--:--";
  }
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
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
