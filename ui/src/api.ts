export type MonteCarloRow = {
  contract: string;
  p_finish: number | null;
  p_no_touch: number | null;
  z_path: number | null;
  sigma_tau: number | null;
  backend: string | null;
  path_count: number | null;
  model_version: string | null;
  age_ms?: number | null;
  flags: string[];
  artifact_id?: string | null;
};

export type MonteCarloStatus = {
  ok: boolean;
  state: string;
  generated_at?: string | null;
  rows: MonteCarloRow[];
  errors?: string[];
};

export type SimulationArtifact = {
  ok: boolean;
  artifact_id: string;
  output_id?: string;
  state_id?: string;
  asof_ts?: string;
  model_version?: string;
  backend?: string;
  artifact?: unknown;
};

export async function fetchMonteCarloStatus(limit = 8): Promise<MonteCarloStatus> {
  const response = await fetch(`/api/runtime/monte-carlo/status?limit=${limit}`);
  if (!response.ok) {
    throw new Error(`Monte Carlo status failed: ${response.status}`);
  }
  return response.json();
}

export async function fetchSimulationArtifact(
  artifactId: string,
  signal?: AbortSignal,
): Promise<SimulationArtifact> {
  const response = await fetch(
    `/api/runtime/simulation-artifacts/${encodeURIComponent(artifactId)}`,
    { signal },
  );
  if (!response.ok) {
    throw new Error(`Simulation artifact failed: ${response.status}`);
  }
  return response.json();
}
