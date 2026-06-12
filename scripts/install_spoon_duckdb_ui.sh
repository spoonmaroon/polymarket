#!/bin/zsh
emulate -L zsh
set -euo pipefail

SPOON_HOST="${SPOON_HOST:-spoon@100.126.126.1}"
REMOTE_SCRIPT="${POLYMARKET_DUCKDB_UI_REMOTE_SCRIPT:-/home/spoon/bin/open-polymarket-duckdb-ui.sh}"
REMOTE_PORT="${POLYMARKET_DUCKDB_UI_PORT:-4213}"

ssh "$SPOON_HOST" "bash -s" -- "$REMOTE_SCRIPT" <<'REMOTE'
set -euo pipefail

REMOTE_SCRIPT="$1"
REMOTE_SCRIPT_DIR="$(dirname "$REMOTE_SCRIPT")"
DATA_DIR="${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}"
SNAPSHOT_DIR="${POLYMARKET_DUCKDB_UI_SNAPSHOT_DIR:-$DATA_DIR/duckdb-ui}"
VIEWER_SCRIPT="$SNAPSHOT_DIR/polymarket_duckdb_viewer.py"

mkdir -p "$REMOTE_SCRIPT_DIR" "$SNAPSHOT_DIR"

cat > "$REMOTE_SCRIPT" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail

PORT="${POLYMARKET_DUCKDB_UI_PORT:-4213}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --port)
      PORT="$2"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

DATA_DIR="${POLYMARKET_DATA_DIR:-/home/spoon/polymarket-data}"
if [ -n "${POLYMARKET_REPO:-}" ]; then
  REPO="$POLYMARKET_REPO"
elif [ -d /home/spoon/polymarket-main ]; then
  REPO=/home/spoon/polymarket-main
else
  REPO=/home/spoon/polymarket
fi
SOURCE_DB="${POLYMARKET_DUCKDB_SOURCE_DB:-/home/spoon/polymarket-data/db/polymarket.duckdb}"
SNAPSHOT_DIR="${POLYMARKET_DUCKDB_UI_SNAPSHOT_DIR:-/home/spoon/polymarket-data/duckdb-ui}"
SNAPSHOT_DB="$SNAPSHOT_DIR/current-polymarket.duckdb"
# Default snapshot path: /home/spoon/polymarket-data/duckdb-ui/current-polymarket.duckdb
SNAPSHOT_TMP="$SNAPSHOT_DIR/snapshot.duckdb"
META_PATH="$SNAPSHOT_DIR/current-polymarket-meta.json"
LOG_DIR="$DATA_DIR/logs"
LOG_FILE="$LOG_DIR/duckdb-ui.log"
VIEWER_SCRIPT="$SNAPSHOT_DIR/polymarket_duckdb_viewer.py"
DUCKDB_BIN="${DUCKDB_BIN:-$HOME/.duckdb/cli/latest/duckdb}"

mkdir -p "$SNAPSHOT_DIR" "$LOG_DIR"

if ! command -v duckdb >/dev/null 2>&1 && [ ! -x "$DUCKDB_BIN" ]; then
  curl -fsSL https://install.duckdb.org | sh >> "$LOG_FILE" 2>&1
fi

if command -v duckdb >/dev/null 2>&1; then
  DUCKDB_BIN="$(command -v duckdb)"
elif [ -x "$DUCKDB_BIN" ]; then
  DUCKDB_BIN="$DUCKDB_BIN"
else
  echo "DuckDB CLI is not installed and could not be found" >&2
  exit 1
fi

if [ ! -f "$SOURCE_DB" ]; then
  echo "source DuckDB missing: $SOURCE_DB" >&2
  exit 1
fi

quote_sql_string() {
  printf "%s" "$1" | sed "s/'/''/g; s/^/'/; s/$/'/"
}

compose_command() {
  local args=()
  if [ -f deploy/collector/.env ]; then
    args+=(--env-file deploy/collector/.env)
  fi
  args+=(-f deploy/collector/docker-compose.yml)
  if [ -f deploy/collector/docker-compose.spoon-cpu-authority.yml ]; then
    args+=(-f deploy/collector/docker-compose.spoon-cpu-authority.yml)
  fi
  docker compose "${args[@]}" "$@"
}

compose_service_exists() {
  local service="$1"
  compose_command config --services 2>/dev/null | grep -Fx "$service" >/dev/null
}

STOPPED_DUCKDB_SERVICES=()
pause_duckdb_services() {
  if [ ! -d "$REPO" ] || [ ! -f "$REPO/deploy/collector/docker-compose.yml" ]; then
    echo "compose repo missing for DuckDB snapshot pause: $REPO" >> "$LOG_FILE"
    return 0
  fi
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker missing; skipping DuckDB snapshot service pause" >> "$LOG_FILE"
    return 0
  fi

  local services=()
  (
    cd "$REPO"
    for service in normalizer outcome-refresh; do
      if compose_service_exists "$service"; then
        services+=("$service")
      fi
    done
    if [ "${#services[@]}" -gt 0 ]; then
      compose_command stop "${services[@]}" >> "$LOG_FILE" 2>&1 || true
      printf '%s\n' "${services[@]}" > "$SNAPSHOT_DIR/stopped-services.txt"
    else
      : > "$SNAPSHOT_DIR/stopped-services.txt"
    fi
  )
  mapfile -t STOPPED_DUCKDB_SERVICES < "$SNAPSHOT_DIR/stopped-services.txt"
}

restart_duckdb_services() {
  if [ "${#STOPPED_DUCKDB_SERVICES[@]}" -eq 0 ]; then
    return 0
  fi
  if [ ! -d "$REPO" ] || ! command -v docker >/dev/null 2>&1; then
    return 0
  fi
  (
    cd "$REPO"
    compose_command up -d --no-deps "${STOPPED_DUCKDB_SERVICES[@]}" >> "$LOG_FILE" 2>&1 || true
  )
}

copy_snapshot() {
  local runner=()
  if command -v ionice >/dev/null 2>&1; then
    runner+=(ionice -c2 -n7)
  fi
  if command -v nice >/dev/null 2>&1; then
    runner+=(nice -n 10)
  fi
  rm -f "$SNAPSHOT_TMP" "$SNAPSHOT_TMP.wal"
  if ! "${runner[@]}" cp --reflink=auto --sparse=always "$SOURCE_DB" "$SNAPSHOT_TMP"; then
    "${runner[@]}" cp "$SOURCE_DB" "$SNAPSHOT_TMP"
  fi
  if [ -f "$SOURCE_DB.wal" ]; then
    if ! "${runner[@]}" cp --reflink=auto --sparse=always "$SOURCE_DB.wal" "$SNAPSHOT_TMP.wal"; then
      "${runner[@]}" cp "$SOURCE_DB.wal" "$SNAPSHOT_TMP.wal"
    fi
  fi
  sync -f "$SNAPSHOT_TMP" >/dev/null 2>&1 || true
}

pause_duckdb_services
trap restart_duckdb_services EXIT
copy_ok=0
for attempt in 1 2 3 4 5; do
  if copy_snapshot >> "$LOG_FILE" 2>&1; then
    copy_ok=1
    break
  fi
  echo "DuckDB snapshot copy failed on attempt $attempt; retrying" >> "$LOG_FILE"
  sleep "$attempt"
done
if [ "$copy_ok" != "1" ]; then
  echo "DuckDB snapshot copy failed after retries" >&2
  exit 1
fi
rm -f "$SNAPSHOT_DB.wal"
mv "$SNAPSHOT_TMP" "$SNAPSHOT_DB"
if [ -f "$SNAPSHOT_TMP.wal" ]; then
  mv "$SNAPSHOT_TMP.wal" "$SNAPSHOT_DB.wal"
fi
python3 - "$SOURCE_DB" "$SNAPSHOT_DB" "$META_PATH" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone

source, snapshot, meta = sys.argv[1:]
payload = {
    "source_host": "spoon",
    "source_db": source,
    "snapshot_db": snapshot,
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "source_mtime": datetime.fromtimestamp(os.stat(source).st_mtime, timezone.utc).isoformat(),
    "snapshot_mtime": datetime.fromtimestamp(os.stat(snapshot).st_mtime, timezone.utc).isoformat(),
}
with open(meta, "w", encoding="utf-8") as fh:
    json.dump(payload, fh, indent=2, sort_keys=True)
    fh.write("\n")
print("Snapshot generated", payload["generated_at"])
PY
restart_duckdb_services
trap - EXIT

pkill -f "polymarket_duckdb_viewer.py.*--port $PORT" >/dev/null 2>&1 || true
nohup python3 "$VIEWER_SCRIPT" --db "$SNAPSHOT_DB" --meta "$META_PATH" --duckdb-bin "$DUCKDB_BIN" --port "$PORT" < /dev/null >/dev/null 2>> "$LOG_FILE" &

for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/api/meta" >/dev/null 2>> "$LOG_FILE"; then
    echo "Polymarket DuckDB viewer ready at http://127.0.0.1:${PORT}"
    echo "Source: $SOURCE_DB"
    echo "Snapshot: $SNAPSHOT_DB"
    exit 0
  fi
  sleep 0.5
done

echo "Polymarket DuckDB viewer did not answer on http://127.0.0.1:${PORT}" >&2
exit 1
LAUNCHER
chmod 755 "$REMOTE_SCRIPT"

cat > "$VIEWER_SCRIPT" <<'DUCKDB_VIEWER_PY'
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
    meta_path = ""
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

    def load_meta(self) -> dict[str, object]:
        with open(self.meta_path, encoding="utf-8") as fh:
            return json.load(fh)

    def send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self) -> None:
        meta = self.load_meta()
        db_name = html.escape(os.path.basename(self.db_path))
        source_host = html.escape(str(meta.get("source_host", "unknown")))
        generated_at = html.escape(str(meta.get("generated_at", "unknown")))
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
  </style>
</head>
<body>
  <header><h1>Polymarket DuckDB</h1><div class="muted">Source: {source_host} | Snapshot generated: {generated_at} | Snapshot: {db_name}</div></header>
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
            elif parsed.path == "/api/meta":
                self.send_json(self.load_meta())
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
    parser.add_argument("--meta", required=True)
    parser.add_argument("--duckdb-bin", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    Viewer.db_path = args.db
    Viewer.meta_path = args.meta
    Viewer.duckdb_bin = args.duckdb_bin
    ThreadingHTTPServer(("127.0.0.1", args.port), Viewer).serve_forever()


if __name__ == "__main__":
    main()
DUCKDB_VIEWER_PY
chmod 755 "$VIEWER_SCRIPT"
REMOTE

if [[ "${POLYMARKET_DUCKDB_UI_INSTALL_ONLY:-0}" == "1" ]]; then
  echo "Spoon DuckDB UI helper installed at $REMOTE_SCRIPT"
  exit 0
fi

ssh "$SPOON_HOST" "$REMOTE_SCRIPT --port $REMOTE_PORT"
