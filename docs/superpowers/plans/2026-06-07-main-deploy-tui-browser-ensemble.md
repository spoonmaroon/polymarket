# Main Deploy, TUI Market Probabilities, and Browser Ensemble Preview Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update THEPC from GitHub `main`, keep GitHub main as the only deploy branch, move TUI probabilities into the Market tab, suppress noisy transient stream errors, and make browser Monte Carlo path fans render from real four-generator ensemble preview data.

**Architecture:** Keep the runtime read-only and paper/display-only. THEPC should fetch GitHub `main` over SSH, check out an exact pushed SHA, build/restart locally, and fail closed if live smoke checks do not see fresh four-contract ensemble rows. The probability worker should emit enough backend paths for useful probabilities while sending only a bounded sampled preview to the browser.

**Tech Stack:** Bash deploy scripts, Git/GitHub SSH, Python probability worker/runtime API, Rust Ratatui TUI, React/Vite browser UI, pytest, cargo tests, TypeScript helper tests.

---

## Current Evidence To Preserve

- GitHub `origin/main` was `46d4af0fd359803cb672db3b486ccb5901ae5fe1`.
- THEPC `/home/ender/polymarket` was `4c6cd59c968c18ce5a182d1cdd66fea8f40de2a3`, `113` commits behind and `0` commits ahead.
- THEPC was checked out on `codex/dedicated-volatility-tab-mac-launcher`, not `main`.
- THEPC Docker services were up: collector, normalizer, API, and `gpu-probability-worker`.
- THEPC GPU was visible: NVIDIA GeForce RTX 5060 Ti with 16 GB VRAM.
- THEPC probability endpoint recovered to `NOWCAST` with four rows: BTC UP, BTC DOWN, ETH UP, ETH DOWN.
- Those four live rows did not include `simulation_preview`, so the browser cannot draw Monte Carlo path lines from current status rows even when probability values exist.
- `STALE_INPUTS` was intermittent around probability input gaps; collector and normalizer freshness were OK. Treat this as a transient input construction/rollover state that should retain last good rows without becoming a TUI runtime error.

## Non-Negotiable Targets

- GitHub remote has only `main` after cleanup.
- THEPC deploy uses GitHub SSH pull/fetch, not Mac-to-PC git bundle transfer.
- THEPC deploy refuses local-only commits that are not on `origin/main`.
- TUI tab labels become `Live`, `Systems`, `Market`, `Outcomes`, `Logs`.
- TUI `Probability` tab is removed.
- Market tab shows selected book plus compact contract probabilities below the book.
- Browser shows one probability card per BTC/ETH UP/DOWN contract with path fans and four-generator context when preview data exists.
- Backend `POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS=320000` means total generator-path budget per cycle. With four active contracts and four generators, the target is about `20,000` paths per generator and `80,000` effective paths per contract.
- Browser preview defaults to at most `64` sampled paths and `48` points per contract. The browser never runs simulations.

## Subagent Split

- Subagent A: deploy script, GitHub SSH, docs, deploy tests.
- Subagent B: TUI Market probabilities and Probability tab removal.
- Subagent C: TUI transient stream error suppression.
- Subagent D: ensemble preview emission and path-budget semantics.
- Subagent E: browser path fan/legend rendering and preview retention.
- Final integrator: focused verification, push `main`, clean remote branches, deploy THEPC, verify live runtime.

---

### Task 0: Baseline And Guardrails

**Files:**
- No source edits.

- [ ] **Step 1: Confirm local main and THEPC state**

Run:

```bash
git fetch origin main --prune
git switch main
git status --short --branch
git rev-parse origin/main
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -lc 'cd /home/ender/polymarket && git rev-parse HEAD && git branch --show-current && git status --short --branch'"
```

Expected:

- Local branch is `main`.
- Local worktree is clean before source edits.
- THEPC may still be behind on the old Codex branch. Do not manually reset THEPC in this task.

- [ ] **Step 2: Confirm current tests before edits**

Run:

```bash
uv run pytest tests/probability tests/test_runtime_api.py tests/ui tests/scripts/test_check_collector_status.py tests/scripts/test_check_probability_latency.py -q
npm run build --prefix ui
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui
```

Expected: all pass before implementation starts. If an unrelated failure appears, record it in the final handoff before changing code.

---

### Task 1: GitHub SSH Main-Only THEPC Deploy

**Files:**
- Modify: `scripts/deploy_pc.sh`
- Modify: `tests/scripts/test_deploy_script.py`
- Modify: `README.md`
- Modify: `docs/SPOON_DEPLOYMENT.md`

- [ ] **Step 1: Add deploy-script tests**

In `tests/scripts/test_deploy_script.py`, add:

```python
def test_pc_deploy_fetches_exact_main_sha_from_github_ssh() -> None:
    script = (ROOT / "scripts" / "deploy_pc.sh").read_text(encoding="utf-8")

    assert 'PC_GIT_REMOTE="${PC_GIT_REMOTE:-git@github.com:AnimeWeeb9000/polymarket.git}"' in script
    assert 'PC_BRANCH="${PC_BRANCH:-main}"' in script
    assert 'git -C "$ROOT" fetch --quiet origin main' in script
    assert 'LOCAL_MAIN_SHA="$(git -C "$ROOT" rev-parse origin/main^{commit})"' in script
    assert 'origin/main is $LOCAL_MAIN_SHA but deploy ref is $FULL_SHA' in script
    assert 'git ls-remote "$PC_GIT_REMOTE" HEAD' in script
    assert 'git clone "$PC_GIT_REMOTE" "$PC_REPO"' in script
    assert 'git fetch --quiet --prune origin "$PC_BRANCH"' in script
    assert 'git checkout -B "$PC_BRANCH" "$FULL_SHA"' in script
    assert "git bundle create" not in script
    assert "PC_BUNDLE" not in script
```

In `test_compose_and_env_support_prebuilt_image_overrides()`, update the path-budget expectation to:

```python
assert "POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS=320000" in env_example
```

- [ ] **Step 2: Verify deploy tests fail**

Run:

```bash
uv run pytest tests/scripts/test_deploy_script.py -q
```

Expected: fail because `scripts/deploy_pc.sh` still uses a git bundle and defaults to `40000`.

- [ ] **Step 3: Replace bundle sync with GitHub SSH sync**

In `scripts/deploy_pc.sh`, replace the deploy defaults with:

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

Remove repository bundle creation and transfer:

Delete the `LOCAL_BUNDLE` assignment, the `PC_BUNDLE` assignment, the `git -C "$ROOT" bundle create "$LOCAL_BUNDLE.tmp" --branches --tags` call, the `mv "$LOCAL_BUNDLE.tmp" "$LOCAL_BUNDLE"` call, and the repository-transfer call that sends `"$LOCAL_BUNDLE"` to THEPC.

If `image-tar` mode still transfers image tarballs, keep that helper but rename it to:

```bash
wsl_put_artifact_file() {
  local src="$1"
  local dest="$2"
  local dest_dir
  local dest_dir_q
  local dest_q
  local dest_tmp
  local dest_tmp_q

  dest_dir="$(dirname "$dest")"
  dest_tmp="$dest.tmp.$$"
  dest_dir_q="$(shell_quote "$dest_dir")"
  dest_q="$(shell_quote "$dest")"
  dest_tmp_q="$(shell_quote "$dest_tmp")"

  ssh "$PC_HOST" "wsl.exe -d $PC_WSL_DISTRO -- bash -lc \"mkdir -p $dest_dir_q && cat > $dest_tmp_q && mv -f $dest_tmp_q $dest_q\"" < "$src"
}
```

- [ ] **Step 4: Update THEPC WSL sync block**

Inside the remote WSL heredoc, pass:

```bash
PC_GIT_REMOTE=$(shell_quote "$PC_GIT_REMOTE")
```

Use this repo sync sequence:

```bash
if ! git ls-remote "$PC_GIT_REMOTE" HEAD >/dev/null 2>&1; then
  echo "THEPC WSL cannot read $PC_GIT_REMOTE over SSH." >&2
  mkdir -p /home/ender/.ssh
  chmod 700 /home/ender/.ssh
  if [ ! -f /home/ender/.ssh/id_ed25519.pub ]; then
    ssh-keygen -t ed25519 -N "" -C "thepc-polymarket@github" -f /home/ender/.ssh/id_ed25519
  fi
  echo "Add this key to GitHub, then rerun deploy:" >&2
  cat /home/ender/.ssh/id_ed25519.pub >&2
  exit 1
fi

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
git fetch --quiet --prune origin "$PC_BRANCH"
git checkout -B "$PC_BRANCH" "$FULL_SHA"
```

- [ ] **Step 5: Document THEPC SSH requirement**

In `README.md`, add:

```markdown
THEPC deploys fetch `origin/main` over SSH from GitHub. The Windows/WSL user
must have a GitHub SSH key that can read
`git@github.com:AnimeWeeb9000/polymarket.git`. The deploy script refuses commits
that are not already present at `origin/main`.
```

In `docs/SPOON_DEPLOYMENT.md`, add:

```markdown
THEPC deploys are GitHub-pull based. The Mac pushes `main`; THEPC WSL fetches
`git@github.com:AnimeWeeb9000/polymarket.git`, checks out the exact pushed SHA,
then builds and restarts from that checkout. Do not deploy local-only commits.
```

- [ ] **Step 6: Run deploy tests**

Run:

```bash
uv run pytest tests/scripts/test_deploy_script.py -q
```

Expected: pass.

- [ ] **Step 7: Commit Task 1**

```bash
git add scripts/deploy_pc.sh tests/scripts/test_deploy_script.py README.md docs/SPOON_DEPLOYMENT.md
git commit -m "deploy: fetch THEPC from GitHub main"
```

---

### Task 2: Move TUI Probabilities Into Market And Remove Probability Tab

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/state.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/mod.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/status.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/probability.rs`
- Modify: `rust/crates/polymarket-cockpit-tui/src/render/market.rs`

- [ ] **Step 1: Update tab test first**

In `state.rs`, change `cockpit_tabs_are_operator_surfaces()` to expect:

```rust
assert_eq!(
    labels,
    vec!["Live", "Systems", "Market", "Outcomes", "Logs"]
);
```

Expected: no `Probability` tab and no separate `Volatility` tab.

- [ ] **Step 2: Add compact probability table test**

In `render/probability.rs`, add a test named:

```rust
#[test]
fn compact_probability_table_shows_contract_rows_without_status_error_row() {
    let app = AppState {
        runtime_probabilities: Some(RuntimeProbabilities {
            ok: true,
            state: "NOWCAST".to_string(),
            generated_at: "2026-06-07T21:15:27Z".to_string(),
            cached: false,
            rows: vec![
                probability_row("BTC 5m UP", 0.4729, 80_000),
                probability_row("BTC 5m DOWN", 0.4271, 80_000),
                probability_row("ETH 5m UP", 0.3700, 80_000),
                probability_row("ETH 5m DOWN", 0.5300, 80_000),
            ],
            error: Some("transient nowcast".to_string()),
            errors: vec![],
        }),
        ..Default::default()
    };

    let table = compact_probability_table(&app);

    assert_eq!(table.headers, vec!["Contract", "p", "NoTouch", "Paths", "Model"]);
    assert_eq!(table.rows.len(), 4);
    assert_eq!(table.rows[0][0], "BTC 5m UP");
    assert_eq!(table.rows[0][1], "0.473");
    assert_eq!(table.rows[0][3], "80000");
    assert!(table.rows.iter().all(|row| !row[0].starts_with("probability ")));
}
```

Add this helper in the same test module:

```rust
fn probability_row(contract: &str, p_finish: f64, effective_path_count: u64) -> RuntimeProbabilityRow {
    RuntimeProbabilityRow {
        contract: contract.to_string(),
        p_finish,
        p_no_touch: 0.25,
        z_path: 0.42,
        sigma_tau: 0.01234,
        age_ms: 850,
        flags: vec!["OK".to_string()],
        decision_hint: Some("READ_ONLY".to_string()),
        edge_after_costs: None,
        required_edge: None,
        skip_reasons: vec![],
        model_version: Some("ensemble-v1".to_string()),
        generator_version: Some("four-generator-ensemble-v1".to_string()),
        path_count: Some(effective_path_count),
        generator_count: Some(4),
    }
}
```

- [ ] **Step 3: Run focused failing TUI tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui cockpit_tabs_are_operator_surfaces compact_probability_table_shows_contract_rows_without_status_error_row
```

Expected: fail until the tab and compact table changes exist.

- [ ] **Step 4: Extend probability status row fields**

In `status.rs`, extend `RuntimeProbabilityRow` with optional fields:

```rust
#[serde(default)]
pub model_version: Option<String>,
#[serde(default)]
pub generator_version: Option<String>,
#[serde(default)]
pub path_count: Option<u64>,
#[serde(default)]
pub generator_count: Option<u64>,
```

Update existing Rust test fixtures that construct `RuntimeProbabilityRow` with:

```rust
model_version: None,
generator_version: None,
path_count: None,
generator_count: None,
```

- [ ] **Step 5: Remove `MainTab::Probability`**

In `state.rs`, remove `Probability` from:

```rust
pub enum MainTab
MainTab::all()
MainTab::label()
```

In `render/mod.rs`, remove the `MainTab::Probability` render arm. Do not reintroduce a separate `Volatility` tab.

- [ ] **Step 6: Add compact probability renderer**

In `render/probability.rs`, add:

```rust
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
                        row.path_count.map(|value| value.to_string()).unwrap_or_else(|| "-".to_string()),
                        row.model_version.clone().unwrap_or_else(|| "-".to_string()),
                    ]
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();

    ProbabilityTableModel {
        headers: vec!["Contract", "p", "NoTouch", "Paths", "Model"],
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
```

Add `render_compact()` using the same `Table` pattern already used by `render()`, with title `Contract Probabilities`.

- [ ] **Step 7: Render compact probabilities below selected book**

In `render/market.rs`, import the probability renderer:

```rust
use crate::{
    market_view,
    render::{orderbook, probability},
    state::AppState,
    status::{RuntimeOrderbookRow, RuntimeOutcomeRow, RuntimeOutcomes},
};
```

Inside `render()`, split `orderbook_area`:

```rust
let [book_area, probabilities_area] = Layout::default()
    .direction(Direction::Vertical)
    .constraints([Constraint::Min(8), Constraint::Length(8)])
    .areas(orderbook_area);
```

Replace:

```rust
orderbook::render(frame, orderbook_area, app);
```

with:

```rust
orderbook::render(frame, book_area, app);
probability::render_compact(frame, probabilities_area, app);
```

- [ ] **Step 8: Run TUI tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui
```

Expected: all TUI crate tests pass.

- [ ] **Step 9: Commit Task 2**

```bash
git add rust/crates/polymarket-cockpit-tui/src/state.rs rust/crates/polymarket-cockpit-tui/src/render/mod.rs rust/crates/polymarket-cockpit-tui/src/status.rs rust/crates/polymarket-cockpit-tui/src/render/probability.rs rust/crates/polymarket-cockpit-tui/src/render/market.rs
git commit -m "tui: move probabilities into market tab"
```

---

### Task 3: Suppress Transient TUI Stream Read Errors

**Files:**
- Modify: `rust/crates/polymarket-cockpit-tui/src/event_loop.rs`

- [ ] **Step 1: Add failing stream error tests**

In `event_loop.rs`, add:

```rust
#[test]
fn transient_live_stream_errors_do_not_become_runtime_errors() {
    let update = runtime_update_from_stream_error("read live stream: connection reset");

    assert_eq!(update.error, None);
    assert!(update.status.is_none());
    assert!(update.monitor.is_none());
}

#[test]
fn non_transient_live_stream_errors_still_surface() {
    let update = runtime_update_from_stream_error("http 500");

    assert_eq!(update.error, Some("stream: http 500".to_string()));
}
```

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui transient_live_stream_errors_do_not_become_runtime_errors non_transient_live_stream_errors_still_surface
```

Expected: fail because `runtime_update_from_stream_error()` does not exist.

- [ ] **Step 3: Add helper**

In `event_loop.rs`, near `runtime_update_from_live()`, add:

```rust
fn runtime_update_from_stream_error(error: &str) -> RuntimeUpdate {
    let normalized = error.to_ascii_lowercase();
    let transient = normalized.contains("read live stream")
        || normalized.contains("live stream closed")
        || normalized.contains("connection reset")
        || normalized.contains("unexpected eof")
        || normalized.contains("operation timed out");

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

- [ ] **Step 4: Use helper in stream loop**

Replace the current stream error send block in `RuntimeLiveTask::spawn()` with:

```rust
if let Err(error) = stream_runtime_updates(&client, poll_interval_ms, &runtime_tx).await {
    let update = runtime_update_from_stream_error(&error.to_string());
    if update.error.is_some() && runtime_tx.send(update).is_err() {
        break;
    }
}
```

Keep the existing polling fallback after stream attempts.

- [ ] **Step 5: Run TUI tests**

Run:

```bash
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui event_loop
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui
```

Expected: pass.

- [ ] **Step 6: Commit Task 3**

```bash
git add rust/crates/polymarket-cockpit-tui/src/event_loop.rs
git commit -m "tui: ignore transient live stream disconnects"
```

---

### Task 4: Emit Ensemble Simulation Preview And Fix Path Budget Semantics

**Files:**
- Modify: `src/polymarket_engine/probability/ensemble_runtime.py`
- Modify: `src/polymarket_engine/probability/gpu_worker.py`
- Modify: `deploy/collector/.env.example`
- Modify: `deploy/collector/docker-compose.yml`
- Modify: `deploy/gpu/gpu-probability-entrypoint.sh`
- Modify: `tests/probability/test_ensemble_runtime.py`
- Modify: `tests/probability/test_gpu_worker.py`
- Modify: `tests/scripts/test_deploy_script.py`

- [ ] **Step 1: Add ensemble preview test**

In `tests/probability/test_ensemble_runtime.py`, add:

```python
def test_run_four_generator_ensemble_attaches_preview_with_generator_ids(monkeypatch) -> None:
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

    assert preview["generator_count"] == 4
    assert preview["paths_per_generator"] == 20
    assert preview["path_count"] == 80
    assert len(preview["sampled_paths"]) == 64
    assert {path["generator_id"] for path in preview["sampled_paths"]} == {
        "empirical_conditional",
        "block_bootstrap",
        "filtered_historical",
        "stress_overlay",
    }
    assert all(len(path["points"]) == 2 for path in preview["sampled_paths"])
```

- [ ] **Step 2: Add budget semantics test**

In `tests/probability/test_gpu_worker.py`, replace `test_worker_budget_caps_paths_per_runtime_input()` with:

```python
def test_worker_budget_caps_total_generator_paths_for_ensemble() -> None:
    budget = ProbabilityWorkerBudget(max_total_paths=320_000, worker_mode="ensemble")

    assert _path_budget_per_input(input_count=4, budget=budget) == 20_000
    assert _clamp_path_count(80_000, path_budget_per_input=20_000) == (
        20_000,
        True,
    )
    assert _clamp_path_count(10_000, path_budget_per_input=20_000) == (
        10_000,
        False,
    )
```

Add a second test for non-ensemble mode:

```python
def test_worker_budget_keeps_single_generator_modes_divided_by_inputs_only() -> None:
    budget = ProbabilityWorkerBudget(max_total_paths=320_000, worker_mode="cuda")

    assert _path_budget_per_input(input_count=4, budget=budget) == 80_000
```

- [ ] **Step 3: Run focused failing tests**

Run:

```bash
uv run pytest tests/probability/test_ensemble_runtime.py::test_run_four_generator_ensemble_attaches_preview_with_generator_ids tests/probability/test_gpu_worker.py::test_worker_budget_caps_total_generator_paths_for_ensemble tests/probability/test_gpu_worker.py::test_worker_budget_keeps_single_generator_modes_divided_by_inputs_only -q
```

Expected: fail until preview and budget semantics are implemented.

- [ ] **Step 4: Add ensemble preview helpers**

In `ensemble_runtime.py`, add constants:

```python
ENSEMBLE_PREVIEW_PATH_LIMIT = 64
ENSEMBLE_PREVIEW_POINT_LIMIT = 48
```

Add a helper that samples evenly from each `PathSimulationResult`:

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
    paths_per_generator = len(results[0].paths)
    per_generator_limit = max(1, path_limit // generator_count)
    sampled_paths: list[dict[str, Any]] = []
    terminal_win_count = 0
    no_touch_win_count = 0
    terminal_prices: list[float] = []

    for result in results:
        terminal_win_count += sum(1 for win in result.terminal_wins if win)
        no_touch_win_count += sum(1 for win in result.no_touch_survivals if win)
        terminal_prices.extend(float(price) for price in result.terminal_prices)
        path_indices = _evenly_spaced_indices(
            len(result.paths),
            min(per_generator_limit, len(result.paths)),
        )
        for path_index in path_indices:
            sampled_paths.append(
                {
                    "index": f"{result.generator_id}:{path_index}",
                    "generator_id": result.generator_id,
                    "terminal_win": bool(result.terminal_wins[path_index]),
                    "no_touch_win": bool(result.no_touch_survivals[path_index]),
                    "points": _sampled_points(result.paths[path_index], point_limit=point_limit),
                }
            )

    return {
        "path_count": sum(len(result.paths) for result in results),
        "paths_per_generator": paths_per_generator,
        "generator_count": generator_count,
        "steps": len(results[0].paths[0]) - 1,
        "start_price": probability_input.settlement_price,
        "threshold": probability_input.threshold,
        "comparison_operator": probability_input.comparison_operator,
        "terminal_win_count": terminal_win_count,
        "no_touch_win_count": no_touch_win_count,
        "sampled_paths": sampled_paths[:path_limit],
        "terminal_histogram": _terminal_histogram(tuple(terminal_prices)),
    }
```

Add these helpers in `ensemble_runtime.py`:

```python
def _evenly_spaced_indices(length: int, count: int) -> tuple[int, ...]:
    if count <= 0:
        return ()
    if count >= length:
        return tuple(range(length))
    if count == 1:
        return (0,)
    return tuple(round(index * (length - 1) / (count - 1)) for index in range(count))


def _sampled_points(path: Sequence[float], *, point_limit: int) -> list[float]:
    indices = _evenly_spaced_indices(len(path), min(point_limit, len(path)))
    return [float(path[index]) for index in indices]


def _terminal_histogram(terminal_prices: tuple[float, ...]) -> list[dict[str, Any]]:
    lower_bound = min(terminal_prices)
    upper_bound = max(terminal_prices)
    if lower_bound == upper_bound:
        return [{"lower": lower_bound, "upper": upper_bound, "count": len(terminal_prices)}]

    bin_count = min(16, len(terminal_prices))
    width = (upper_bound - lower_bound) / bin_count
    counts = [0] * bin_count
    for price in terminal_prices:
        index = min(bin_count - 1, int((price - lower_bound) / width))
        counts[index] += 1
    return [
        {
            "lower": lower_bound + width * index,
            "upper": lower_bound + width * (index + 1),
            "count": count,
        }
        for index, count in enumerate(counts)
    ]
```

- [ ] **Step 5: Attach preview to ensemble diagnostics**

In `run_four_generator_ensemble()`, add:

```python
"path_count": int(path_count * len(results)),
"paths_per_generator": int(path_count),
"generator_count": len(results),
"simulation_preview": _ensemble_simulation_preview(probability_input, results),
```

Keep `generator_summary`, `generator_runs`, `effective_weights`, and `prior_fragment_generators`.

- [ ] **Step 6: Fix worker budget semantics**

In `gpu_worker.py`, set:

```python
DEFAULT_MAX_TOTAL_PATHS = 320_000
```

Change `_path_budget_per_input()` so ensemble mode divides by active inputs and four generators:

```python
def _path_budget_per_input(
    *,
    input_count: int,
    budget: ProbabilityWorkerBudget,
) -> int:
    if input_count <= 0:
        return 0
    generator_count = 4 if budget.worker_mode == "ensemble" else 1
    return max(1, budget.max_total_paths // (input_count * generator_count))
```

When writing row diagnostics, preserve both effective and per-generator counts from `output.diagnostics`:

```python
row["path_count"] = int(output.diagnostics.get("path_count", path_count))
row["paths_per_generator"] = int(output.diagnostics.get("paths_per_generator", path_count))
row["generator_count"] = int(output.diagnostics.get("generator_count", 4))
```

- [ ] **Step 7: Update deploy defaults**

In `deploy/collector/.env.example`:

```bash
POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS=320000
```

In `deploy/collector/docker-compose.yml`:

```yaml
POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS: ${POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS:-320000}
```

In `deploy/gpu/gpu-probability-entrypoint.sh`:

```bash
MAX_TOTAL_PATHS="${POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS:-320000}"
```

- [ ] **Step 8: Run focused probability tests**

Run:

```bash
uv run pytest tests/probability/test_ensemble_runtime.py tests/probability/test_gpu_worker.py tests/scripts/test_deploy_script.py -q
```

Expected: pass.

- [ ] **Step 9: Commit Task 4**

```bash
git add src/polymarket_engine/probability/ensemble_runtime.py src/polymarket_engine/probability/gpu_worker.py deploy/collector/.env.example deploy/collector/docker-compose.yml deploy/gpu/gpu-probability-entrypoint.sh tests/probability/test_ensemble_runtime.py tests/probability/test_gpu_worker.py tests/scripts/test_deploy_script.py
git commit -m "probability: emit ensemble previews and raise path budget"
```

---

### Task 5: Browser Path Fans And Generator Context

**Files:**
- Modify: `ui/src/App.tsx`
- Modify: `ui/src/probabilityRows.ts`
- Modify: `ui/src/styles.css`
- Modify: `tests/ui/probability_rows_test.ts`
- Modify: `tests/ui/probability_value_test.ts`

- [ ] **Step 1: Add preview-retention test**

In `tests/ui/probability_rows_test.ts`, add a case where the previous row has `simulation_preview`, the new row has the same contract/window with a newer `asof_ts`, and the merged result keeps the preview while using the new `p_finish`:

```typescript
const preview = {
  sampled_paths: [{ index: "empirical_conditional:0", generator_id: "empirical_conditional", points: [1, 2, 3] }],
  terminal_histogram: [],
};

const retainedPreviewAcrossAsof = mergeGraphableProbabilityPayloadRows(
  {
    ok: true,
    state: "OK",
    rows: [{
      contract_id: "btc-window-up",
      asset: "BTC",
      side: "UP",
      start_ts: "2026-06-05T13:20:00Z",
      expiry_ts: "2026-06-05T13:25:00Z",
      asof_ts: "2026-06-05T13:20:01Z",
      p_finish: 0.61,
      simulation_preview: preview,
    }],
  },
  {
    ok: true,
    state: "OK",
    rows: [{
      contract_id: "btc-window-up",
      asset: "BTC",
      side: "UP",
      start_ts: "2026-06-05T13:20:00Z",
      expiry_ts: "2026-06-05T13:25:00Z",
      asof_ts: "2026-06-05T13:20:05Z",
      p_finish: 0.62,
    }],
  },
  nowMs,
);

assert.deepEqual(retainedPreviewAcrossAsof.rows?.[0]?.simulation_preview, preview);
assert.equal(retainedPreviewAcrossAsof.rows?.[0]?.p_finish, 0.62);
```

- [ ] **Step 2: Add four-generator breakdown test**

In `tests/ui/probability_value_test.ts`, add:

```typescript
assert.deepEqual(
  generatorBreakdownRows({
    path_count: 80_000,
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

This locks the browser-visible generator order and proves all four ensemble members can be surfaced.

- [ ] **Step 3: Run UI helper tests and verify failure**

Run:

```bash
uv run pytest tests/ui/test_probability_rows_helper.py -q
```

Expected: fail if preview retention or four-generator breakdown handling is missing.

- [ ] **Step 4: Preserve `generator_id` in browser preview paths**

In `ui/src/App.tsx`, update the preview path type:

```typescript
type SimulationPath = {
  index: number | string;
  generator_id?: string;
  terminal_win: boolean;
  no_touch_win: boolean;
  points: number[];
};
```

In `parseSimulationPreview()`, keep:

```typescript
generator_id: typeof path.generator_id === "string" ? path.generator_id : undefined,
```

- [ ] **Step 5: Render generator legend and path classes**

In `MonteCarloCanvas`, build generator rows from the selected probability row and render a compact four-generator legend below the SVG. Use these labels:

```typescript
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

When building SVG path geometry, carry `generatorId` through so CSS can color by generator.

- [ ] **Step 6: Add CSS**

In `ui/src/styles.css`, add:

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
  border: 1px solid rgba(148, 163, 184, 0.35);
  border-radius: 6px;
  padding: 5px 7px;
  font-size: 0.72rem;
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

- [ ] **Step 7: Run UI tests and build**

Run:

```bash
uv run pytest tests/ui/test_probability_rows_helper.py -q
npm run build --prefix ui
```

Expected: pass.

- [ ] **Step 8: Commit Task 5**

```bash
git add ui/src/App.tsx ui/src/probabilityRows.ts ui/src/styles.css tests/ui/probability_rows_test.ts tests/ui/probability_value_test.ts
git commit -m "ui: show ensemble path previews"
```

---

### Task 6: Final Verification, Push Main, Clean Branches, Deploy THEPC

**Files:**
- No source edits unless verification exposes a bug.

- [ ] **Step 1: Run focused verification**

Run:

```bash
uv run pytest tests/probability/test_ensemble_runtime.py tests/probability/test_gpu_worker.py tests/scripts/test_deploy_script.py tests/test_runtime_api.py tests/ui -q
cargo test -q --manifest-path rust/Cargo.toml -p polymarket-cockpit-tui
npm run build --prefix ui
```

Expected: all pass.

- [ ] **Step 2: Push only `main`**

Run:

```bash
git status --short --branch
git fetch origin main --prune
git push origin main
```

Expected: `origin/main` contains the implementation commits.

- [ ] **Step 3: Delete remote Codex branches**

Run:

```bash
git ls-remote --heads origin 'refs/heads/codex/*'
```

For every listed branch, run:

```bash
git push origin --delete <branch-name>
```

Expected: `origin/main` is the only remote branch.

- [ ] **Step 4: Deploy THEPC from GitHub main**

Run:

```bash
PC_DEPLOY_MODE=remote-build POLYMARKET_DEPLOY_REF=HEAD ./scripts/deploy_pc.sh
```

Expected:

- THEPC WSL can `git ls-remote git@github.com:AnimeWeeb9000/polymarket.git HEAD`.
- THEPC checks out branch `main` at the exact pushed SHA.
- Docker images build/restart on THEPC.
- Deploy smoke passes `/health`, `/api/runtime/live`, `/api/runtime/probabilities`, `/api/runtime/outcomes`, and live SSE.

- [ ] **Step 5: Verify live THEPC probability rows**

Run:

```bash
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -lc 'python3 - <<\"PY\"
import json
from pathlib import Path

payload = json.loads(Path(\"/home/ender/polymarket-data/live/probabilities.json\").read_text())
rows = payload.get(\"rows\") or []
print(\"state\", payload.get(\"state\"))
print(\"budget\", payload.get(\"budget\"))
for row in rows[:4]:
    preview = row.get(\"simulation_preview\") or {}
    generators = sorted((row.get(\"generator_summary\") or {}).keys())
    print(row.get(\"contract\"), row.get(\"model_version\"), row.get(\"path_count\"), row.get(\"paths_per_generator\"), row.get(\"generator_count\"), len(preview.get(\"sampled_paths\") or []), generators)
PY'"
```

Expected:

- State is `OK` or `NOWCAST`.
- Four visible rows exist for BTC UP, BTC DOWN, ETH UP, ETH DOWN.
- Each row has `model_version == "ensemble-v1"`.
- Each row has `generator_count == 4`.
- Each row has all four generator IDs in `generator_summary`.
- Each row has non-empty `simulation_preview.sampled_paths`.
- Budget diagnostics show `max_total_paths == 320000`.

- [ ] **Step 6: Verify TUI manually**

Run on THEPC:

```bash
/home/ender/bin/open-polymarket-tui.sh
```

Expected:

- Tabs are `Live`, `Systems`, `Market`, `Outcomes`, `Logs`.
- No `Probability` tab appears.
- Market tab shows selected book and `Contract Probabilities` below it.
- Normal stream reconnects do not spam `runtime_error=stream: read live stream`.

- [ ] **Step 7: Verify browser manually**

Open the deployed browser UI.

Expected:

- BTC UP, BTC DOWN, ETH UP, ETH DOWN cards are visible.
- Monte Carlo path fans are visible, not only snapshot bars.
- Each card has a four-generator legend.
- Probability values and generator context match `/api/runtime/probabilities`.

- [ ] **Step 8: Final repository check**

Run:

```bash
git status --short --branch
git branch -r
ssh ender@100.72.104.49 "wsl.exe -d Ubuntu -- bash -lc 'cd /home/ender/polymarket && git rev-parse HEAD && git branch --show-current && git status --short --branch'"
```

Expected:

- Local worktree is clean.
- Remote branches list only `origin/main`.
- THEPC branch is `main`.
- THEPC HEAD equals pushed `origin/main`.

---

## Self-Review Notes

- The plan no longer assumes that updating THEPC alone restores browser path lines. It explicitly fixes the missing `simulation_preview` data path.
- The path-budget target is now precise: `320000` total generator paths per cycle, roughly `80k` effective paths per contract when four contracts and four generators are active.
- The TUI target matches current `main`: no separate `Volatility` tab and no `Probability` tab.
- The deploy target matches the requested fastest flow: push GitHub `main`, then THEPC fetches over SSH.
- No live trading, signing, or order-placement path is introduced.
