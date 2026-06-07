#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${DIST_DIR:-$ROOT/dist/docker}"
DEPLOY_REF="${POLYMARKET_DEPLOY_REF:-HEAD}"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"

PC_HOST="${PC_HOST:-ender@100.72.104.49}"
PC_WSL_DISTRO="${PC_WSL_DISTRO:-Ubuntu}"
PC_REPO="${PC_REPO:-/home/ender/polymarket}"
PC_BUNDLE="${PC_BUNDLE:-/home/ender/polymarket.bundle}"
PC_DATA_DIR="${PC_DATA_DIR:-/home/ender/polymarket-data}"
PC_DIST_DIR="${PC_DIST_DIR:-/home/ender/polymarket-image-artifacts}"
PC_BIN_DIR="${PC_BIN_DIR:-/home/ender/bin}"
PC_NORMALIZER_INTERVAL_SECONDS="${PC_NORMALIZER_INTERVAL_SECONDS:-0.1}"
PC_REST_BACKUP_INTERVAL_MS="${PC_REST_BACKUP_INTERVAL_MS:-1000}"
PC_PROBABILITY_MAX_TOTAL_PATHS="${PC_PROBABILITY_MAX_TOTAL_PATHS:-40000}"
PC_GPU_WORKER_MEM_LIMIT="${PC_GPU_WORKER_MEM_LIMIT:-1536m}"
PC_API_PORT="${PC_API_PORT:-8000}"
PC_DEPLOY_MODE="${PC_DEPLOY_MODE:-remote-build}"
PC_DEPLOY_BUILD_IMAGES="${PC_DEPLOY_BUILD_IMAGES:-1}"
PC_REMOTE_BUILD_SAVE_TARS="${PC_REMOTE_BUILD_SAVE_TARS:-0}"
PC_BRANCH="${PC_BRANCH:-$(git -C "$ROOT" branch --show-current)}"

if [ -z "$PC_BRANCH" ]; then
  echo "could not infer current git branch; set PC_BRANCH explicitly" >&2
  exit 1
fi

case "$PC_DEPLOY_MODE" in
  remote-build | image-tar)
    ;;
  *)
    echo "unsupported PC_DEPLOY_MODE=$PC_DEPLOY_MODE; expected remote-build or image-tar" >&2
    exit 2
    ;;
esac

if ! git -C "$ROOT" diff --quiet; then
  echo "working tree has unstaged changes; commit or stash before deploying to THEPC" >&2
  exit 1
fi

if ! git -C "$ROOT" diff --cached --quiet; then
  echo "working tree has staged changes; commit or unstage before deploying to THEPC" >&2
  exit 1
fi

if [ -n "$(git -C "$ROOT" ls-files --others --exclude-standard)" ]; then
  echo "working tree has untracked files; commit, remove, or ignore them before deploying to THEPC" >&2
  exit 1
fi

FULL_SHA="$(git -C "$ROOT" rev-parse "$DEPLOY_REF^{commit}")"
HEAD_SHA="$(git -C "$ROOT" rev-parse HEAD)"
if [ "$HEAD_SHA" != "$FULL_SHA" ]; then
  echo "deploy ref $DEPLOY_REF resolves to $FULL_SHA but HEAD is $HEAD_SHA; checkout the deploy ref first" >&2
  exit 1
fi
SHORT_SHA="${FULL_SHA:0:12}"

COLLECTOR_IMAGE="polymarket-rust-collector:${SHORT_SHA}"
NORMALIZER_IMAGE="polymarket-normalizer:${SHORT_SHA}"
CUDA_PROBABILITY_IMAGE="polymarket-cuda-probability:${SHORT_SHA}"
COLLECTOR_TAR="$DIST_DIR/polymarket-rust-collector-${SHORT_SHA}.tar"
NORMALIZER_TAR="$DIST_DIR/polymarket-normalizer-${SHORT_SHA}.tar"
CUDA_PROBABILITY_TAR="$DIST_DIR/polymarket-cuda-probability-${SHORT_SHA}.tar"
TUI_BIN="$DIST_DIR/polymarket-cockpit-tui-${SHORT_SHA}"
LOCAL_BUNDLE="$DIST_DIR/polymarket-${SHORT_SHA}.bundle"

if [ "$PC_DEPLOY_MODE" = "image-tar" ] && [ "$PC_DEPLOY_BUILD_IMAGES" = "1" ]; then
  TARGET_PLATFORM="$TARGET_PLATFORM" POLYMARKET_DEPLOY_REF="$DEPLOY_REF" "$ROOT/scripts/build_images_pc.sh"
fi

if [ "$PC_DEPLOY_MODE" = "image-tar" ]; then
  if [ ! -f "$COLLECTOR_TAR" ]; then
    echo "missing collector image tarball: $COLLECTOR_TAR" >&2
    exit 1
  fi

  if [ ! -f "$NORMALIZER_TAR" ]; then
    echo "missing normalizer image tarball: $NORMALIZER_TAR" >&2
    exit 1
  fi

  if [ ! -f "$CUDA_PROBABILITY_TAR" ]; then
    echo "missing CUDA probability image tarball: $CUDA_PROBABILITY_TAR" >&2
    exit 1
  fi

  if [ ! -f "$TUI_BIN" ]; then
    echo "missing TUI binary: $TUI_BIN" >&2
    exit 1
  fi
fi

mkdir -p "$DIST_DIR"
git -C "$ROOT" bundle create "$LOCAL_BUNDLE.tmp" --branches --tags
mv "$LOCAL_BUNDLE.tmp" "$LOCAL_BUNDLE"

shell_quote() {
  printf "%q" "$1"
}

wsl_put_file() {
  local src="$1"
  local dest="$2"
  local dest_dir
  local dest_dir_q
  local dest_q

  dest_dir="$(dirname "$dest")"
  dest_dir_q="$(shell_quote "$dest_dir")"
  dest_q="$(shell_quote "$dest")"

  ssh "$PC_HOST" "wsl.exe -d $PC_WSL_DISTRO -- bash -lc \"mkdir -p $dest_dir_q && cat > $dest_q\"" < "$src"
}

if [ "$PC_DEPLOY_MODE" = "remote-build" ]; then
  echo "copying git bundle to THEPC WSL; images will build on THEPC"
else
  echo "copying git bundle and image tarballs to THEPC WSL"
fi
wsl_put_file "$LOCAL_BUNDLE" "$PC_BUNDLE"
if [ "$PC_DEPLOY_MODE" = "image-tar" ]; then
  wsl_put_file "$COLLECTOR_TAR" "$PC_DIST_DIR/$(basename "$COLLECTOR_TAR")"
  wsl_put_file "$NORMALIZER_TAR" "$PC_DIST_DIR/$(basename "$NORMALIZER_TAR")"
  wsl_put_file "$CUDA_PROBABILITY_TAR" "$PC_DIST_DIR/$(basename "$CUDA_PROBABILITY_TAR")"
  wsl_put_file "$TUI_BIN" "$PC_DIST_DIR/$(basename "$TUI_BIN")"
fi

ssh "$PC_HOST" "wsl.exe -d $PC_WSL_DISTRO -- bash -s" <<EOF
set -euo pipefail

FULL_SHA=$(shell_quote "$FULL_SHA")
SHORT_SHA=$(shell_quote "$SHORT_SHA")
PC_BRANCH=$(shell_quote "$PC_BRANCH")
PC_REPO=$(shell_quote "$PC_REPO")
PC_BUNDLE=$(shell_quote "$PC_BUNDLE")
PC_DATA_DIR=$(shell_quote "$PC_DATA_DIR")
PC_DIST_DIR=$(shell_quote "$PC_DIST_DIR")
PC_BIN_DIR=$(shell_quote "$PC_BIN_DIR")
PC_WSL_DISTRO=$(shell_quote "$PC_WSL_DISTRO")
PC_NORMALIZER_INTERVAL_SECONDS=$(shell_quote "$PC_NORMALIZER_INTERVAL_SECONDS")
PC_REST_BACKUP_INTERVAL_MS=$(shell_quote "$PC_REST_BACKUP_INTERVAL_MS")
PC_PROBABILITY_MAX_TOTAL_PATHS=$(shell_quote "$PC_PROBABILITY_MAX_TOTAL_PATHS")
PC_GPU_WORKER_MEM_LIMIT=$(shell_quote "$PC_GPU_WORKER_MEM_LIMIT")
PC_API_PORT=$(shell_quote "$PC_API_PORT")
PC_DEPLOY_MODE=$(shell_quote "$PC_DEPLOY_MODE")
PC_REMOTE_BUILD_SAVE_TARS=$(shell_quote "$PC_REMOTE_BUILD_SAVE_TARS")
TARGET_PLATFORM=$(shell_quote "$TARGET_PLATFORM")
COLLECTOR_IMAGE=$(shell_quote "$COLLECTOR_IMAGE")
NORMALIZER_IMAGE=$(shell_quote "$NORMALIZER_IMAGE")
CUDA_PROBABILITY_IMAGE=$(shell_quote "$CUDA_PROBABILITY_IMAGE")
COLLECTOR_TAR=$(shell_quote "$PC_DIST_DIR/$(basename "$COLLECTOR_TAR")")
NORMALIZER_TAR=$(shell_quote "$PC_DIST_DIR/$(basename "$NORMALIZER_TAR")")
CUDA_PROBABILITY_TAR=$(shell_quote "$PC_DIST_DIR/$(basename "$CUDA_PROBABILITY_TAR")")
TUI_BIN=$(shell_quote "$PC_DIST_DIR/$(basename "$TUI_BIN")")

set_env() {
  key="\$1"
  value="\$2"
  file="\$3"
  tmp="\$(mktemp)"
  touch "\$file"
  awk -v key="\$key" -v value="\$value" '
    BEGIN { found = 0 }
    \$0 ~ "^" key "=" {
      print key "=" value
      found = 1
      next
    }
    { print }
    END {
      if (!found) {
        print key "=" value
      }
    }
  ' "\$file" > "\$tmp"
  mv "\$tmp" "\$file"
}

mkdir -p "\$PC_DATA_DIR/raw" "\$PC_DATA_DIR/db" "\$PC_DATA_DIR/live" "\$PC_DATA_DIR/logs" "\$PC_DIST_DIR" "\$PC_BIN_DIR"
touch "\$PC_DATA_DIR/raw/.polymarket_archive_root"

if [ ! -d "\$PC_REPO/.git" ]; then
  git clone "\$PC_BUNDLE" "\$PC_REPO"
fi

cd "\$PC_REPO"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "THEPC repo is dirty; refusing deploy" >&2
  git status --porcelain >&2
  exit 1
fi

git remote set-url origin "\$PC_BUNDLE" 2>/dev/null || git remote add origin "\$PC_BUNDLE"
git fetch --quiet origin
git checkout -B "\$PC_BRANCH" "\$FULL_SHA"

if [ ! -f deploy/collector/.env ]; then
  cp deploy/collector/.env.example deploy/collector/.env
fi

set_env POLYMARKET_UID "\$(id -u)" deploy/collector/.env
set_env POLYMARKET_GID "\$(id -g)" deploy/collector/.env
set_env POLYMARKET_DATA_DIR "\$PC_DATA_DIR" deploy/collector/.env
set_env POLYMARKET_NORMALIZER_INTERVAL_SECONDS "\$PC_NORMALIZER_INTERVAL_SECONDS" deploy/collector/.env
set_env POLYMARKET_REST_BACKUP_INTERVAL_MS "\$PC_REST_BACKUP_INTERVAL_MS" deploy/collector/.env
set_env POLYMARKET_PROBABILITY_MAX_TOTAL_PATHS "\$PC_PROBABILITY_MAX_TOTAL_PATHS" deploy/collector/.env
set_env POLYMARKET_GPU_WORKER_MEM_LIMIT "\$PC_GPU_WORKER_MEM_LIMIT" deploy/collector/.env
set_env POLYMARKET_API_PORT "\$PC_API_PORT" deploy/collector/.env
set_env POLYMARKET_ENABLE_RUNTIME_PROBABILITIES 1 deploy/collector/.env
set_env POLYMARKET_ALLOW_RUNTIME_PROBABILITY_COMPUTE 0 deploy/collector/.env
set_env POLYMARKET_COLLECTOR_IMAGE "\$COLLECTOR_IMAGE" deploy/collector/.env
set_env POLYMARKET_NORMALIZER_IMAGE "\$NORMALIZER_IMAGE" deploy/collector/.env
set_env POLYMARKET_CUDA_PROBABILITY_IMAGE "\$CUDA_PROBABILITY_IMAGE" deploy/collector/.env

if [ "\$PC_DEPLOY_MODE" = "remote-build" ]; then
  DOCKER_CONFIG="\$PC_DATA_DIR/docker-config"
  mkdir -p "\$DOCKER_CONFIG"
  printf '%s\n' '{"auths":{}}' > "\$DOCKER_CONFIG/config.json"
  export DOCKER_CONFIG
  POLYMARKET_BUILD_SAVE_TARS="\$PC_REMOTE_BUILD_SAVE_TARS" \\
    TARGET_PLATFORM="\$TARGET_PLATFORM" \\
    POLYMARKET_DEPLOY_REF="\$FULL_SHA" \\
    ./scripts/build_images_pc.sh
  TUI_BIN="\$PC_REPO/dist/docker/polymarket-cockpit-tui-\$SHORT_SHA"
else
  docker load -i "\$COLLECTOR_TAR"
  docker load -i "\$NORMALIZER_TAR"
  docker load -i "\$CUDA_PROBABILITY_TAR"
fi
if [ ! -f "\$TUI_BIN" ]; then
  echo "missing TUI binary after deploy image prep: \$TUI_BIN" >&2
  exit 1
fi
install -m 755 "\$TUI_BIN" "\$PC_BIN_DIR/polymarket-cockpit-tui"

{
  printf '%s\n' '#!/usr/bin/env bash'
  printf '%s\n' 'set -euo pipefail'
  printf 'cd %q\n' "\$PC_REPO"
  printf '%s\n' "echo 'Checking Polymarket runtime...'"
  printf 'if curl -fsS --max-time 2 http://127.0.0.1:%s/api/runtime/live?limit=8 2>/dev/null | python3 -c %q >/dev/null 2>&1; then\n' "\$PC_API_PORT" 'import json,sys; p=json.load(sys.stdin); m=p.get("monitor") or {}; sys.exit(0 if p.get("ok") and len(m.get("orderbooks") or []) > 0 else 1)'
  printf '%s\n' "  echo 'Runtime already live.'"
  printf '%s\n' 'else'
  printf '%s\n' "  echo 'Runtime not live; starting containers...'"
  printf '%s\n' '  docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml stop outcome-refresh >/dev/null 2>&1 || true'
  printf '%s\n' '  docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml up -d --no-recreate collector normalizer api gpu-probability-worker >/dev/null 2>&1 || true'
  printf '%s\n' 'fi'
  printf '%s\n' "echo 'Waiting for runtime API and live market rows...'"
  printf '%s\n' 'for _ in \$(seq 1 45); do'
  printf '  if curl -fsS --max-time 2 http://127.0.0.1:%s/api/runtime/live?limit=8 2>/dev/null | python3 -c %q >/dev/null 2>&1; then\n' "\$PC_API_PORT" 'import json,sys; p=json.load(sys.stdin); m=p.get("monitor") or {}; sys.exit(0 if p.get("ok") and len(m.get("orderbooks") or []) > 0 else 1)'
  printf '%s\n' '    break'
  printf '%s\n' '  fi'
  printf '%s\n' '  sleep 1'
  printf '%s\n' 'done'
  printf 'exec %q --engine-api-url http://127.0.0.1:%s --poll-interval-ms 250\n' "\$PC_BIN_DIR/polymarket-cockpit-tui" "\$PC_API_PORT"
} > "\$PC_BIN_DIR/open-polymarket-tui.sh"
chmod 755 "\$PC_BIN_DIR/open-polymarket-tui.sh"

cat > "\$PC_BIN_DIR/open-polymarket-tui-window.sh" <<'TUI_WINDOW_LAUNCHER'
#!/usr/bin/env bash
set +e
__PC_BIN_DIR__/open-polymarket-tui.sh
status=\$?
if [ "\$status" -ne 0 ]; then
  echo
  echo "Polymarket TUI exited with status \$status"
  read -r -p "Press Enter to close"
fi
exit "\$status"
TUI_WINDOW_LAUNCHER
sed -i "s|__PC_BIN_DIR__|\$PC_BIN_DIR|g" "\$PC_BIN_DIR/open-polymarket-tui-window.sh"
chmod 755 "\$PC_BIN_DIR/open-polymarket-tui-window.sh"

cat > "\$PC_BIN_DIR/open-polymarket-duckdb-ui.sh" <<'DUCKDB_UI_LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail

PORT="\${POLYMARKET_DUCKDB_UI_PORT:-4213}"
while [ "\$#" -gt 0 ]; do
  case "\$1" in
    --port)
      PORT="\$2"
      shift 2
      ;;
    *)
      echo "unknown argument: \$1" >&2
      exit 2
      ;;
  esac
done

PC_REPO="\${PC_REPO:-/home/ender/polymarket}"
DATA_DIR="\${POLYMARKET_DATA_DIR:-/home/ender/polymarket-data}"
SOURCE_DB="\${POLYMARKET_DUCKDB_SOURCE_DB:-\$DATA_DIR/db/polymarket.duckdb}"
SNAPSHOT_DIR="\${POLYMARKET_DUCKDB_UI_SNAPSHOT_DIR:-\$DATA_DIR/duckdb-ui}"
SNAPSHOT_DB="\$SNAPSHOT_DIR/current-polymarket.duckdb"
SNAPSHOT_TMP="\$SNAPSHOT_DIR/snapshot.duckdb"
LOG_DIR="\$DATA_DIR/logs"
LOG_FILE="\$LOG_DIR/duckdb-ui.log"
VIEWER_SCRIPT="\$SNAPSHOT_DIR/polymarket_duckdb_viewer.py"
DUCKDB_BIN="\${DUCKDB_BIN:-\$HOME/.duckdb/cli/latest/duckdb}"

mkdir -p "\$SNAPSHOT_DIR" "\$LOG_DIR" "\$HOME/bin"

if ! command -v duckdb >/dev/null 2>&1 && [ ! -x "\$DUCKDB_BIN" ]; then
  curl -fsSL https://install.duckdb.org | sh >> "\$LOG_FILE" 2>&1
fi

if command -v duckdb >/dev/null 2>&1; then
  DUCKDB_BIN="\$(command -v duckdb)"
elif [ -x "\$DUCKDB_BIN" ]; then
  DUCKDB_BIN="\$DUCKDB_BIN"
else
  echo "DuckDB CLI is not installed and could not be found" >&2
  exit 1
fi

if [ ! -f "\$SOURCE_DB" ]; then
  echo "source DuckDB missing: \$SOURCE_DB" >&2
  exit 1
fi

quote_sql_string() {
  printf "%s" "\$1" | sed "s/'/''/g; s/^/'/; s/\$/'/"
}

restart_refresh_services() {
  (
    cd "\$PC_REPO" &&
      docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml up -d --no-deps normalizer >/dev/null
  ) || true
}

cd "\$PC_REPO"
trap restart_refresh_services EXIT
docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml stop normalizer outcome-refresh >/dev/null
rm -f "\$SNAPSHOT_TMP" "\$SNAPSHOT_TMP.wal"
"\$DUCKDB_BIN" "\$SNAPSHOT_TMP" -batch -c "ATTACH \$(quote_sql_string "\$SOURCE_DB") AS source_db (READ_ONLY); COPY FROM DATABASE source_db TO snapshot;"
mv "\$SNAPSHOT_TMP" "\$SNAPSHOT_DB"
restart_refresh_services
trap - EXIT

cat > "\$VIEWER_SCRIPT" <<'DUCKDB_VIEWER_PY'
#!/usr/bin/env python3
import argparse
import html
import json
import os
import subprocess
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class Viewer(BaseHTTPRequestHandler):
    db_path = ""
    duckdb_bin = "duckdb"

    def log_message(self, fmt: str, *args: object) -> None:
        return

    def run_json(self, sql: str) -> list[dict[str, object]]:
        completed = subprocess.run(
            [self.duckdb_bin, "-readonly", self.db_path, "-json", "-c", sql],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        output = completed.stdout.strip()
        return json.loads(output) if output else []

    def send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self) -> None:
        db_name = html.escape(os.path.basename(self.db_path))
        body = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Polymarket DuckDB</title>
  <style>
    :root {{ color-scheme: light dark; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    body {{ margin: 0; background: #f7f7f4; color: #1f2523; }}
    header {{ padding: 14px 18px; border-bottom: 1px solid #d8ddd7; background: #ffffff; display: flex; justify-content: space-between; align-items: baseline; gap: 16px; }}
    h1 {{ font-size: 16px; margin: 0; font-weight: 650; }}
    .muted {{ color: #68716d; font-size: 12px; }}
    main {{ display: grid; grid-template-columns: 280px minmax(0, 1fr); min-height: calc(100vh - 51px); }}
    aside {{ border-right: 1px solid #d8ddd7; background: #fcfcfa; overflow: auto; padding: 10px; }}
    button.table {{ display: block; width: 100%; text-align: left; border: 0; background: transparent; padding: 8px 10px; border-radius: 6px; cursor: pointer; color: inherit; }}
    button.table:hover, button.table.active {{ background: #e9eee9; }}
    .schema {{ font-size: 11px; color: #68716d; text-transform: uppercase; margin: 12px 10px 4px; }}
    section {{ min-width: 0; overflow: hidden; }}
    .toolbar {{ padding: 10px 12px; border-bottom: 1px solid #d8ddd7; display: flex; align-items: center; gap: 10px; background: #fff; }}
    .toolbar strong {{ font-size: 14px; }}
    .toolbar button {{ border: 1px solid #c8d0ca; background: #fff; border-radius: 6px; padding: 6px 9px; cursor: pointer; }}
    .table-wrap {{ overflow: auto; height: calc(100vh - 101px); }}
    table {{ border-collapse: collapse; width: max-content; min-width: 100%; font-size: 12px; }}
    th, td {{ border-bottom: 1px solid #e1e5e0; padding: 6px 8px; max-width: 360px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
    th {{ position: sticky; top: 0; background: #f0f2ee; text-align: left; z-index: 1; }}
    td.null {{ color: #9aa19d; font-style: italic; }}
    .empty {{ padding: 28px; color: #68716d; }}
    @media (prefers-color-scheme: dark) {{
      body {{ background: #161917; color: #edf0ed; }}
      header, .toolbar {{ background: #1d211f; border-color: #343b37; }}
      aside {{ background: #191d1b; border-color: #343b37; }}
      button.table:hover, button.table.active {{ background: #29302c; }}
      th {{ background: #222723; }}
      th, td {{ border-color: #303832; }}
      .toolbar button {{ background: #202520; color: inherit; border-color: #465149; }}
    }}
  </style>
</head>
<body>
  <header><h1>Polymarket DuckDB</h1><div class="muted">Snapshot: {db_name}</div></header>
  <main>
    <aside id="tables"><div class="empty">Loading tables...</div></aside>
    <section>
      <div class="toolbar">
        <strong id="current">Select a table</strong>
        <span class="muted" id="meta"></span>
        <button id="prev">Prev</button>
        <button id="next">Next</button>
      </div>
      <div class="table-wrap" id="rows"><div class="empty">Choose a table from the left.</div></div>
    </section>
  </main>
  <script>
    let selected = null;
    let offset = 0;
    const limit = 200;
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
    async function getJson(url) {{
      const res = await fetch(url);
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    }}
    function tableButton(t) {{
      const b = document.createElement('button');
      b.className = 'table';
      b.textContent = t.name;
      b.onclick = () => {{ selected = t; offset = 0; loadRows(); }};
      return b;
    }}
    async function loadTables() {{
      const tables = await getJson('/api/tables');
      const root = document.getElementById('tables');
      root.innerHTML = '';
      let last = '';
      for (const t of tables) {{
        if (t.schema !== last) {{
          const s = document.createElement('div');
          s.className = 'schema';
          s.textContent = t.schema;
          root.appendChild(s);
          last = t.schema;
        }}
        root.appendChild(tableButton(t));
      }}
      if (!tables.length) root.innerHTML = '<div class="empty">No tables found.</div>';
    }}
    async function loadRows() {{
      document.querySelectorAll('button.table').forEach(b => b.classList.toggle('active', selected && b.textContent === selected.name));
      document.getElementById('current').textContent = selected.schema + '.' + selected.name;
      document.getElementById('meta').textContent = 'rows ' + (offset + 1) + '-' + (offset + limit);
      const url = '/api/table?schema=' + encodeURIComponent(selected.schema) + '&table=' + encodeURIComponent(selected.name) + '&limit=' + limit + '&offset=' + offset;
      const payload = await getJson(url);
      const cols = payload.columns;
      const rows = payload.rows;
      if (!rows.length) {{
        document.getElementById('rows').innerHTML = '<div class="empty">No rows at this offset.</div>';
        return;
      }}
      let out = '<table><thead><tr>' + cols.map(c => '<th>' + esc(c) + '</th>').join('') + '</tr></thead><tbody>';
      for (const row of rows) {{
        out += '<tr>' + cols.map(c => row[c] == null ? '<td class="null">NULL</td>' : '<td title="' + esc(row[c]) + '">' + esc(row[c]) + '</td>').join('') + '</tr>';
      }}
      out += '</tbody></table>';
      document.getElementById('rows').innerHTML = out;
    }}
    document.getElementById('prev').onclick = () => {{ if (selected) {{ offset = Math.max(0, offset - limit); loadRows(); }} }};
    document.getElementById('next').onclick = () => {{ if (selected) {{ offset += limit; loadRows(); }} }};
    loadTables().catch(e => document.getElementById('tables').innerHTML = '<div class="empty">' + esc(e.message) + '</div>');
  </script>
</body>
</html>"""
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                self.send_html()
            elif parsed.path == "/api/tables":
                self.send_json(self.run_json(
                    "SELECT table_schema AS schema, table_name AS name, table_type AS type "
                    "FROM information_schema.tables "
                    "WHERE table_schema NOT IN ('information_schema', 'pg_catalog') "
                    "ORDER BY table_schema, table_name"
                ))
            elif parsed.path == "/api/table":
                schema = params.get("schema", ["main"])[0]
                table = params.get("table", [""])[0]
                limit = min(max(int(params.get("limit", ["200"])[0]), 1), 1000)
                offset = max(int(params.get("offset", ["0"])[0]), 0)
                if not table:
                    self.send_json({"error": "missing table"}, 400)
                    return
                relation = f"{quote_ident(schema)}.{quote_ident(table)}"
                rows = self.run_json(f"SELECT * FROM {relation} LIMIT {limit} OFFSET {offset}")
                columns = list(rows[0].keys()) if rows else [
                    row["column_name"] for row in self.run_json(
                        "SELECT column_name FROM information_schema.columns "
                        f"WHERE table_schema = {quote_literal(schema)} "
                        f"AND table_name = {quote_literal(table)} "
                        "ORDER BY ordinal_position"
                    )
                ]
                self.send_json({"columns": columns, "rows": rows})
            else:
                self.send_json({"error": "not found"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--duckdb-bin", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    Viewer.db_path = args.db
    Viewer.duckdb_bin = args.duckdb_bin
    ThreadingHTTPServer(("127.0.0.1", args.port), Viewer).serve_forever()


if __name__ == "__main__":
    main()
DUCKDB_VIEWER_PY
chmod 755 "\$VIEWER_SCRIPT"

pkill -f "duckdb.*ui-catalog.duckdb" >/dev/null 2>&1 || true
pkill -f "polymarket_duckdb_viewer.py.*--port \$PORT" >/dev/null 2>&1 || true
nohup python3 "\$VIEWER_SCRIPT" --db "\$SNAPSHOT_DB" --duckdb-bin "\$DUCKDB_BIN" --port "\$PORT" >/dev/null 2>> "\$LOG_FILE" &

for _ in \$(seq 1 30); do
  if curl -fsS --max-time 2 "http://127.0.0.1:\${PORT}/api/tables" >/dev/null 2>> "\$LOG_FILE"; then
    echo "Polymarket DuckDB viewer ready at http://127.0.0.1:\${PORT}"
    echo "Snapshot: \$SNAPSHOT_DB"
    exit 0
  fi
  sleep 0.5
done

echo "Polymarket DuckDB viewer did not answer on http://127.0.0.1:\${PORT}" >&2
exit 1
DUCKDB_UI_LAUNCHER
chmod 755 "\$PC_BIN_DIR/open-polymarket-duckdb-ui.sh"

cat > "\$PC_BIN_DIR/open-polymarket-duckdb-ui-window.sh" <<'DUCKDB_UI_WINDOW_LAUNCHER'
#!/usr/bin/env bash
set +e
__PC_BIN_DIR__/open-polymarket-duckdb-ui.sh
status=\$?
if [ "\$status" -ne 0 ]; then
  echo
  echo "Polymarket DuckDB UI exited with status \$status"
  read -r -p "Press Enter to close"
  exit "\$status"
fi
echo
echo "Open http://127.0.0.1:4213 in the Windows browser."
read -r -p "Press Enter to close"
DUCKDB_UI_WINDOW_LAUNCHER
sed -i "s|__PC_BIN_DIR__|\$PC_BIN_DIR|g" "\$PC_BIN_DIR/open-polymarket-duckdb-ui-window.sh"
chmod 755 "\$PC_BIN_DIR/open-polymarket-duckdb-ui-window.sh"

WINDOWS_USER_DIR="/mnt/c/Users/ender"
if [ -d "\$WINDOWS_USER_DIR" ]; then
  cat > "\$WINDOWS_USER_DIR/open-polymarket-tui.ps1" <<'PS_LAUNCHER'
\$ErrorActionPreference = 'Stop'
\$logPath = Join-Path \$env:USERPROFILE ("polymarket-tui-launch-{0}.log" -f \$PID)
\$fallbackLogPath = Join-Path \$env:USERPROFILE 'polymarket-tui-launch-error.log'
\$arguments = @('-w', 'new', 'new-tab', '--title', 'Polymarket TUI', 'wsl.exe', '-d', '__PC_WSL_DISTRO__', '--', '__PC_BIN_DIR__/open-polymarket-tui-window.sh')

try {
  Start-Transcript -Path \$logPath -Append -ErrorAction SilentlyContinue | Out-Null
} catch {
}

try {
  Start-Process -FilePath 'wt.exe' -ArgumentList \$arguments -WindowStyle Normal
} catch {
  Add-Content -Path \$fallbackLogPath -Value ("{0} Failed to launch Windows Terminal for Polymarket TUI: {1}" -f (Get-Date -Format o), \$_)
  & wsl.exe -d __PC_WSL_DISTRO__ -- __PC_BIN_DIR__/open-polymarket-tui-window.sh
  \$exitCode = \$LASTEXITCODE
  if (\$exitCode -ne 0) {
    Read-Host 'Press Enter to close'
  }
  exit \$exitCode
} finally {
  try {
    Stop-Transcript | Out-Null
  } catch {
  }
}
PS_LAUNCHER
  sed -i "s|__PC_BIN_DIR__|\$PC_BIN_DIR|g; s|__PC_WSL_DISTRO__|\$PC_WSL_DISTRO|g" "\$WINDOWS_USER_DIR/open-polymarket-tui.ps1"
  cat > "\$WINDOWS_USER_DIR/open-polymarket-tui.cmd" <<CMD_LAUNCHER
@echo off
start "Polymarket TUI" wsl.exe -d \$PC_WSL_DISTRO -- \$PC_BIN_DIR/open-polymarket-tui-window.sh
CMD_LAUNCHER
  cat > "\$WINDOWS_USER_DIR/open-polymarket-duckdb-ui.cmd" <<CMD_DUCKDB_UI_LAUNCHER
@echo off
start "Polymarket DuckDB UI" wsl.exe -d \$PC_WSL_DISTRO -- \$PC_BIN_DIR/open-polymarket-duckdb-ui-window.sh
timeout /t 3 >nul
start "" "http://127.0.0.1:4213"
CMD_DUCKDB_UI_LAUNCHER
  POWERSHELL_SCRIPT="\$WINDOWS_USER_DIR/AppData/Local/Temp/polymarket-tui-shortcut.ps1"
  cat > "\$POWERSHELL_SCRIPT" <<'PS1'
\$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Polymarket TUI.lnk'
\$launcherPath = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'open-polymarket-tui.cmd'
\$shell = New-Object -ComObject WScript.Shell
\$shortcut = \$shell.CreateShortcut(\$shortcutPath)
\$shortcut.TargetPath = \$launcherPath
\$shortcut.Arguments = ''
\$shortcut.WorkingDirectory = [Environment]::GetFolderPath('UserProfile')
\$shortcut.IconLocation = 'C:\WINDOWS\System32\cmd.exe,0'
\$shortcut.WindowStyle = 1
\$shortcut.Save()

\$shortcutPath = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Polymarket DuckDB UI.lnk'
\$launcherPath = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'open-polymarket-duckdb-ui.cmd'
\$shortcut = \$shell.CreateShortcut(\$shortcutPath)
\$shortcut.TargetPath = \$launcherPath
\$shortcut.Arguments = ''
\$shortcut.WorkingDirectory = [Environment]::GetFolderPath('UserProfile')
\$shortcut.IconLocation = 'C:\WINDOWS\System32\cmd.exe,0'
\$shortcut.WindowStyle = 1
\$shortcut.Save()
PS1
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "\$(wslpath -w "\$POWERSHELL_SCRIPT")" >/dev/null < /dev/null
  rm -f "\$POWERSHELL_SCRIPT"
fi

export POLYMARKET_DEPLOY_USE_PREBUILT=1
export POLYMARKET_DEPLOY_REF="\$FULL_SHA"
export POLYMARKET_EXPECTED_DEPLOY_SHA="\$FULL_SHA"
export POLYMARKET_COLLECTOR_IMAGE="\$COLLECTOR_IMAGE"
export POLYMARKET_NORMALIZER_IMAGE="\$NORMALIZER_IMAGE"
export POLYMARKET_CUDA_PROBABILITY_IMAGE="\$CUDA_PROBABILITY_IMAGE"
export POLYMARKET_DATA_DIR="\$PC_DATA_DIR"
export POLYMARKET_NORMALIZER_INTERVAL_SECONDS="\$PC_NORMALIZER_INTERVAL_SECONDS"
export POLYMARKET_REST_BACKUP_INTERVAL_MS="\$PC_REST_BACKUP_INTERVAL_MS"
export DEPLOY_FORCE=1
./scripts/deploy.sh

collector_status_ok=0
for _ in \$(seq 1 30); do
  if python3 scripts/check_collector_status.py \\
    --status-path "\$PC_DATA_DIR/live/status.json" \\
    --max-status-age-seconds 30 \\
    --max-price-age-ms 30000 \\
    --max-orderbook-age-ms 30000 \\
    --max-websocket-event-age-ms 30000 \\
    --raw-root "\$PC_DATA_DIR/raw" \\
    --max-raw-event-age-ms 30000 \\
    --normalized-health-path "\$PC_DATA_DIR/live/normalized_health.json" \\
    --max-normalized-health-age-ms 30000 \\
    --expected-prewarm-windows 2; then
    collector_status_ok=1
    break
  fi
  sleep 1
done
if [ "\$collector_status_ok" -ne 1 ]; then
  exit 1
fi

POLYMARKET_API_PORT="\$PC_API_PORT" python3 - <<'PY'
import json
import os
import time
import urllib.request
from datetime import datetime, timezone

base = f"http://127.0.0.1:{os.environ['POLYMARKET_API_PORT']}"
required_generators = {
    "empirical_conditional",
    "block_bootstrap",
    "filtered_historical",
    "stress_overlay",
}


def get_json(path: str) -> dict[str, object]:
    with urllib.request.urlopen(base + path, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def probability_candidate_rows(payload: dict[str, object]) -> list[object]:
    candidates: list[object] = []
    for key in ("rows", "last_good_rows"):
        rows = payload.get(key)
        if isinstance(rows, list):
            candidates.extend(rows)
    return candidates


def row_is_recent(row: dict[str, object], now: datetime) -> bool:
    valid_until = parse_ts(row.get("valid_until"))
    if valid_until is not None and valid_until > now:
        return True
    generated_at = parse_ts(row.get("generated_at"))
    if generated_at is None:
        return False
    return (now - generated_at).total_seconds() <= 90


health = get_json("/health")
if health.get("status") != "ok":
    raise SystemExit(f"health smoke failed: {health}")

live = get_json("/api/runtime/live?limit=8")
if live.get("ok") is not True or not live.get("monitor", {}).get("orderbooks"):
    raise SystemExit(f"runtime live smoke failed: {live}")

probabilities = {}
for _ in range(30):
    try:
        probabilities = get_json("/api/runtime/probabilities?limit=8")
    except Exception as exc:
        probabilities = {"error": repr(exc)}
        time.sleep(1)
        continue
    probability_rows = probability_candidate_rows(probabilities)
    now = datetime.now(timezone.utc)
    has_recent_ensemble_row = any(
        isinstance(row, dict)
        and row.get("model_version") == "ensemble-v1"
        and row_is_recent(row, now)
        and required_generators.issubset(set(row.get("prior_fragment_generators") or []))
        for row in probability_rows
    )
    if (
        probabilities.get("ok") is True
        and probabilities.get("state") in {"OK", "NOWCAST"}
        and has_recent_ensemble_row
    ):
        break
    time.sleep(1)
else:
    raise SystemExit(f"runtime probabilities smoke failed: {probabilities}")

outcomes = get_json("/api/runtime/outcomes?limit=8")
if (
    not isinstance(outcomes.get("rows"), list)
    or outcomes.get("ok") is not True
    or outcomes.get("state") == "LOCKED"
):
    raise SystemExit(f"runtime outcomes smoke failed: {outcomes}")

with urllib.request.urlopen(
    base + "/api/runtime/live/stream?limit=8&interval_ms=250&max_events=1",
    timeout=15,
) as response:
    body = response.read().decode("utf-8")
if "event: live" not in body or "data: " not in body:
    raise SystemExit("runtime SSE smoke failed")
PY

docker compose --env-file deploy/collector/.env -f deploy/collector/docker-compose.yml ps
printf 'THEPC TUI installed %s\\n' "\$PC_BIN_DIR/polymarket-cockpit-tui"
printf 'THEPC TUI launcher installed %s\\n' "\$PC_BIN_DIR/open-polymarket-tui.sh"
printf 'THEPC deployed %s\\n' "\$FULL_SHA"
EOF
