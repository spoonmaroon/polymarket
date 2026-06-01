#!/usr/bin/env sh
set -eu

set -- collect \
  --assets "${POLYMARKET_ASSETS:-BTC,ETH}" \
  --intervals "${POLYMARKET_INTERVALS:-5m,15m}" \
  --forever \
  --windows-to-track "${POLYMARKET_WINDOWS_TO_TRACK:-2}" \
  --raw-root /var/lib/polymarket/raw \
  --duckdb-path /var/lib/polymarket/db/polymarket.duckdb \
  --status-path /var/lib/polymarket/live/status.json \
  --max-batch-size "${POLYMARKET_MAX_BATCH_SIZE:-100}" \
  --snapshot-interval "${POLYMARKET_REST_SNAPSHOT_INTERVAL:-1}" \
  --clob-rest-backup-interval "${POLYMARKET_CLOB_REST_BACKUP_INTERVAL:-15}" \
  --market-refresh-interval "${POLYMARKET_MARKET_REFRESH_INTERVAL:-30}" \
  --market-fetch-timeout "${POLYMARKET_MARKET_FETCH_TIMEOUT:-10}" \
  --display-timezone "${POLYMARKET_DISPLAY_TZ:-America/Chicago}"

if [ "${POLYMARKET_ENABLE_CLOB_WEBSOCKET:-1}" = "0" ]; then
  set -- "$@" --disable-clob-websocket
fi

exec polymarket-engine "$@"
