import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  fetchMonteCarloStatus,
  fetchSimulationArtifact,
  type MonteCarloRow,
  type MonteCarloStatus,
  type SimulationArtifact,
} from "./api";

const POLL_INTERVAL_MS = 3000;

export function MonteCarloView() {
  const [status, setStatus] = useState<MonteCarloStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedArtifact, setSelectedArtifact] =
    useState<SimulationArtifact | null>(null);
  const [artifactError, setArtifactError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;

    const load = async () => {
      try {
        const payload = await fetchMonteCarloStatus(8);
        if (!alive) {
          return;
        }
        setStatus(payload);
        setError(null);
      } catch (exc) {
        if (!alive) {
          return;
        }
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    };

    load();
    const id = window.setInterval(load, POLL_INTERVAL_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const rows = status?.rows ?? [];
  const chartRows = useMemo(
    () =>
      rows.map((row) => ({
        contract: compactContract(row.contract),
        p_finish: row.p_finish ?? 0,
        p_no_touch: row.p_no_touch ?? 0,
      })),
    [rows],
  );

  const loadArtifact = async (artifactId: string) => {
    try {
      setArtifactError(null);
      setSelectedArtifact(await fetchSimulationArtifact(artifactId));
    } catch (exc) {
      setArtifactError(exc instanceof Error ? exc.message : String(exc));
      setSelectedArtifact(null);
    }
  };

  return (
    <main className="dashboard-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">Polymarket Research</p>
          <h1>Monte Carlo Cockpit</h1>
        </div>
        <StatusBadge ok={status?.ok} state={status?.state ?? "LOADING"} />
      </header>

      <section className="status-grid" aria-label="Monte Carlo status">
        <Metric label="Rows" value={rows.length.toString()} />
        <Metric label="Generated" value={formatGeneratedAt(status?.generated_at)} />
        <Metric label="Backends" value={backendSummary(rows)} />
        <Metric label="Paths" value={pathSummary(rows)} />
      </section>

      {error ? <div className="notice error">{error}</div> : null}
      {status?.errors?.length ? (
        <div className="notice warning">{status.errors.join("; ")}</div>
      ) : null}

      <section className="dashboard-grid">
        <div className="panel chart-panel">
          <div className="panel-heading">
            <h2>Probability Summary</h2>
            <span>cached outputs</span>
          </div>
          {chartRows.length ? (
            <ResponsiveContainer width="100%" height={300}>
              <BarChart data={chartRows} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
                <CartesianGrid stroke="#d8ded7" strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="contract" tickLine={false} axisLine={false} />
                <YAxis domain={[0, 1]} tickLine={false} axisLine={false} />
                <Tooltip
                  formatter={(value) =>
                    typeof value === "number" ? value.toFixed(3) : value
                  }
                />
                <Legend />
                <Bar dataKey="p_finish" name="p_finish" fill="#276a73" radius={[4, 4, 0, 0]} />
                <Bar dataKey="p_no_touch" name="p_no_touch" fill="#9a6b2f" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState state={status?.state ?? "PENDING"} />
          )}
        </div>

        <div className="panel artifact-panel">
          <div className="panel-heading">
            <h2>Artifact Inspector</h2>
            <span>persisted metadata</span>
          </div>
          {artifactError ? <div className="notice error">{artifactError}</div> : null}
          {selectedArtifact ? (
            <dl className="artifact-meta">
              <Meta label="artifact_id" value={selectedArtifact.artifact_id} />
              <Meta label="output_id" value={selectedArtifact.output_id} />
              <Meta label="state_id" value={selectedArtifact.state_id} />
              <Meta label="asof_ts" value={selectedArtifact.asof_ts} />
              <Meta label="backend" value={selectedArtifact.backend} />
              <Meta label="model" value={selectedArtifact.model_version} />
            </dl>
          ) : (
            <p className="muted">No artifact selected.</p>
          )}
        </div>
      </section>

      <section className="panel table-panel">
        <div className="panel-heading">
          <h2>Cached Runs</h2>
          <span>{rows.length ? `${rows.length} rows` : "waiting"}</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Contract</th>
                <th>p_finish</th>
                <th>p_no_touch</th>
                <th>z_path</th>
                <th>sigma_tau</th>
                <th>Backend</th>
                <th>Paths</th>
                <th>Model</th>
                <th>Age/Flags</th>
                <th>Artifact</th>
              </tr>
            </thead>
            <tbody>
              {rows.length ? (
                rows.map((row) => (
                  <MonteCarloTableRow
                    key={`${row.contract}-${row.artifact_id ?? row.model_version ?? "row"}`}
                    row={row}
                    onArtifact={loadArtifact}
                  />
                ))
              ) : (
                <tr>
                  <td colSpan={10} className="empty-cell">
                    Monte Carlo status is {status?.state ?? "loading"}.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}

function MonteCarloTableRow({
  row,
  onArtifact,
}: {
  row: MonteCarloRow;
  onArtifact: (artifactId: string) => void;
}) {
  const artifactId = row.artifact_id;

  return (
    <tr>
      <td className="contract-cell">{row.contract}</td>
      <td>{formatProbability(row.p_finish)}</td>
      <td>{formatProbability(row.p_no_touch)}</td>
      <td>{formatNumber(row.z_path, 3)}</td>
      <td>{formatNumber(row.sigma_tau, 5)}</td>
      <td>{row.backend ?? "-"}</td>
      <td>{formatPaths(row.path_count)}</td>
      <td>{row.model_version ?? "-"}</td>
      <td>{formatAgeFlags(row)}</td>
      <td>
        {artifactId ? (
          <button type="button" className="link-button" onClick={() => onArtifact(artifactId)}>
            view
          </button>
        ) : (
          "-"
        )}
      </td>
    </tr>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Meta({ label, value }: { label: string; value?: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value ?? "-"}</dd>
    </>
  );
}

function StatusBadge({ ok, state }: { ok?: boolean; state: string }) {
  return (
    <div className={`status-badge ${ok ? "ok" : "blocked"}`}>
      <span>{ok ? "OK" : "WAIT"}</span>
      <strong>{state}</strong>
    </div>
  );
}

function EmptyState({ state }: { state: string }) {
  return (
    <div className="empty-state">
      <strong>No cached Monte Carlo rows</strong>
      <span>State: {state}</span>
    </div>
  );
}

function compactContract(contract: string) {
  return contract.length > 18 ? `${contract.slice(0, 15)}...` : contract;
}

function formatProbability(value: number | null) {
  return value == null ? "-" : value.toFixed(3);
}

function formatNumber(value: number | null, digits: number) {
  return value == null ? "-" : value.toFixed(digits);
}

function formatGeneratedAt(value?: string | null) {
  if (!value) {
    return "-";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.valueOf())) {
    return value;
  }
  return parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatPaths(value: number | null) {
  if (value == null) {
    return "-";
  }
  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1)}M`;
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1)}k`;
  }
  return value.toString();
}

function formatAgeFlags(row: MonteCarloRow) {
  const age = row.age_ms == null ? "-" : `${row.age_ms}ms`;
  const flags = row.flags.length ? row.flags.join(", ") : "OK";
  return `${age} ${flags}`;
}

function backendSummary(rows: MonteCarloRow[]) {
  const backends = [...new Set(rows.map((row) => row.backend).filter(Boolean))];
  return backends.length ? backends.join(", ") : "-";
}

function pathSummary(rows: MonteCarloRow[]) {
  const total = rows.reduce((sum, row) => sum + (row.path_count ?? 0), 0);
  return total ? formatPaths(total) : "-";
}
