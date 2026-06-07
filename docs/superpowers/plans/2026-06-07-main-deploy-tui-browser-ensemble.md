# Main Deploy, TUI Probabilities, and Browser Ensemble Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `main` the only GitHub/deploy branch, deploy THEPC by GitHub SSH pull, move TUI probability rows into the Market tab, stop noisy stream read errors, and restore browser Monte Carlo path fans with visible four-generator ensemble context.

**Architecture:** Keep runtime authority unchanged and read-only. Treat GitHub `origin/main` as the deploy source of truth, keep THEPC pinned by exact commit SHA after fetching `main`, and expose the existing ensemble outputs more clearly in TUI/browser surfaces. Separate backend simulation count from browser preview line count: backend gets enough paths for probability quality, browser receives a bounded sampled preview for rendering.

**Tech Stack:** Bash deploy scripts, Git/GitHub SSH, Rust Ratatui TUI, Python probability worker/runtime API, React/Vite browser UI, pytest, Rust cargo tests, TypeScript helper tests.

---

## Key Decisions

- `main` is the only long-lived GitHub branch.
- THEPC deploy should fetch `git@github.com:AnimeWeeb9000/polymarket.git` over SSH instead of receiving a git bundle.
- The Monte Carlo ensemble has four generator members: `empirical_conditional`, `block_bootstrap`, `filtered_historical`, and `stress_overlay`.
- Browser display should show one card per contract: BTC UP, BTC DOWN, ETH UP, ETH DOWN. Each card shows the final ensemble probability, a path fan sampled from all four generators, and a compact generator legend.
- Backend target path budget should default to `320_000` total paths per cycle on THEPC. With four live contract rows, this allows `80_000` paths per contract, matching `runtime_path_count_for_state()` for near-threshold/forming contracts.
- Browser preview should default to `64` sampled path lines per contract, stratified across the four generators. The browser must not run simulations; it only renders sampled backend paths.
- Implementation should happen on local branch `codex/main-deploy-tui-browser-ensemble`, then merge into `main` and push only `main`.

## File Structure

- Modify `scripts/deploy_pc.sh`
  - Replace git-bundle repository sync with GitHub SSH clone/fetch.
  - Default `PC_BRANCH=main`.
  - Add a preflight check that local `HEAD` equals `origin/main` before deploying.
  - Keep exact-SHA checkout on THEPC.
  - Increase THEPC probability path budget default to `320000`.

- Modify `tests/scripts/test_deploy_script.py`
  - Lock the GitHub SSH deploy flow.
  - Lock main-only defaults.
  - Lock the new `320000` THEPC path budget.

- Modify `README.md` and `docs/SPOON_DEPLOYMENT.md`
  - Document `main` as the only deploy branch.
  - Document THEPC GitHub SSH requirement.

- Modify `rust/crates/polymarket-cockpit-tui/src/state.rs`
  - Remove `MainTab::Probability`.

- Modify `rust/crates/polymarket-cockpit-tui/src/render/mod.rs`
  - Remove the `MainTab::Probability` render arm.

- Modify `rust/crates/polymarket-cockpit-tui/src/status.rs`
  - Add optional probability metadata fields needed for TUI labels.

- Modify `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`
  - Keep reusable probability table model helpers.
  - Add compact, status-free market probability rows.

- Modify `rust/crates/polymarket-cockpit-tui/src/render/market.rs`
  - Split the selected market lower area into book and probabilities.
  - Render compact contract probabilities below the book.

- Modify `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`
  - Treat live SSE read/close errors as transient and keep poll fallback running without setting `runtime_error`.

- Modify `src/polymarket_engine/probability/ensemble_runtime.py`
  - Attach `simulation_preview` to `ensemble-v1` diagnostics.
  - Include sampled paths from all four generators with generator IDs.
  - Include `generator_count` and total preview path count.

- Modify `src/polymarket_engine/probability/gpu_worker.py`
  - Raise `DEFAULT_MAX_TOTAL_PATHS` to `320_000`.
  - Ensure emitted rows/events preserve ensemble preview diagnostics.

- Modify `deploy/collector/.env.example`, `deploy/collector/docker-compose.yml`, and `deploy/gpu/gpu-probability-entrypoint.sh`
  - Add `POLYMARKET_ENSEMBLE_PREVIEW_PATHS` default `64`.
  - Add `POLYMARKET_ENSEMBLE_PREVIEW_POINTS` default `48`.
  - Raise default `POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS` to `320000`.

- Modify `ui/src/App.tsx`, `ui/src/probabilityRows.ts`, and `ui/src/styles.css`
  - Add optional `generator_id` on sampled preview paths.
  - Render ensemble legend/weights in each Monte Carlo card.
  - Color path lines by generator.
  - Retain previews across fresh rows for the same contract/window even when `asof_ts` changes.

- Modify tests:
  - `tests/probability/test_ensemble_runtime.py`
  - `tests/probability/test_gpu_worker.py`
  - `tests/test_cli.py`
  - `tests/scripts/test_deploy_script.py`
  - `tests/ui/probability_value_test.ts`
  - `tests/ui/probability_rows_test.ts`

---

### Task 0: Create Local Work Branch From Main

**Files:**
- No source file edits expected.

- [ ] **Step 1: Start from current GitHub main**

Run:

```bash
git fetch origin main
git switch main
git pull --ff-only origin main
git switch -c codex/main-deploy-tui-browser-ensemble
```

Expected: local branch `codex/main-deploy-tui-browser-ensemble` exists and starts at `origin/main`. Do not push this branch unless a reviewer explicitly needs it; final GitHub state should still be `main` only.

---

### Task 1: Main-Only GitHub SSH Deploy Flow

**Files:**
- Modify: `scripts/deploy_pc.sh`
- Modify: `tests/scripts/test_deploy_script.py`
- Modify: `README.md`
- Modify: `docs/SPOON_DEPLOYMENT.md`

- [ ] **Step 1: Write failing deploy-script tests**

In `tests/scripts/test_deploy_script.py`, replace the bundle expectations in `test_pc_deploy_script_streams_bundle_and_images_into_wsl()` with GitHub SSH expectations:

```python
def test_pc_deploy_script_fetches_main_from_github_ssh_in_wsl() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert 'PC_GIT_REMOTE="${PC_GIT_REMOTE:-git@github.com:AnimeWeeb9000/polymarket.git}"' in script
    assert 'PC_BRANCH="${PC_BRANCH:-main}"' in script
    assert 'PC_BUNDLE=' not in script
    assert "git -C \"$ROOT\" fetch --quiet origin main" in script
    assert 'LOCAL_MAIN_SHA="$(git -C "$ROOT" rev-parse origin/main^{commit})"' in script
    assert 'if [ "$LOCAL_MAIN_SHA" != "$FULL_SHA" ]; then' in script
    assert 'git clone "$PC_GIT_REMOTE" "$PC_REPO"' in script
    assert 'git remote set-url origin "$PC_GIT_REMOTE"' in script
    assert 'git fetch --quiet --prune origin "$PC_BRANCH"' in script
    assert 'git checkout -B "$PC_BRANCH" "$FULL_SHA"' in script
    assert "wsl_put_file()" not in script
    assert "git bundle create" not in script
```

Add this assertion to `test_pc_deploy_script_runs_prebuilt_deploy_gate_with_pc_cadence()`:

```python
assert 'PC_PROBABILITY_MAX_TOTAL_PATHS="${PC_PROBABILITY_MAX_TOTAL_PATHS:-320000}"' in script
```

Remove old assertions that require `PC_BUNDLE`, `git bundle create`, `wsl_put_file()`, and `copying git bundle`.

- [ ] **Step 2: Run deploy-script tests and verify they fail**

Run:

```bash
uv run pytest tests/scripts/test_deploy_script.py -q
```

Expected: fail because `deploy_pc.sh` still creates/copies a git bundle and defaults path budget to `40000`.

- [ ] **Step 3: Update `scripts/deploy_pc.sh` constants and preflight**

At the top of `scripts/deploy_pc.sh`, replace the repo/bundle/branch/path defaults with:

```bash
PC_REPO="${PC_REPO:-/home/ender/polymarket}"
PC_GIT_REMOTE="${PC_GIT_REMOTE:-git@github.com:AnimeWeeb9000/polymarket.git}"
PC_DATA_DIR="${PC_DATA_DIR:-/home/ender/polymarket-data}"
PC_DIST_DIR="${PC_DIST_DIR:-/home/ender/polymarket-image-artifacts}"
PC_BIN_DIR="${PC_BIN_DIR:-/home/ender/bin}"
PC_NORMALIZER_INTERVAL_SECONDS="${PC_NORMALIZER_INTERVAL_SECONDS:-0.1}"
PC_REST_BACKUP_INTERVAL_MS="${PC_REST_BACKUP_INTERVAL_MS:-1000}"
PC_PROBABILITY_MAX_TOTAL_PATHS="${PC_PROBABILITY_MAX_TOTAL_PATHS:-320000}"
PC_GPU_WORKER_MEM_LIMIT="${PC_GPU_WORKER_MEM_LIMIT:-1536m}"
PC_API_PORT="${PC_API_PORT:-8000}"
PC_DEPLOY_MODE="${PC_DEPLOY_MODE:-remote-build}"
PC_DEPLOY_BUILD_IMAGES="${PC_DEPLOY_BUILD_IMAGES:-1}"
PC_REMOTE_BUILD_SAVE_TARS="${PC_REMOTE_BUILD_SAVE_TARS:-0}"
PC_BRANCH="${PC_BRANCH:-main}"
```

After `FULL_SHA` and `HEAD_SHA` are computed, add:

```bash
if [ "$PC_BRANCH" != "main" ]; then
  echo "THEPC deploy is main-only; set PC_BRANCH=main" >&2
  exit 1
fi

git -C "$ROOT" fetch --quiet origin main
LOCAL_MAIN_SHA="$(git -C "$ROOT" rev-parse origin/main^{commit})"
if [ "$LOCAL_MAIN_SHA" != "$FULL_SHA" ]; then
  echo "origin/main is $LOCAL_MAIN_SHA but deploy ref is $FULL_SHA; push main before deploying" >&2
  exit 1
fi
```

Remove:

```bash
PC_BUNDLE=...
LOCAL_BUNDLE=...
git -C "$ROOT" bundle create ...
wsl_put_file()
wsl_put_file "$LOCAL_BUNDLE" "$PC_BUNDLE"
```

Keep `wsl_put_file` only if `image-tar` mode still needs tarball transfer. If it is kept for tarballs, rename it to `wsl_put_artifact_file()` so tests do not confuse it with repo sync.

- [ ] **Step 4: Update THEPC WSL repo sync block**

In the remote WSL heredoc, replace clone/fetch origin setup with:

```bash
PC_GIT_REMOTE=$(shell_quote "$PC_GIT_REMOTE")
```

and:

```bash
mkdir -p "$PC_DATA_DIR/raw" "$PC_DATA_DIR/db" "$PC_DATA_DIR/live" "$PC_DATA_DIR/logs" "$PC_DIST_DIR" "$PC_BIN_DIR"
touch "$PC_DATA_DIR/raw/.polymarket_archive_root"

if [ ! -d "$PC_REPO/.git" ]; then
  git clone "$PC_GIT_REMOTE" "$PC_REPO"
fi

cd "$PC_REPO"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "THEPC repo is dirty; refusing deploy" >&2
  git status --porcelain >&2
  exit 1
fi

git remote set-url origin "$PC_GIT_REMOTE" 2>/dev/null || git remote add origin "$PC_GIT_REMOTE"
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -T git@github.com >/dev/null 2>&1 || {
  echo "THEPC WSL cannot SSH to GitHub. Add /home/ender/.ssh/id_ed25519.pub to GitHub, then rerun." >&2
  if [ ! -f /home/ender/.ssh/id_ed25519.pub ]; then
    mkdir -p /home/ender/.ssh
    chmod 700 /home/ender/.ssh
    ssh-keygen -t ed25519 -N "" -C "thepc-polymarket@github" -f /home/ender/.ssh/id_ed25519
  fi
  cat /home/ender/.ssh/id_ed25519.pub >&2
  exit 1
}
git fetch --quiet --prune origin "$PC_BRANCH"
git checkout -B "$PC_BRANCH" "$FULL_SHA"
```

- [ ] **Step 5: Update docs**

In `README.md`, keep the existing main-only language and add:

```markdown
THEPC deploys fetch `origin/main` over SSH from GitHub. The Windows/WSL user
must have a GitHub SSH key that can read `git@github.com:AnimeWeeb9000/polymarket.git`.
The deploy script refuses commits that are not already present at `origin/main`.
```

In `docs/SPOON_DEPLOYMENT.md`, replace the old THEPC bundle language with:

```markdown
THEPC deploys are GitHub-pull based. The Mac pushes `main`; THEPC WSL fetches
`git@github.com:AnimeWeeb9000/polymarket.git`, checks out the exact pushed SHA,
then builds/restarts from that checkout. Do not deploy local-only commits.
```

- [ ] **Step 6: Run deploy-script tests**

Run:

```bash
uv run pytest tests/scripts/test_deploy_script.py -q
```

Expected: pass.

- [ ] **Step 7: Commit Task 1**

```bash
git add scripts/deploy_pc.sh tests/scripts/test_deploy_script.py README.md docs/SPOON_DEPLOYMENT.md
git commit -m "deploy: pull THEPC from GitHub main"
```

---

### Task 2: Market Tab Contract Probabilities and No Probability Tab

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/state.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/mod.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/status.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/market.rs`

- [ ] **Step 1: Write failing TUI tab test**

In `rust/crates/polymarket-cockpit-tui/src/state.rs`, update `cockpit_tabs_are_operator_surfaces()` expected labels to:

```rust
assert_eq!(
    labels,
    vec!["Live", "Systems", "Market", "Volatility", "Outcomes", "Logs"]
);
```

- [ ] **Step 2: Write failing compact probability model test**

In `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`, add:

```rust
#[test]
fn compact_probability_table_shows_four_contracts_without_status_row() {
    let app = AppState {
        runtime_probabilities: Some(RuntimeProbabilities {
            ok: false,
            state: "NOWCAST".to_string(),
            generated_at: "2026-06-07T16:00:00Z".to_string(),
            cached: false,
            rows: vec![
                probability_row("BTC 5m UP", 0.61, "ensemble-v1", 80_000),
                probability_row("BTC 5m DOWN", 0.39, "ensemble-v1", 80_000),
                probability_row("ETH 5m UP", 0.54, "ensemble-v1", 80_000),
                probability_row("ETH 5m DOWN", 0.46, "ensemble-v1", 80_000),
            ],
            error: Some("nowcast stale".to_string()),
            errors: vec!["temporary".to_string()],
        }),
        ..Default::default()
    };

    let table = compact_probability_table(&app);

    assert_eq!(table.headers, vec!["Contract", "p_finish", "p_no_touch", "Paths", "Model"]);
    assert_eq!(table.rows.len(), 4);
    assert_eq!(table.rows[0][0], "BTC 5m UP");
    assert_eq!(table.rows[0][3], "80000");
    assert_eq!(table.rows[0][4], "ensemble-v1");
    assert!(table.rows.iter().all(|row| !row[0].starts_with("probability ")));
}
```

Add this helper inside the test module:

```rust
fn probability_row(
    contract: &str,
    p_finish: f64,
    model_version: &str,
    path_count: u64,
) -> RuntimeProbabilityRow {
    RuntimeProbabilityRow {
        contract: contract.to_string(),
        p_finish,
        p_no_touch: 0.25,
        z_path: 0.42,
        sigma_tau: 0.0123,
        age_ms: 850,
        flags: vec!["OK".to_string()],
        decision_hint: Some("READ_ONLY".to_string()),
        edge_after_costs: None,
        required_edge: None,
        skip_reasons: Vec::new(),
        model_version: Some(model_version.to_string()),
        generator_version: Some("four-generator-ensemble-v1".to_string()),
        path_count: Some(path_count),
        prior_fragment_generators: vec![
            "empirical_conditional".to_string(),
            "block_bootstrap".to_string(),
            "filtered_historical".to_string(),
            "stress_overlay".to_string(),
        ],
    }
}
```

- [ ] **Step 3: Run TUI tests and verify they fail**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui cockpit_tabs_are_operator_surfaces compact_probability_table_shows_four_contracts_without_status_row
```

Expected: fail because `MainTab::Probability`, compact table, and new row fields do not exist together.

- [ ] **Step 4: Extend `RuntimeProbabilityRow`**

In `rust/crates/polymarket-cockpit-tui/src/status.rs`, append these fields to `RuntimeProbabilityRow`:

```rust
#[serde(default)]
pub model_version: Option<String>,
#[serde(default)]
pub generator_version: Option<String>,
#[serde(default)]
pub path_count: Option<u64>,
#[serde(default)]
pub prior_fragment_generators: Vec<String>,
```

Update all existing test fixtures constructing `RuntimeProbabilityRow` to include:

```rust
model_version: None,
generator_version: None,
path_count: None,
prior_fragment_generators: Vec::new(),
```

- [ ] **Step 5: Remove the Probability tab**

In `state.rs`, remove `Probability` from `MainTab`, `MainTab::all()`, and `label()`.

In `render/mod.rs`, delete:

```rust
MainTab::Probability => {
    probability::render(frame, body.primary, app);
    systems::render(frame, body.secondary, app);
}
```

- [ ] **Step 6: Add compact probability table helpers**

In `render/probability.rs`, add:

```rust
pub fn compact_probability_header_labels() -> [&'static str; 5] {
    ["Contract", "p_finish", "p_no_touch", "Paths", "Model"]
}

pub fn compact_probability_table(app: &AppState) -> ProbabilityTableModel {
    let rows = app
        .runtime_probabilities
        .as_ref()
        .map(|probabilities| {
            probabilities
                .rows
                .iter()
                .map(|row| {
                    vec![
                        row.contract.clone(),
                        format_probability(row.p_finish),
                        format_probability(row.p_no_touch),
                        row.path_count
                            .map(|value| value.to_string())
                            .unwrap_or_else(|| "-".to_string()),
                        row.model_version
                            .clone()
                            .unwrap_or_else(|| "-".to_string()),
                    ]
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    ProbabilityTableModel {
        headers: compact_probability_header_labels().to_vec(),
        rows: if rows.is_empty() {
            vec![vec![
                "probability pending".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
                "-".to_string(),
            ]]
        } else {
            rows
        },
    }
}

pub fn render_compact(frame: &mut Frame<'_>, area: Rect, app: &AppState) {
    let model = compact_probability_table(app);
    let rows = model
        .rows
        .into_iter()
        .map(|row| Row::new(row.into_iter().map(Cell::from).collect::<Vec<_>>()))
        .collect::<Vec<_>>();
    let table = Table::new(
        rows,
        [
            Constraint::Length(18),
            Constraint::Length(10),
            Constraint::Length(12),
            Constraint::Length(10),
            Constraint::Min(12),
        ],
    )
    .header(Row::new(model.headers).style(Style::default().fg(Color::Cyan)))
    .block(Block::bordered().title("Contract Probabilities"));

    frame.render_widget(table, area);
}
```

- [ ] **Step 7: Render compact probabilities below the Market book**

In `render/market.rs`, add `probability` to the import:

```rust
render::{orderbook, probability},
```

Replace the lower render area inside `render()`:

```rust
let [book_area, probabilities_area] = Layout::default()
    .direction(Direction::Vertical)
    .constraints([Constraint::Min(8), Constraint::Length(8)])
    .areas(orderbook_area);
```

Change:

```rust
orderbook::render(frame, orderbook_area, app);
```

to:

```rust
orderbook::render(frame, book_area, app);
probability::render_compact(frame, probabilities_area, app);
```

- [ ] **Step 8: Run focused TUI tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui cockpit_tabs_are_operator_surfaces compact_probability_table_shows_four_contracts_without_status_row
```

Expected: pass.

- [ ] **Step 9: Run all TUI crate tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui
```

Expected: pass.

- [ ] **Step 10: Commit Task 2**

```bash
git add rust/crates/polymarket-cockpit-tui/src/state.rs rust/crates/polymarket-cockpit-tui/src/render/mod.rs rust/crates/polymarket-cockpit-tui/src/status.rs rust/crates/polymarket-cockpit-tui/src/render/probability.rs rust/crates/polymarket-cockpit-tui/src/render/market.rs
git commit -m "tui: show contract probabilities in market tab"
```

---

### Task 3: Suppress Transient TUI Live Stream Read Errors

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`

- [ ] **Step 1: Write failing transient stream error test**

In the `event_loop.rs` test module, add:

```rust
#[test]
fn stream_read_errors_do_not_surface_as_runtime_errors() {
    let update = runtime_update_from_stream_error("read live stream: connection reset");

    assert_eq!(update.error, None);
    assert!(update.status.is_none());
    assert!(update.monitor.is_none());
}
```

- [ ] **Step 2: Run test and verify it fails**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui stream_read_errors_do_not_surface_as_runtime_errors
```

Expected: fail because `runtime_update_from_stream_error()` does not exist.

- [ ] **Step 3: Add transient stream helper**

In `event_loop.rs`, near `runtime_update_from_live()`, add:

```rust
fn runtime_update_from_stream_error(error: &str) -> RuntimeUpdate {
    let normalized = error.to_ascii_lowercase();
    let transient = normalized.contains("read live stream")
        || normalized.contains("live stream closed")
        || normalized.contains("connection reset")
        || normalized.contains("unexpected eof");

    RuntimeUpdate {
        status: None,
        gates: None,
        monitor: None,
        volatility: None,
        probabilities: None,
        outcomes: None,
        display_lag: None,
        error: if transient {
            None
        } else {
            Some(format!("stream: {error}"))
        },
    }
}
```

- [ ] **Step 4: Use helper in `RuntimeLiveTask::spawn`**

Replace the stream error send block with:

```rust
let stream_result = stream_runtime_updates(&client, poll_interval_ms, &runtime_tx).await;
if let Err(error) = stream_result {
    let update = runtime_update_from_stream_error(&error.to_string());
    if update.error.is_some() && runtime_tx.send(update).is_err() {
        break;
    }
}
```

Keep the existing `poll_runtime(&client).await` fallback after the stream attempt.

- [ ] **Step 5: Run focused test**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui stream_read_errors_do_not_surface_as_runtime_errors
```

Expected: pass.

- [ ] **Step 6: Run event loop tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui event_loop
```

Expected: pass.

- [ ] **Step 7: Commit Task 3**

```bash
git add rust/crates/polymarket-cockpit-tui/src/event_loop.rs
git commit -m "tui: ignore transient live stream disconnects"
```

---

### Task 4: Attach Ensemble Simulation Preview and Raise Path Budget

**Files:**
- Modify: `src/polymarket_engine/probability/ensemble_runtime.py`
- Modify: `src/polymarket_engine/probability/gpu_worker.py`
- Modify: `deploy/collector/.env.example`
- Modify: `deploy/collector/docker-compose.yml`
- Modify: `deploy/gpu/gpu-probability-entrypoint.sh`
- Modify: `tests/probability/test_ensemble_runtime.py`
- Modify: `tests/probability/test_gpu_worker.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/scripts/test_deploy_script.py`

- [ ] **Step 1: Write failing ensemble preview test**

In `tests/probability/test_ensemble_runtime.py`, add:

```python
def test_run_four_generator_ensemble_attaches_stratified_simulation_preview(
    monkeypatch,
) -> None:
    from polymarket_engine.probability import ensemble_runtime

    monkeypatch.setattr(ensemble_runtime, "run_generator_suite", _suite)

    output = ensemble_runtime.run_four_generator_ensemble(
        _probability_input(),
        path_count=20,
        steps=1,
        seed=17,
        history_fragments=((100.0, 101.0),),
    )

    preview = output.diagnostics["simulation_preview"]
    sampled_paths = preview["sampled_paths"]

    assert preview["generator_count"] == 4
    assert preview["path_count"] == 80
    assert len(sampled_paths) == 64
    assert {path["generator_id"] for path in sampled_paths} == {
        "empirical_conditional",
        "block_bootstrap",
        "filtered_historical",
        "stress_overlay",
    }
    assert all(len(path["points"]) == 2 for path in sampled_paths)
```

- [ ] **Step 2: Update path budget tests to target 320k**

In `tests/probability/test_gpu_worker.py`, update `test_worker_budget_caps_paths_per_runtime_input()`:

```python
def test_worker_budget_caps_paths_per_runtime_input() -> None:
    budget = ProbabilityWorkerBudget(max_total_paths=320_000)

    assert _path_budget_per_input(input_count=4, budget=budget) == 80_000
    assert _clamp_path_count(80_000, path_budget_per_input=80_000) == (
        80_000,
        False,
    )
    assert _clamp_path_count(250_000, path_budget_per_input=80_000) == (
        80_000,
        True,
    )
```

Update script/docs tests expecting `40000` to expect `320000`.

- [ ] **Step 3: Run focused Python tests and verify failures**

Run:

```bash
uv run pytest tests/probability/test_ensemble_runtime.py::test_run_four_generator_ensemble_attaches_stratified_simulation_preview tests/probability/test_gpu_worker.py::test_worker_budget_caps_paths_per_runtime_input tests/scripts/test_deploy_script.py::test_pc_deploy_script_runs_prebuilt_deploy_gate_with_pc_cadence -q
```

Expected: fail because ensemble diagnostics do not include `simulation_preview` and defaults still use `40000`.

- [ ] **Step 4: Add preview constants and helper in `ensemble_runtime.py`**

Add near the existing constants:

```python
ENSEMBLE_PREVIEW_PATH_LIMIT = 64
ENSEMBLE_PREVIEW_POINT_LIMIT = 48
```

Add helper functions:

```python
def _ensemble_simulation_preview(
    probability_input: ProbabilityInput,
    results: Sequence[PathSimulationResult],
    *,
    path_limit: int = ENSEMBLE_PREVIEW_PATH_LIMIT,
    point_limit: int = ENSEMBLE_PREVIEW_POINT_LIMIT,
) -> dict[str, Any]:
    generator_count = len(results)
    if generator_count <= 0:
        raise ValueError("results must be non-empty")
    per_generator_limit = max(1, path_limit // generator_count)
    sampled_paths: list[dict[str, Any]] = []
    terminal_win_count = 0
    no_touch_win_count = 0
    total_path_count = 0

    for result in results:
        total_path_count += len(result.paths)
        terminal_win_count += sum(1 for win in result.terminal_wins if win)
        no_touch_win_count += sum(1 for win in result.no_touch_survivals if win)
        path_indices = _evenly_spaced_indices(
            len(result.paths),
            min(per_generator_limit, len(result.paths)),
        )
        for path_index in path_indices:
            points = _sampled_points(result.paths[path_index], point_limit=point_limit)
            sampled_paths.append(
                {
                    "index": f"{result.generator_id}:{path_index}",
                    "generator_id": result.generator_id,
                    "terminal_win": bool(result.terminal_wins[path_index]),
                    "no_touch_win": bool(result.no_touch_survivals[path_index]),
                    "points": points,
                }
            )

    return {
        "path_count": total_path_count,
        "generator_count": generator_count,
        "steps": len(results[0].paths[0]) - 1,
        "start_price": probability_input.settlement_price,
        "threshold": probability_input.threshold,
        "comparison_operator": probability_input.comparison_operator,
        "terminal_win_count": terminal_win_count,
        "no_touch_win_count": no_touch_win_count,
        "sampled_paths": sampled_paths[:path_limit],
        "terminal_histogram": [],
    }


def _evenly_spaced_indices(count: int, sample_count: int) -> tuple[int, ...]:
    if count <= 0 or sample_count <= 0:
        return ()
    if sample_count >= count:
        return tuple(range(count))
    return tuple(round(index * (count - 1) / (sample_count - 1)) for index in range(sample_count))


def _sampled_points(path: Sequence[float], *, point_limit: int) -> list[float]:
    indices = _evenly_spaced_indices(len(path), min(point_limit, len(path)))
    return [float(path[index]) for index in indices]
```

- [ ] **Step 5: Attach preview in `run_four_generator_ensemble()`**

Add this to `diagnostics`:

```python
"simulation_preview": _ensemble_simulation_preview(probability_input, results),
```

- [ ] **Step 6: Raise worker defaults**

In `gpu_worker.py`, change:

```python
DEFAULT_MAX_TOTAL_PATHS = 320_000
```

In `deploy/collector/.env.example`, change:

```bash
POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS=320000
POLYMARKET_ENSEMBLE_PREVIEW_PATHS=64
POLYMARKET_ENSEMBLE_PREVIEW_POINTS=48
```

In `deploy/collector/docker-compose.yml`, change the worker environment default:

```yaml
POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS: ${POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS:-320000}
POLYMARKET_ENSEMBLE_PREVIEW_PATHS: ${POLYMARKET_ENSEMBLE_PREVIEW_PATHS:-64}
POLYMARKET_ENSEMBLE_PREVIEW_POINTS: ${POLYMARKET_ENSEMBLE_PREVIEW_POINTS:-48}
```

In `deploy/gpu/gpu-probability-entrypoint.sh`, change:

```bash
MAX_TOTAL_PATHS="${POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS:-320000}"
```

and export:

```bash
export POLYMARKET_ENSEMBLE_PREVIEW_PATHS="${POLYMARKET_ENSEMBLE_PREVIEW_PATHS:-64}"
export POLYMARKET_ENSEMBLE_PREVIEW_POINTS="${POLYMARKET_ENSEMBLE_PREVIEW_POINTS:-48}"
```

- [ ] **Step 7: Run focused Python tests**

Run:

```bash
uv run pytest tests/probability/test_ensemble_runtime.py tests/probability/test_gpu_worker.py tests/scripts/test_deploy_script.py tests/test_cli.py -q
```

Expected: pass.

- [ ] **Step 8: Commit Task 4**

```bash
git add src/polymarket_engine/probability/ensemble_runtime.py src/polymarket_engine/probability/gpu_worker.py deploy/collector/.env.example deploy/collector/docker-compose.yml deploy/gpu/gpu-probability-entrypoint.sh tests/probability/test_ensemble_runtime.py tests/probability/test_gpu_worker.py tests/scripts/test_deploy_script.py tests/test_cli.py
git commit -m "probability: preview ensemble paths and raise path budget"
```

---

### Task 5: Browser Ensemble Path Fan and Preview Retention

**Files:**
- Modify: `ui/src/App.tsx`
- Modify: `ui/src/probabilityRows.ts`
- Modify: `ui/src/styles.css`
- Modify: `tests/ui/probability_value_test.ts`
- Modify: `tests/ui/probability_rows_test.ts`

- [ ] **Step 1: Write failing helper tests**

In `tests/ui/probability_value_test.ts`, add:

```typescript
assert.deepEqual(
  generatorBreakdownRows({
    path_count: 80_000,
    simulation_preview: {
      generator_count: 4,
      sampled_paths: new Array(64).fill({
        generator_id: "empirical_conditional",
        points: [1, 2, 3],
      }),
    },
    generator_summary: {
      empirical_conditional: { p_finish: 0.61, p_no_touch: 0.55, weight: 0.4, sparse: false },
      block_bootstrap: { p_finish: 0.58, p_no_touch: 0.52, weight: 0.25, sparse: false },
      filtered_historical: { p_finish: 0.63, p_no_touch: 0.57, weight: 0.25, sparse: false },
      stress_overlay: { p_finish: 0.53, p_no_touch: 0.49, weight: 0.1, sparse: false },
    },
  }).map((row) => row.id),
  ["empirical_conditional", "block_bootstrap", "filtered_historical", "stress_overlay"],
);
```

In `tests/ui/probability_rows_test.ts`, add a preview-retention case:

```typescript
const retainedAcrossFreshAsof = mergeGraphableProbabilityPayloadRows(
  {
    ok: true,
    state: "OK",
    rows: [
      {
        contract_id: "btc-window-up",
        asset: "BTC",
        side: "UP",
        start_ts: "2026-06-05T13:20:00Z",
        expiry_ts: "2026-06-05T13:25:00Z",
        asof_ts: "2026-06-05T13:20:01Z",
        generated_at: "2026-06-05T13:20:01Z",
        valid_until: "2026-06-05T13:20:31Z",
        p_finish: 0.61,
        simulation_preview: preview,
      },
    ],
  },
  {
    ok: true,
    state: "OK",
    rows: [
      {
        contract_id: "btc-window-up",
        asset: "BTC",
        side: "UP",
        start_ts: "2026-06-05T13:20:00Z",
        expiry_ts: "2026-06-05T13:25:00Z",
        asof_ts: "2026-06-05T13:20:05Z",
        generated_at: "2026-06-05T13:20:05Z",
        valid_until: "2026-06-05T13:20:35Z",
        p_finish: 0.62,
      },
    ],
  },
  nowMs,
);

assert.deepEqual(retainedAcrossFreshAsof.rows?.[0]?.simulation_preview, preview);
assert.equal(retainedAcrossFreshAsof.rows?.[0]?.p_finish, 0.62);
```

- [ ] **Step 2: Run UI helper tests and verify they fail**

Run:

```bash
uv run pytest tests/ui/test_probability_rows_helper.py::test_probability_value_helper_handles_p_hat_and_path_metadata tests/ui/test_probability_rows_helper.py::test_probability_row_filter_helper_handles_partial_payloads -q
```

Expected: fail until preview retention and optional generator IDs are handled.

- [ ] **Step 3: Extend preview types**

In `ui/src/App.tsx`, update `SimulationPath`:

```typescript
type SimulationPath = {
  index: number | string;
  generator_id?: string;
  terminal_win: boolean;
  no_touch_win: boolean;
  points: number[];
};
```

In `parseSimulationPreview()`, preserve `generator_id` when present:

```typescript
generator_id: typeof path.generator_id === "string" ? path.generator_id : undefined,
```

- [ ] **Step 4: Render generator-aware path classes and legend**

In `MonteCarloCanvas`, compute generator rows:

```typescript
const generators = generatorBreakdownRows(row);
```

Change path class generation to:

```typescript
className={compactList([
  "mc-path",
  path.terminalWin ? "path-win" : "path-loss",
  path.generatorId ? `generator-${generatorTone(path.generatorId)}` : undefined,
])}
```

Update `buildPathGeometry()` to return `generatorId`:

```typescript
return { d, index: path.index, terminalWin: path.terminal_win, generatorId: path.generator_id };
```

Add a legend below the SVG:

```tsx
{generators.length > 0 ? (
  <div className="ensemble-legend" aria-label="Four-generator ensemble weights">
    {generators.map((generator) => (
      <span className={`ensemble-chip generator-${generatorTone(generator.id)}`} key={generator.id}>
        <strong>{shortGeneratorLabel(generator.id)}</strong>
        <small>{formatProbability(generator.weight)}</small>
      </span>
    ))}
  </div>
) : null}
```

Add helpers:

```typescript
function generatorTone(value: string) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

function shortGeneratorLabel(value: string) {
  switch (value) {
    case "empirical_conditional":
      return "Empirical";
    case "block_bootstrap":
      return "Bootstrap";
    case "filtered_historical":
      return "Filtered";
    case "stress_overlay":
      return "Stress";
    default:
      return sanitizeOperatorLabel(value);
  }
}
```

- [ ] **Step 5: Loosen preview retention**

In `mergeProbabilityPreviews()`, remove the strict same-`asof_ts` requirement and require same contract/window via `probabilityPreviewKeys()` instead:

```typescript
const previousRow = probabilityPreviewKeys(row)
  .map((key) => previousRowsByKey.get(key))
  .find((candidate) => candidate?.simulation_preview);
```

Keep the returned row current:

```typescript
return {
  ...row,
  simulation_preview: previousRow.simulation_preview,
};
```

- [ ] **Step 6: Add CSS**

In `ui/src/styles.css`, add this variable inside the existing `:root` block:

```css
--border-muted: rgba(148, 163, 184, 0.35);
```

Then add:

```css
.ensemble-legend {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 6px;
  margin-top: 8px;
}

.ensemble-chip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
  border: 1px solid var(--border-muted);
  border-radius: 6px;
  padding: 5px 7px;
  font-size: 0.72rem;
}

.ensemble-chip strong,
.ensemble-chip small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mc-path.generator-empirical-conditional {
  stroke: #4f8cff;
}

.mc-path.generator-block-bootstrap {
  stroke: #00a878;
}

.mc-path.generator-filtered-historical {
  stroke: #d89100;
}

.mc-path.generator-stress-overlay {
  stroke: #d64f5f;
}
```

- [ ] **Step 7: Run UI tests/build**

Run:

```bash
uv run pytest tests/ui/test_probability_rows_helper.py::test_probability_value_helper_handles_p_hat_and_path_metadata tests/ui/test_probability_rows_helper.py::test_probability_row_filter_helper_handles_partial_payloads -q
npm --prefix ui run build
```

Expected: pass.

- [ ] **Step 8: Commit Task 5**

```bash
git add ui/src/App.tsx ui/src/probabilityRows.ts ui/src/styles.css tests/ui/probability_value_test.ts tests/ui/probability_rows_test.ts
git commit -m "ui: render ensemble path previews"
```

---

### Task 6: Merge to Main, Push, Clean Remote Branches, and Deploy THEPC

**Files:**
- No source file edits expected.

- [ ] **Step 1: Run focused verification**

Run:

```bash
uv run pytest tests/probability/test_ensemble_runtime.py tests/probability/test_gpu_worker.py tests/scripts/test_deploy_script.py tests/test_cli.py tests/ui/test_probability_rows_helper.py -q
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui
npm --prefix ui run build
```

Expected: all pass.

- [ ] **Step 2: Switch to `main` and merge implementation commits**

Run:

```bash
git fetch origin main
git switch main
git pull --ff-only origin main
git merge --ff-only codex/main-deploy-tui-browser-ensemble
```

If fast-forward is impossible because the executor committed directly on `main`, use:

```bash
git log --oneline --decorate -5
git status --short --branch
```

Then either cherry-pick the implementation commits onto `main` or merge with a normal merge commit. Do not force-push.

- [ ] **Step 3: Push `main`**

Run:

```bash
git push origin main
```

Expected: `origin/main` contains the implementation commit(s).

- [ ] **Step 4: Delete remote `codex/*` branches after main push**

Run:

```bash
git ls-remote --heads origin 'refs/heads/codex/*'
```

For every listed branch that is now obsolete because `main` is the only deploy branch, run:

```bash
git push origin --delete <branch-name>
```

Do not delete non-`codex/*` branches in this task.

- [ ] **Step 5: Deploy THEPC from GitHub main**

Run from `/Users/goon/polymarket`:

```bash
PC_DEPLOY_MODE=remote-build POLYMARKET_DEPLOY_REF=HEAD ./scripts/deploy_pc.sh
```

Expected:

- THEPC WSL can SSH to GitHub.
- THEPC fetches `origin/main`.
- THEPC checks out the exact pushed SHA.
- Images build or refresh on THEPC.
- Runtime smoke passes for `/health`, `/api/runtime/live`, `/api/runtime/probabilities`, `/api/runtime/outcomes`, and SSE.

- [ ] **Step 6: Verify live probability budget and ensemble preview**

Run:

```bash
ssh ender@100.72.104.49 'wsl.exe -d Ubuntu -- bash -lc "python3 - <<'\''PY'\''
import json
from pathlib import Path

payload = json.loads(Path('/home/ender/polymarket-data/live/probabilities.json').read_text())
rows = payload.get('rows') or []
budget = payload.get('budget') or {}
print('state=', payload.get('state'))
print('budget=', budget)
for row in rows[:4]:
    preview = row.get('simulation_preview') or {}
    print(row.get('contract'), row.get('model_version'), row.get('path_count'), len(preview.get('sampled_paths') or []), sorted((row.get('generator_summary') or {}).keys()))
PY"'
```

Expected:

- `budget["max_total_paths"] == 320000`
- `budget["path_budget_per_input"] >= 80000` when there are four active MC inputs
- four visible rows use `model_version == "ensemble-v1"`
- each row has `simulation_preview.sampled_paths` length up to `64`
- each row has all four generator IDs in `generator_summary`

- [ ] **Step 7: Verify browser UI manually**

Start or use the deployed UI route that serves the React build, then inspect the Runtime Monitor in a browser.

Expected:

- BTC UP, BTC DOWN, ETH UP, ETH DOWN cards are visible.
- Cards show Monte Carlo path fans, not only the snapshot bar.
- Each card has a four-generator legend.
- The selected contract panel still shows prior fragments and generator breakdown.

- [ ] **Step 8: Verify TUI manually**

Run on THEPC WSL:

```bash
/home/ender/bin/open-polymarket-tui.sh
```

Expected:

- Tabs are `Live`, `Systems`, `Market`, `Volatility`, `Outcomes`, `Logs`.
- No `Probability` tab exists.
- Market tab shows the selected book and a `Contract Probabilities` table below it.
- No repeating `runtime_error=stream: read live stream` appears during normal stream reconnects.

- [ ] **Step 9: Final status**

Run:

```bash
git status --short --branch
git branch -r
```

Expected:

- Local `main` is clean.
- `origin/main` is the deployment branch.
- No remote `codex/*` branches remain unless intentionally retained outside this task.

---

## Self-Review Notes

- Spec coverage: deploy flow, main-only GitHub state, THEPC GitHub SSH, TUI probability placement, Probability tab removal, stream error suppression, browser ensemble display, preview line increase, and backend path budget increase are all mapped to tasks.
- No placeholders: all tasks include exact paths, concrete expected code, commands, and expected outcomes.
- Type consistency: Rust probability metadata fields are optional to preserve old payload compatibility; TypeScript preview `index` becomes `number | string` because ensemble preview uses `generator_id:index` identities.
- Risk: `320000` total paths may exceed THEPC cadence with the current Python ensemble implementation. The deploy verification step must check live budget/cycle diagnostics. If cycle runtime is consistently breached, keep the code path but lower only the deployed env value after capturing evidence.
