import { useEffect, useMemo, useState } from "react";

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
  contract: string;
  contract_id?: string;
  asset?: string;
  side?: string;
  asof_ts?: string;
  expiry_ts?: string;
  p_finish: number;
  p_no_touch: number;
  z_path: number;
  sigma_tau: number;
  age_ms: number;
  flags?: string[];
  model_version?: string;
  output_id?: string;
  mc_dispersion?: number | null;
  uncertainty_buffer?: number | null;
  path_diagnosis?: string[];
  effective_weights?: Record<string, number>;
  decision_hint?: string | null;
  edge_after_costs?: number | null;
  required_edge?: number | null;
  gate_reasons?: string[];
  generator_metadata?: Record<string, unknown>;
  simulation_preview?: SimulationPreview | null;
};

type ProbabilityPayload = {
  ok: boolean;
  state: string;
  generated_at?: string;
  cached?: boolean;
  model_version?: string | null;
  rows?: ProbabilityRow[];
  skipped?: number;
  errors?: string[];
};

type RuntimeState =
  | { source: "loading"; payload: ProbabilityPayload; error: null }
  | { source: "api"; payload: ProbabilityPayload; error: null }
  | { source: "fixture"; payload: ProbabilityPayload; error: string };

const fallbackPayload: ProbabilityPayload = {
  ok: true,
  state: "PREVIEW",
  cached: false,
  generated_at: "2026-06-05T20:10:00+00:00",
  model_version: "fixture-ensemble-v1",
  skipped: 0,
  errors: [],
  rows: [
    {
      contract: "BTC 5m UP",
      contract_id: "btc-5m-up",
      asset: "BTC",
      side: "UP",
      asof_ts: "2026-06-05T20:10:00+00:00",
      expiry_ts: "2026-06-05T20:15:00+00:00",
      p_finish: 0.674,
      p_no_touch: 0.718,
      z_path: 0.82,
      sigma_tau: 0.0118,
      age_ms: 840,
      flags: ["OK"],
      mc_dispersion: 0.073,
      uncertainty_buffer: 0.046,
      path_diagnosis: ["FRAGILE", "NEAR_THRESHOLD"],
      effective_weights: {
        lognormal_baseline: 0.55,
        empirical_conditional: 0.3,
        stress_overlay: 0.15,
      },
      decision_hint: "WAIT",
      edge_after_costs: 0.019,
      required_edge: 0.086,
      gate_reasons: ["NEAR_THRESHOLD"],
      generator_metadata: { snapshot_id: "fixture-preview", source: "local_fixture" },
      simulation_preview: fixturePreview({
        threshold: 100,
        start: 100,
        drift: 0.08,
        amplitude: 0.82,
        pathCount: 1000,
        terminalWins: 674,
        noTouchWins: 718,
        comparison: ">=",
      }),
    },
    {
      contract: "BTC 5m DOWN",
      contract_id: "btc-5m-down",
      asset: "BTC",
      side: "DOWN",
      asof_ts: "2026-06-05T20:10:00+00:00",
      expiry_ts: "2026-06-05T20:15:00+00:00",
      p_finish: 0.382,
      p_no_touch: 0.811,
      z_path: -0.42,
      sigma_tau: 0.0118,
      age_ms: 910,
      flags: ["OK"],
      mc_dispersion: 0.031,
      uncertainty_buffer: 0.029,
      path_diagnosis: ["WRONG_SIDE"],
      effective_weights: {
        lognormal_baseline: 0.62,
        empirical_conditional: 0.23,
        stress_overlay: 0.15,
      },
      decision_hint: "BLOCK",
      edge_after_costs: -0.074,
      required_edge: 0.059,
      gate_reasons: ["WRONG_SIDE"],
      generator_metadata: { snapshot_id: "fixture-preview", source: "local_fixture" },
      simulation_preview: fixturePreview({
        threshold: 100,
        start: 100,
        drift: -0.04,
        amplitude: 0.55,
        pathCount: 1000,
        terminalWins: 382,
        noTouchWins: 811,
        comparison: "<",
      }),
    },
    {
      contract: "ETH 5m UP",
      contract_id: "eth-5m-up",
      asset: "ETH",
      side: "UP",
      asof_ts: "2026-06-05T20:10:00+00:00",
      expiry_ts: "2026-06-05T20:15:00+00:00",
      p_finish: 0.593,
      p_no_touch: 0.762,
      z_path: 0.64,
      sigma_tau: 0.0142,
      age_ms: 1120,
      flags: ["OK"],
      mc_dispersion: 0.028,
      uncertainty_buffer: 0.031,
      path_diagnosis: ["CLEAN"],
      effective_weights: {
        lognormal_baseline: 0.48,
        empirical_conditional: 0.37,
        stress_overlay: 0.15,
      },
      decision_hint: "DEMAND_MORE_EDGE",
      edge_after_costs: 0.024,
      required_edge: 0.061,
      gate_reasons: ["INSUFFICIENT_EDGE"],
      generator_metadata: { snapshot_id: "fixture-preview", source: "local_fixture" },
      simulation_preview: fixturePreview({
        threshold: 2500,
        start: 2500,
        drift: 0.05,
        amplitude: 19,
        pathCount: 1000,
        terminalWins: 593,
        noTouchWins: 762,
        comparison: ">=",
      }),
    },
  ],
};

const emptyPayload: ProbabilityPayload = {
  ok: false,
  state: "LOADING",
  cached: false,
  rows: [],
  skipped: 0,
  errors: [],
};

export function App() {
  const [runtime, setRuntime] = useState<RuntimeState>({
    source: "loading",
    payload: emptyPayload,
    error: null,
  });
  const rows = runtime.payload.rows ?? [];
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selectedRow = useMemo(
    () => rows.find((row) => rowKey(row) === selectedId) ?? rows[0] ?? null,
    [rows, selectedId],
  );

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/runtime/probabilities?limit=12", { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        return (await response.json()) as ProbabilityPayload;
      })
      .then((payload) => setRuntime({ source: "api", payload, error: null }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) {
          return;
        }
        setRuntime({
          source: "fixture",
          payload: fallbackPayload,
          error: error instanceof Error ? error.message : "runtime API unavailable",
        });
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (rows.length === 0) {
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
          <h1>Probability Runtime</h1>
          <p>Monte Carlo preview, cache status, and read-only gate output.</p>
        </div>
        <div className="mode-lock">Read-only paper</div>
      </header>

      <StatusStrip runtime={runtime} rowCount={rows.length} />

      <section className="runtime-grid" aria-label="Probability runtime dashboard">
        <SimulationPanel row={selectedRow} runtime={runtime} />
        <aside className="side-stack">
        <ContractQueue
          rows={rows}
          selectedKey={selectedRow ? rowKey(selectedRow) : null}
          onSelect={setSelectedId}
        />
          {selectedRow ? <GateAndWeights row={selectedRow} /> : null}
        </aside>
      </section>
    </main>
  );
}

function StatusStrip({ runtime, rowCount }: { runtime: RuntimeState; rowCount: number }) {
  const cacheState = runtime.payload.cached ? "1s HIT" : runtime.source === "api" ? "LIVE READ" : "PREVIEW";
  return (
    <section className="status-strip" aria-label="Runtime status">
      <Metric
        label="State"
        value={runtime.source === "fixture" ? "PREVIEW" : runtime.payload.state}
        tone={runtime.payload.ok ? "good" : "warn"}
      />
      <Metric label="Generated" value={formatTimestamp(runtime.payload.generated_at)} />
      <Metric label="Payload" value={cacheState} tone={runtime.payload.cached ? "good" : "neutral"} />
      <Metric label="Grid cache" value="PENDING" tone="warn" />
      <Metric label="Rows" value={String(rowCount)} />
      <Metric label="Skipped" value={String(runtime.payload.skipped ?? 0)} />
      {runtime.source === "fixture" ? <div className="status-note">API fallback: {runtime.error}</div> : null}
    </section>
  );
}

function SimulationPanel({ row, runtime }: { row: ProbabilityRow | null; runtime: RuntimeState }) {
  if (!row) {
    return (
      <section className="panel simulation-panel">
        <PanelHeader title="Monte Carlo Preview" subtitle="No probability rows available." />
      </section>
    );
  }

  const preview = row.simulation_preview ?? null;
  return (
    <section className="panel simulation-panel">
      <div className="simulation-head">
        <div>
          <p className="panel-kicker">Selected contract</p>
          <h2>{row.contract}</h2>
          <p>{row.contract_id ?? "runtime probability row"}</p>
        </div>
        <GatePill value={row.decision_hint} />
      </div>

      <div className="hero-metrics">
        <Metric label="p_finish" value={formatProbability(row.p_finish)} />
        <Metric label="p_no_touch" value={formatProbability(row.p_no_touch)} />
        <Metric label="edge / req" value={formatEdge(row)} />
        <Metric label="sigma_tau" value={formatSmall(row.sigma_tau)} />
      </div>

      <div className="simulation-body">
        <MonteCarloCanvas preview={preview} row={row} source={runtime.source} />
        <TerminalHistogram preview={preview} />
      </div>

      <div className="simulation-footer">
        <Metric label="paths" value={preview ? formatInteger(preview.path_count) : "-"} />
        <Metric label="steps" value={preview ? String(preview.steps) : "-"} />
        <Metric label="terminal wins" value={preview ? formatInteger(preview.terminal_win_count) : "-"} />
        <Metric label="no-touch wins" value={preview ? formatInteger(preview.no_touch_win_count) : "-"} />
      </div>
    </section>
  );
}

function MonteCarloCanvas({
  preview,
  row,
  source,
}: {
  preview: SimulationPreview | null;
  row: ProbabilityRow;
  source: RuntimeState["source"];
}) {
  const geometry = useMemo(() => (preview ? buildPathGeometry(preview) : null), [preview]);
  if (!preview || !geometry) {
    return (
      <div className="chart-empty">
        <strong>Simulation preview unavailable</strong>
        <span>Persisted rows need `simulation_preview` diagnostics to draw real paths.</span>
      </div>
    );
  }

  return (
    <div className="path-chart">
      <div className="chart-labels">
        <span>Threshold {formatPrice(preview.threshold)}</span>
        <span>{source === "fixture" ? "fixture preview" : "runtime preview"}</span>
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
        <span>{row.path_diagnosis?.join(",") || "path diagnostics pending"}</span>
        <span>z {formatSigned(row.z_path)}</span>
      </div>
    </div>
  );
}

function TerminalHistogram({ preview }: { preview: SimulationPreview | null }) {
  if (!preview || preview.terminal_histogram.length === 0) {
    return <div className="histogram-empty">Terminal distribution pending.</div>;
  }
  const maxCount = Math.max(...preview.terminal_histogram.map((bucket) => bucket.count));
  return (
    <div className="histogram-panel">
      <div className="histogram-title">
        <span>Terminal distribution</span>
        <strong>{preview.comparison_operator} {formatPrice(preview.threshold)}</strong>
      </div>
      <div className="histogram-bars">
        {preview.terminal_histogram.map((bucket) => (
          <div className="histogram-bar-wrap" key={`${bucket.lower}-${bucket.upper}`}>
            <div
              className={isWinningBucket(preview, bucket) ? "histogram-bar win-bin" : "histogram-bar"}
              style={{ height: `${Math.max(8, (bucket.count / maxCount) * 100)}%` }}
              title={`${formatPrice(bucket.lower)}-${formatPrice(bucket.upper)}: ${bucket.count}`}
            />
          </div>
        ))}
      </div>
    </div>
  );
}

function ContractQueue({
  rows,
  selectedKey,
  onSelect,
}: {
  rows: ProbabilityRow[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  return (
    <section className="panel queue-panel">
      <PanelHeader title="Contracts" subtitle="Select a row to inspect paths." />
      <div className="contract-list">
        {rows.length === 0 ? (
          <div className="empty-row">Probability rows pending.</div>
        ) : (
          rows.map((row) => {
            const key = rowKey(row);
            return (
              <button
                className={key === selectedKey ? "contract-row selected-row" : "contract-row"}
                key={key}
                onClick={() => onSelect(key)}
                type="button"
              >
                <span>
                  <strong>{row.contract}</strong>
                  <small>{formatAge(row.age_ms)} {formatList(row.flags, "OK")}</small>
                </span>
                <span className="row-numbers">
                  <b>{formatProbability(row.p_finish)}</b>
                  <GatePill value={row.decision_hint} />
                </span>
              </button>
            );
          })
        )}
      </div>
    </section>
  );
}

function GateAndWeights({ row }: { row: ProbabilityRow }) {
  return (
    <section className="panel compact-detail">
      <PanelHeader title="Gate + Generators" subtitle={row.contract_id ?? "selected row"} />
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
        <h3>Gate reasons</h3>
        <div className="chip-row">
          {(row.gate_reasons?.length ? row.gate_reasons : ["CLEAR"]).map((reason) => (
            <span className="chip" key={reason}>{reason}</span>
          ))}
        </div>
      </section>
    </section>
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
  const entries = Object.entries(weights).sort(([left], [right]) => left.localeCompare(right));
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
            <div className="bar-fill" style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
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
  const pathData = preview.sampled_paths.map((path) => {
    const maxIndex = Math.max(1, path.points.length - 1);
    const d = path.points
      .map((price, index) => {
        const x = left + (index / maxIndex) * width;
        const y = yFor(price);
        return `${index === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`;
      })
      .join(" ");
    return { d, index: path.index, terminalWin: path.terminal_win };
  });
  return {
    thresholdY: yFor(preview.threshold),
    startY: yFor(preview.start_price),
    paths: pathData,
  };
}

function fixturePreview({
  threshold,
  start,
  drift,
  amplitude,
  pathCount,
  terminalWins,
  noTouchWins,
  comparison,
}: {
  threshold: number;
  start: number;
  drift: number;
  amplitude: number;
  pathCount: number;
  terminalWins: number;
  noTouchWins: number;
  comparison: string;
}): SimulationPreview {
  const steps = 20;
  const sampled_paths = Array.from({ length: 24 }, (_, pathIndex) => {
    const points = Array.from({ length: steps + 1 }, (_, step) => {
      const t = step / steps;
      const wave = Math.sin(t * Math.PI * 2 + pathIndex * 0.65) * amplitude;
      const bend = Math.cos(t * Math.PI + pathIndex * 0.2) * amplitude * 0.32;
      return start + drift * step + wave + bend;
    });
    const terminal = points[points.length - 1];
    const terminal_win = comparison.startsWith(">") ? terminal >= threshold : terminal < threshold;
    const no_touch_win = points.every((point) =>
      comparison.startsWith(">") ? point >= threshold : point < threshold,
    );
    return { index: pathIndex, terminal_win, no_touch_win, points };
  });
  const terminal_histogram = Array.from({ length: 16 }, (_, index) => {
    const lower = threshold - amplitude * 2.2 + index * amplitude * 0.32;
    return { lower, upper: lower + amplitude * 0.32, count: 20 + ((index * 37) % 110) };
  });
  return {
    path_count: pathCount,
    steps,
    start_price: start,
    threshold,
    comparison_operator: comparison,
    terminal_win_count: terminalWins,
    no_touch_win_count: noTouchWins,
    sampled_paths,
    terminal_histogram,
  };
}

function rowKey(row: ProbabilityRow) {
  return row.output_id ?? row.contract_id ?? `${row.contract}-${row.asof_ts ?? ""}`;
}

function isWinningBucket(preview: SimulationPreview, bucket: HistogramBucket) {
  if (preview.comparison_operator === ">") {
    return bucket.upper > preview.threshold;
  }
  if (preview.comparison_operator === ">=") {
    return bucket.upper >= preview.threshold;
  }
  if (preview.comparison_operator === "<") {
    return bucket.lower < preview.threshold;
  }
  if (preview.comparison_operator === "<=") {
    return bucket.lower <= preview.threshold;
  }
  return false;
}

function formatProbability(value: number) {
  return value.toFixed(3);
}

function formatSmall(value: number) {
  return value.toFixed(5);
}

function formatSigned(value: number) {
  return `${value >= 0 ? "+" : ""}${value.toFixed(3)}`;
}

function formatOptional(value?: number | null) {
  return typeof value === "number" ? value.toFixed(3) : "-";
}

function formatEdge(row: ProbabilityRow) {
  if (typeof row.edge_after_costs !== "number" || typeof row.required_edge !== "number") {
    return "-";
  }
  return `${row.edge_after_costs.toFixed(3)}/${row.required_edge.toFixed(3)}`;
}

function formatAge(ageMs: number) {
  if (ageMs >= 1000) {
    return `${(ageMs / 1000).toFixed(1)}s`;
  }
  return `${ageMs}ms`;
}

function formatList(values: string[] | undefined, fallback: string) {
  return values && values.length > 0 ? values.join(",") : fallback;
}

function shortWeightLabel(value: string) {
  if (value === "empirical_conditional") {
    return "emp";
  }
  if (value === "lognormal_baseline") {
    return "log";
  }
  if (value === "stress_overlay") {
    return "stress";
  }
  return value.replaceAll("_", " ");
}

function formatGate(value?: string | null) {
  if (!value) {
    return "PENDING";
  }
  return value === "TRADE_CANDIDATE" ? "PAPER_CANDIDATE" : value;
}

function gateTone(value: string) {
  if (value === "BLOCK") {
    return "block";
  }
  if (value === "WAIT" || value === "DEMAND_MORE_EDGE") {
    return "wait";
  }
  if (value === "PAPER_CANDIDATE") {
    return "candidate";
  }
  return "neutral";
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

function formatInteger(value: number) {
  return new Intl.NumberFormat("en-US").format(value);
}

function formatPrice(value: number) {
  if (Math.abs(value) >= 1000) {
    return value.toLocaleString("en-US", { maximumFractionDigits: 0 });
  }
  return value.toFixed(2);
}
