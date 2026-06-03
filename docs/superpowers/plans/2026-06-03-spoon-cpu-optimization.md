# Spoon CPU Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce `spoon` CPU pressure by lowering normalizer churn, preventing live-host Rust rebuilds in prebuilt mode, and skipping decision-state rebuilds when only ops/status fields change.

**Architecture:** Keep `spoon` as the always-on read-only collector and truth host. The normalizer remains a lightweight replay/research sidecar, but deployed cadence defaults to 0.25 seconds and state rebuilds are gated by semantic input changes rather than full status-file mtime churn. Docker builds move to a Mac/PC artifact path, while `scripts/deploy.sh` keeps an explicit host-build fallback.

**Tech Stack:** POSIX shell, Docker Compose, Python 3.11+ with `uv`, DuckDB, pytest, ruff, mypy.

---

## Report Decision

The report recommends five levers: normalizer cadence, prebuilt images, compact state input, state rebuild gating, and keeping Monte Carlo off `spoon`.

Best implementation order:

1. Lower the deployed normalizer default from `0.1` to `0.25` seconds. This aligns Docker with the CLI default that already exists.
2. Add a prebuilt Docker image path. `spoon` should restart images, not compile Rust, unless an operator explicitly opts into host builds.
3. Add semantic state gating in the normalizer. This gets most of the compact `status_state.json` benefit without waiting for Rust-side status schema work.
4. Leave `status_state.json` and deeper in-memory caches as future work after live CPU measurements. They are useful, but not needed for the first CPU cut.
5. Do not deploy to `spoon` from this local task. SSH/image transfer leaves the machine and needs explicit operator approval after local checks pass.

## Current Worktree Constraint

The repo already has uncommitted changes that reduce prewarm windows from 3 to 2 in:

- `README.md`
- `deploy/collector/.env.example`
- `deploy/collector/docker-compose.yml`
- `docs/SPOON_DEPLOYMENT.md`
- `scripts/deploy.sh`
- `tests/scripts/test_deploy_script.py`

Workers must preserve those edits and must not revert them.

## File Structure

- `deploy/collector/docker-compose.yml`  
  Owns collector/normalizer service defaults. Add image override environment expansion and change deployed normalizer cadence to `0.25`.

- `deploy/normalizer/normalizer-entrypoint.sh`  
  Owns normalizer runtime defaults inside the container. Change fallback cadence to `0.25`.

- `deploy/collector/.env.example`  
  Owns operator defaults. Add normalizer cadence and optional prebuilt image tag variables.

- `scripts/build_images_pc.sh`  
  New local build script. Builds SHA-tagged collector and normalizer images, tags `latest`, saves tarballs under `dist/docker/`, and writes a manifest.

- `scripts/deploy_prebuilt_images.sh`  
  New operator script. Copies tarballs to `spoon`, loads them, runs Compose without `--build`, and prints health-check commands. It must not compile Rust on `spoon`.

- `scripts/deploy.sh`  
  Existing spoon auto-deploy script. Add `POLYMARKET_DEPLOY_USE_PREBUILT=1` mode that refuses `--build`, checks required image tags locally on `spoon`, and only runs `docker compose up -d collector normalizer`. Keep host build fallback only when `POLYMARKET_DEPLOY_USE_PREBUILT` is not set.

- `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`  
  Add a semantic status signature from current/next contracts, orderbooks, Chainlink prices, and prices. Use that signature instead of raw status mtime to decide whether status-only cycles require decision-state rebuilds.

- `tests/ingestion/test_rust_normalizer_sidecar.py`  
  Add regression tests for ops-only status changes being skipped and semantic status changes still rebuilding.

- `tests/scripts/test_deploy_script.py`  
  Add static tests for the `0.25` cadence, image overrides, build scripts, and prebuilt deploy mode.

- `docs/SPOON_DEPLOYMENT.md` and `docs/PART_TWO_LIVE_COLLECTORS.md`  
  Document the CPU policy: default 0.25 normalizer cadence, prebuilt images for production deploys, host builds as explicit fallback, and Monte Carlo off `spoon`.

---

### Task 1: Lower Normalizer Cadence And Add Semantic State Gating

**Files:**
- Modify: `deploy/collector/docker-compose.yml`
- Modify: `deploy/normalizer/normalizer-entrypoint.sh`
- Modify: `deploy/collector/.env.example`
- Modify: `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`
- Modify: `tests/ingestion/test_rust_normalizer_sidecar.py`
- Modify: `tests/scripts/test_deploy_script.py`

- [ ] **Step 1: Write failing deploy cadence test**

In `tests/scripts/test_deploy_script.py`, change `test_normalizer_defaults_to_tenth_second_checkpointed_cadence` into:

```python
def test_normalizer_defaults_to_quarter_second_checkpointed_cadence() -> None:
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    entrypoint = (
        ROOT / "deploy" / "normalizer" / "normalizer-entrypoint.sh"
    ).read_text(encoding="utf-8")
    env_example = (ROOT / "deploy" / "collector" / ".env.example").read_text(
        encoding="utf-8"
    )

    assert "POLYMARKET_NORMALIZER_INTERVAL_SECONDS=0.25" in env_example
    assert "POLYMARKET_NORMALIZER_INTERVAL_SECONDS:-0.25" in compose
    assert 'INTERVAL_SECONDS="${POLYMARKET_NORMALIZER_INTERVAL_SECONDS:-0.25}"' in entrypoint
    assert "run-rust-normalizer-sidecar" in entrypoint
    assert "exec polymarket-engine" in entrypoint
    assert "while true" not in entrypoint
    assert '--interval-seconds "$INTERVAL_SECONDS"' in entrypoint
    assert '--normalized-health-path "$NORMALIZED_HEALTH_PATH"' in entrypoint
```

- [ ] **Step 2: Run cadence test and verify failure**

Run:

```bash
uv run pytest -q tests/scripts/test_deploy_script.py::test_normalizer_defaults_to_quarter_second_checkpointed_cadence
```

Expected: FAIL because Docker and the entrypoint still default to `0.1`.

- [ ] **Step 3: Update deployed normalizer default**

Change `deploy/collector/.env.example` by adding:

```text
POLYMARKET_NORMALIZER_INTERVAL_SECONDS=0.25
```

Change `deploy/collector/docker-compose.yml` normalizer environment to:

```yaml
      POLYMARKET_NORMALIZER_INTERVAL_SECONDS: ${POLYMARKET_NORMALIZER_INTERVAL_SECONDS:-0.25}
```

Change `deploy/normalizer/normalizer-entrypoint.sh` to:

```sh
INTERVAL_SECONDS="${POLYMARKET_NORMALIZER_INTERVAL_SECONDS:-0.25}"
```

- [ ] **Step 4: Run cadence test and verify pass**

Run:

```bash
uv run pytest -q tests/scripts/test_deploy_script.py::test_normalizer_defaults_to_quarter_second_checkpointed_cadence
```

Expected: PASS.

- [ ] **Step 5: Write failing semantic status gating tests**

Add this import near the top of `tests/ingestion/test_rust_normalizer_sidecar.py` if missing:

```python
from typing import Any, cast
```

Add the following tests after `test_sidecar_loop_rebuilds_state_when_status_changes_without_raw_rows`:

```python
def test_sidecar_loop_skips_state_build_when_only_ops_status_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts, monitor_counter=1)
    real_build = getattr(
        rust_normalizer_sidecar,
        "build_current_decision_state_snapshots",
    )
    build_calls = 0

    def counting_build(*args: Any, **kwargs: Any) -> Any:
        nonlocal build_calls
        build_calls += 1
        return real_build(*args, **kwargs)

    def change_ops_status(_: float) -> None:
        _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts, monitor_counter=2)
        next_mtime = time.time() + 1
        os.utime(status_path, (next_mtime, next_mtime))

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar."
        "build_current_decision_state_snapshots",
        counting_build,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        change_ops_status,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert build_calls == 1


def test_sidecar_loop_rebuilds_state_when_semantic_status_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = tmp_path / "raw"
    db_path = tmp_path / "state.duckdb"
    status_path = tmp_path / "live" / "status.json"
    health_path = tmp_path / "live" / "normalized_health.json"
    start_ts = datetime(2026, 6, 2, 6, 0, tzinfo=timezone.utc)
    asof_ts = start_ts + timedelta(minutes=2)
    _write_raw_tree(raw_root=raw_root, start_ts=start_ts, asof_ts=asof_ts)
    _write_status(status_path, start_ts=start_ts, asof_ts=asof_ts)
    real_build = getattr(
        rust_normalizer_sidecar,
        "build_current_decision_state_snapshots",
    )
    build_calls = 0

    def counting_build(*args: Any, **kwargs: Any) -> Any:
        nonlocal build_calls
        build_calls += 1
        return real_build(*args, **kwargs)

    def change_semantic_status(_: float) -> None:
        _write_status(
            status_path,
            start_ts=start_ts,
            asof_ts=asof_ts + timedelta(seconds=1),
        )
        next_mtime = time.time() + 1
        os.utime(status_path, (next_mtime, next_mtime))

    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar."
        "build_current_decision_state_snapshots",
        counting_build,
    )
    monkeypatch.setattr(
        "polymarket_engine.ingestion.rust_normalizer_sidecar.time.sleep",
        change_semantic_status,
    )

    run_rust_normalizer_loop(
        raw_root=raw_root,
        db_path=db_path,
        status_path=status_path,
        normalized_health_path=health_path,
        interval_seconds=0.0,
        include_next=False,
        max_cycles=2,
    )

    assert build_calls == 2
```

Change the helper signature to:

```python
def _write_status(
    path: Path,
    *,
    start_ts: datetime,
    asof_ts: datetime,
    monitor_counter: int | None = None,
) -> None:
```

Before writing the helper payload, add:

```python
    if monitor_counter is not None:
        payload["monitor"] = {"counter": monitor_counter}
        payload["websockets"] = {"last_event_age_ms": monitor_counter}
```

- [ ] **Step 6: Run semantic status tests and verify failure**

Run:

```bash
uv run pytest -q \
  tests/ingestion/test_rust_normalizer_sidecar.py::test_sidecar_loop_skips_state_build_when_only_ops_status_changes \
  tests/ingestion/test_rust_normalizer_sidecar.py::test_sidecar_loop_rebuilds_state_when_semantic_status_changes
```

Expected: first test FAILS with `build_calls == 2`; second test PASSES or FAILS only until helper changes are complete.

- [ ] **Step 7: Implement semantic status signature**

In `src/polymarket_engine/ingestion/rust_normalizer_sidecar.py`, add imports:

```python
import hashlib
import json
```

Add a dataclass near `RawTreeIdleSummary`:

```python
@dataclass(frozen=True)
class StatusStateSignature:
    mtime_ns: int
    semantic_hash: str
```

In `run_rust_normalizer_loop`, replace `previous_status_mtime_ns` with:

```python
        previous_status_signature: StatusStateSignature | None = None
```

At the start of each loop cycle, replace:

```python
            status_mtime_ns = _file_mtime_ns(status_path)
```

with:

```python
            status_signature = _status_state_signature(status_path)
            status_mtime_ns = status_signature.mtime_ns if status_signature is not None else None
            previous_status_mtime_ns = (
                previous_status_signature.mtime_ns
                if previous_status_signature is not None
                else None
            )
```

Replace the existing `status_changed = ...` calculation with:

```python
                status_changed = _status_signature_changed(
                    previous=previous_status_signature,
                    current=status_signature,
                )
```

At the end of each cycle, replace:

```python
            previous_status_mtime_ns = status_mtime_ns
```

with:

```python
            previous_status_signature = status_signature
```

Add helper functions near `_file_mtime_ns`:

```python
def _status_state_signature(path: Path) -> StatusStateSignature | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        mtime_ns = path.stat().st_mtime_ns
    except FileNotFoundError:
        return None
    if not isinstance(payload, dict):
        return None
    semantic_payload = {
        "schema_version": payload.get("schema_version"),
        "generated_at": payload.get("generated_at"),
        "current": payload.get("current", []),
        "next": payload.get("next", []),
        "orderbooks": payload.get("orderbooks", []),
        "chainlink_prices": payload.get("chainlink_prices", []),
        "prices": payload.get("prices", []),
    }
    encoded = json.dumps(
        semantic_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return StatusStateSignature(
        mtime_ns=mtime_ns,
        semantic_hash=hashlib.sha256(encoded).hexdigest(),
    )


def _status_signature_changed(
    *,
    previous: StatusStateSignature | None,
    current: StatusStateSignature | None,
) -> bool:
    if current is None:
        return False
    if previous is None:
        return True
    return current.semantic_hash != previous.semantic_hash
```

Keep `_run_rust_normalizer_cycle_with_store`, `_run_changed_rust_normalizer_cycle_with_store`, and `_run_idle_rust_normalizer_cycle_with_store` parameters as mtime-based so direct unit tests and single-cycle behavior remain compatible. The semantic signature is only for loop-level status-only gating.

- [ ] **Step 8: Run semantic status tests and verify pass**

Run:

```bash
uv run pytest -q \
  tests/ingestion/test_rust_normalizer_sidecar.py::test_sidecar_loop_skips_state_build_when_only_ops_status_changes \
  tests/ingestion/test_rust_normalizer_sidecar.py::test_sidecar_loop_rebuilds_state_when_semantic_status_changes
```

Expected: PASS.

- [ ] **Step 9: Run focused normalizer tests**

Run:

```bash
uv run pytest -q tests/ingestion/test_rust_normalizer_sidecar.py tests/scripts/test_deploy_script.py
```

Expected: PASS.

### Task 2: Add Prebuilt Docker Image Deployment Path

**Files:**
- Create: `scripts/build_images_pc.sh`
- Create: `scripts/deploy_prebuilt_images.sh`
- Modify: `deploy/collector/docker-compose.yml`
- Modify: `deploy/collector/.env.example`
- Modify: `scripts/deploy.sh`
- Modify: `tests/scripts/test_deploy_script.py`
- Modify: `docs/SPOON_DEPLOYMENT.md`

- [ ] **Step 1: Write failing script/deploy tests**

Add these tests to `tests/scripts/test_deploy_script.py`:

```python
def test_compose_supports_prebuilt_image_overrides() -> None:
    compose = (ROOT / "deploy" / "collector" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    env_example = (ROOT / "deploy" / "collector" / ".env.example").read_text(
        encoding="utf-8"
    )

    assert "image: ${POLYMARKET_COLLECTOR_IMAGE:-polymarket-rust-collector:latest}" in compose
    assert "image: ${POLYMARKET_NORMALIZER_IMAGE:-polymarket-normalizer:latest}" in compose
    assert "POLYMARKET_COLLECTOR_IMAGE=polymarket-rust-collector:latest" in env_example
    assert "POLYMARKET_NORMALIZER_IMAGE=polymarket-normalizer:latest" in env_example


def test_pc_image_build_script_exports_sha_tagged_tarballs() -> None:
    script = (ROOT / "scripts" / "build_images_pc.sh").read_text(encoding="utf-8")

    assert "DOCKER_BUILDKIT=1" in script
    assert "git rev-parse --short=12 HEAD" in script
    assert "polymarket-rust-collector:${GIT_SHA}" in script
    assert "polymarket-normalizer:${GIT_SHA}" in script
    assert "docker save" in script
    assert "dist/docker" in script
    assert "manifest" in script


def test_prebuilt_deploy_script_loads_images_without_building_on_spoon() -> None:
    script = (ROOT / "scripts" / "deploy_prebuilt_images.sh").read_text(encoding="utf-8")

    assert "docker load" in script
    assert "--build" not in script
    assert "POLYMARKET_DEPLOY_USE_PREBUILT=1" in script
    assert "POLYMARKET_COLLECTOR_IMAGE" in script
    assert "POLYMARKET_NORMALIZER_IMAGE" in script
    assert "check_collector_status.py" in script


def test_deploy_script_prebuilt_mode_refuses_host_builds() -> None:
    script = (ROOT / "scripts" / "deploy.sh").read_text(encoding="utf-8")

    assert 'USE_PREBUILT="${POLYMARKET_DEPLOY_USE_PREBUILT:-0}"' in script
    assert "required_image_available" in script
    assert "POLYMARKET_DEPLOY_USE_PREBUILT=1" in script
    assert 'compose -f "$COMPOSE_FILE" up -d collector normalizer' in script
    assert 'compose -f "$COMPOSE_FILE" up -d --build collector normalizer' in script
    assert "CARGO_BUILD_JOBS" in script
```

- [ ] **Step 2: Run script/deploy tests and verify failure**

Run:

```bash
uv run pytest -q \
  tests/scripts/test_deploy_script.py::test_compose_supports_prebuilt_image_overrides \
  tests/scripts/test_deploy_script.py::test_pc_image_build_script_exports_sha_tagged_tarballs \
  tests/scripts/test_deploy_script.py::test_prebuilt_deploy_script_loads_images_without_building_on_spoon \
  tests/scripts/test_deploy_script.py::test_deploy_script_prebuilt_mode_refuses_host_builds
```

Expected: FAIL because the new scripts and prebuilt mode do not exist yet.

- [ ] **Step 3: Add Compose image overrides and env defaults**

Change collector service image line in `deploy/collector/docker-compose.yml` to:

```yaml
    image: ${POLYMARKET_COLLECTOR_IMAGE:-polymarket-rust-collector:latest}
```

Change normalizer service image line to:

```yaml
    image: ${POLYMARKET_NORMALIZER_IMAGE:-polymarket-normalizer:latest}
```

Add to `deploy/collector/.env.example`:

```text
POLYMARKET_COLLECTOR_IMAGE=polymarket-rust-collector:latest
POLYMARKET_NORMALIZER_IMAGE=polymarket-normalizer:latest
```

- [ ] **Step 4: Create PC build script**

Create `scripts/build_images_pc.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${DIST_DIR:-$ROOT/dist/docker}"
GIT_SHA="$(cd "$ROOT" && git rev-parse --short=12 HEAD)"
COLLECTOR_IMAGE="${POLYMARKET_COLLECTOR_IMAGE:-polymarket-rust-collector:${GIT_SHA}}"
NORMALIZER_IMAGE="${POLYMARKET_NORMALIZER_IMAGE:-polymarket-normalizer:${GIT_SHA}}"
COLLECTOR_TAR="$DIST_DIR/polymarket-rust-collector-${GIT_SHA}.tar"
NORMALIZER_TAR="$DIST_DIR/polymarket-normalizer-${GIT_SHA}.tar"
MANIFEST="$DIST_DIR/manifest-${GIT_SHA}.txt"

mkdir -p "$DIST_DIR"

cd "$ROOT"
export DOCKER_BUILDKIT=1

docker build \
  -f deploy/collector/Dockerfile \
  -t "$COLLECTOR_IMAGE" \
  -t "polymarket-rust-collector:latest" \
  .

docker build \
  -f deploy/normalizer/Dockerfile \
  -t "$NORMALIZER_IMAGE" \
  -t "polymarket-normalizer:latest" \
  .

docker save "$COLLECTOR_IMAGE" "polymarket-rust-collector:latest" -o "$COLLECTOR_TAR"
docker save "$NORMALIZER_IMAGE" "polymarket-normalizer:latest" -o "$NORMALIZER_TAR"

{
  echo "git_sha=$GIT_SHA"
  echo "collector_image=$COLLECTOR_IMAGE"
  echo "normalizer_image=$NORMALIZER_IMAGE"
  echo "collector_tar=$COLLECTOR_TAR"
  echo "normalizer_tar=$NORMALIZER_TAR"
  docker image inspect "$COLLECTOR_IMAGE" --format 'collector_id={{.Id}}'
  docker image inspect "$NORMALIZER_IMAGE" --format 'normalizer_id={{.Id}}'
} > "$MANIFEST"

echo "wrote $MANIFEST"
echo "collector tar: $COLLECTOR_TAR"
echo "normalizer tar: $NORMALIZER_TAR"
```

Run:

```bash
chmod 755 scripts/build_images_pc.sh
```

- [ ] **Step 5: Create prebuilt deploy script**

Create `scripts/deploy_prebuilt_images.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_HOST="${REMOTE_HOST:-spoon}"
REMOTE_REPO="${REMOTE_REPO:-/home/spoon/polymarket}"
REMOTE_DIST_DIR="${REMOTE_DIST_DIR:-/home/spoon/polymarket-image-artifacts}"
DATA_DIR="${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}"
GIT_SHA="${GIT_SHA:-$(cd "$ROOT" && git rev-parse --short=12 HEAD)}"
COLLECTOR_IMAGE="${POLYMARKET_COLLECTOR_IMAGE:-polymarket-rust-collector:${GIT_SHA}}"
NORMALIZER_IMAGE="${POLYMARKET_NORMALIZER_IMAGE:-polymarket-normalizer:${GIT_SHA}}"
COLLECTOR_TAR="${COLLECTOR_TAR:-$ROOT/dist/docker/polymarket-rust-collector-${GIT_SHA}.tar}"
NORMALIZER_TAR="${NORMALIZER_TAR:-$ROOT/dist/docker/polymarket-normalizer-${GIT_SHA}.tar}"

test -f "$COLLECTOR_TAR"
test -f "$NORMALIZER_TAR"

ssh "$REMOTE_HOST" "mkdir -p '$REMOTE_DIST_DIR'"
scp "$COLLECTOR_TAR" "$NORMALIZER_TAR" "$REMOTE_HOST:$REMOTE_DIST_DIR/"

ssh "$REMOTE_HOST" "
  set -euo pipefail
  docker load -i '$REMOTE_DIST_DIR/$(basename "$COLLECTOR_TAR")'
  docker load -i '$REMOTE_DIST_DIR/$(basename "$NORMALIZER_TAR")'
  cd '$REMOTE_REPO'
  POLYMARKET_DEPLOY_USE_PREBUILT=1 \
  POLYMARKET_COLLECTOR_IMAGE='$COLLECTOR_IMAGE' \
  POLYMARKET_NORMALIZER_IMAGE='$NORMALIZER_IMAGE' \
  POLYMARKET_DATA_DIR='$DATA_DIR' \
  DEPLOY_FORCE=1 \
  ./scripts/deploy.sh
  python3 scripts/check_collector_status.py \
    --status-path '$DATA_DIR/live/status.json' \
    --raw-root '$DATA_DIR/raw' \
    --normalized-health-path '$DATA_DIR/live/normalized_health.json' \
    --expected-prewarm-windows 2
"
```

Run:

```bash
chmod 755 scripts/deploy_prebuilt_images.sh
```

- [ ] **Step 6: Add prebuilt mode to `scripts/deploy.sh`**

Near the existing constants, add:

```bash
USE_PREBUILT="${POLYMARKET_DEPLOY_USE_PREBUILT:-0}"
ALLOW_SPOON_BUILD="${POLYMARKET_DEPLOY_ALLOW_SPOON_BUILD:-1}"
COLLECTOR_IMAGE="${POLYMARKET_COLLECTOR_IMAGE:-polymarket-rust-collector:latest}"
NORMALIZER_IMAGE="${POLYMARKET_NORMALIZER_IMAGE:-polymarket-normalizer:latest}"
```

Add a helper after `normalizer_uses_sidecar()`:

```bash
required_image_available() {
  docker image inspect "$1" >/dev/null 2>&1
}
```

Replace the compose `up` block with:

```bash
if [ "$USE_PREBUILT" = "1" ]; then
  if ! required_image_available "$COLLECTOR_IMAGE"; then
    LOG "missing prebuilt collector image: $COLLECTOR_IMAGE"
    exit 66
  fi
  if ! required_image_available "$NORMALIZER_IMAGE"; then
    LOG "missing prebuilt normalizer image: $NORMALIZER_IMAGE"
    exit 66
  fi
  LOG "starting prebuilt images without host build: $COLLECTOR_IMAGE $NORMALIZER_IMAGE"
  if ! POLYMARKET_COLLECTOR_IMAGE="$COLLECTOR_IMAGE" \
    POLYMARKET_NORMALIZER_IMAGE="$NORMALIZER_IMAGE" \
    compose -f "$COMPOSE_FILE" up -d collector normalizer >> "$LOG_FILE" 2>&1; then
    LOG "docker compose failed"
    exit 1
  fi
else
  if [ "$ALLOW_SPOON_BUILD" != "1" ]; then
    LOG "host build refused; set POLYMARKET_DEPLOY_USE_PREBUILT=1 or POLYMARKET_DEPLOY_ALLOW_SPOON_BUILD=1"
    exit 66
  fi
  export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-1}"
  if ! compose -f "$COMPOSE_FILE" up -d --build collector normalizer >> "$LOG_FILE" 2>&1; then
    LOG "docker compose failed"
    exit 1
  fi
fi
```

- [ ] **Step 7: Run script syntax checks**

Run:

```bash
bash -n scripts/build_images_pc.sh
bash -n scripts/deploy_prebuilt_images.sh
bash -n scripts/deploy.sh
```

Expected: all commands exit `0`.

- [ ] **Step 8: Run script/deploy tests and verify pass**

Run:

```bash
uv run pytest -q tests/scripts/test_deploy_script.py
```

Expected: PASS.

- [ ] **Step 9: Update spoon deployment docs**

In `docs/SPOON_DEPLOYMENT.md`, replace the auto-deploy paragraph that says the deploy script rebuilds the Rust collector image with:

```markdown
The preferred production path uses prebuilt Docker images so `spoon` does not
compile Rust during live operation. Build images on the Mac or PC with
`scripts/build_images_pc.sh`, then deploy them with `scripts/deploy_prebuilt_images.sh`.
The script copies image tarballs to `spoon`, runs `docker load`, starts Compose
without `--build`, and smoke-checks collector plus normalizer health.

`scripts/deploy.sh` still supports host builds as an explicit fallback when
`POLYMARKET_DEPLOY_USE_PREBUILT` is not set. Fallback host builds export
`CARGO_BUILD_JOBS=1` by default so emergency rebuilds are slower but less
hostile to the live collector. For normal live operations, set
`POLYMARKET_DEPLOY_USE_PREBUILT=1` and provide `POLYMARKET_COLLECTOR_IMAGE` plus
`POLYMARKET_NORMALIZER_IMAGE`.
```

Add this under the normalizer/runbook section:

```markdown
The deployed normalizer defaults to `POLYMARKET_NORMALIZER_INTERVAL_SECONDS=0.25`.
The Rust collector remains the live truth source; the normalizer sidecar writes
DuckDB replay/research state and `normalized_health.json`. If normalized health
stays comfortably under the 30 second threshold, test `0.5` manually before
changing the repo default again.
```

### Task 3: Focused Verification And Final Review

**Files:**
- Review all modified files from Tasks 1-2.

- [ ] **Step 1: Run focused test suite once**

Run:

```bash
uv run pytest -q \
  tests/ingestion/test_rust_normalizer_sidecar.py \
  tests/scripts/test_deploy_script.py \
  tests/docs/test_active_runtime_docs.py \
  tests/test_cli.py
```

Expected: PASS.

- [ ] **Step 2: Run focused lint/type checks once**

Run:

```bash
uv run ruff check \
  src/polymarket_engine/ingestion/rust_normalizer_sidecar.py \
  tests/ingestion/test_rust_normalizer_sidecar.py \
  tests/scripts/test_deploy_script.py
uv run mypy \
  src/polymarket_engine/ingestion/rust_normalizer_sidecar.py \
  tests/ingestion/test_rust_normalizer_sidecar.py
```

Expected: PASS.

- [ ] **Step 3: Run shell syntax checks once**

Run:

```bash
sh -n deploy/normalizer/normalizer-entrypoint.sh
bash -n scripts/build_images_pc.sh
bash -n scripts/deploy_prebuilt_images.sh
bash -n scripts/deploy.sh
```

Expected: PASS.

- [ ] **Step 4: Inspect final diff**

Run:

```bash
git diff -- deploy/collector/docker-compose.yml deploy/normalizer/normalizer-entrypoint.sh deploy/collector/.env.example scripts/build_images_pc.sh scripts/deploy_prebuilt_images.sh scripts/deploy.sh src/polymarket_engine/ingestion/rust_normalizer_sidecar.py tests/ingestion/test_rust_normalizer_sidecar.py tests/scripts/test_deploy_script.py docs/SPOON_DEPLOYMENT.md docs/superpowers/plans/2026-06-03-spoon-cpu-optimization.md
```

Expected: diff contains only CPU optimization changes and preserves existing prewarm-window edits.

---

## Deployment Notes

Do not run `scripts/deploy_prebuilt_images.sh` or any `ssh spoon ...` command without explicit operator approval. Those commands leave this machine and affect the live host.

Manual local-only verification does not prove live CPU improvement. After approval, verify on `spoon` with:

```bash
ssh spoon 'ps -eo pcpu,args --sort=-pcpu | head -n 12'
ssh spoon 'pgrep -af "cargo build|rustc" || true'
ssh spoon 'cd /home/spoon/polymarket && python3 scripts/check_collector_status.py --status-path /home/spoon/polymarket-data/live/status.json --raw-root /home/spoon/polymarket-data/raw --normalized-health-path /home/spoon/polymarket-data/live/normalized_health.json --expected-prewarm-windows 2'
```

Expected after live deploy:

- no `cargo build` or `rustc` during prebuilt deploy;
- collector health passes;
- normalized health remains fresh;
- normalizer CPU drops because it wakes less often and skips ops-only state rebuilds.
