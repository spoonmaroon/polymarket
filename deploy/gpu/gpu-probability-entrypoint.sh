#!/usr/bin/env sh
set -eu

DB_PATH="${POLYMARKET_DUCKDB_PATH:-/var/lib/polymarket/db/polymarket.duckdb}"
PROBABILITY_STATUS_PATH="${POLYMARKET_PROBABILITY_STATUS_PATH:-/var/lib/polymarket/live/probabilities.json}"
PROBABILITY_INPUTS_PATH="${POLYMARKET_PROBABILITY_INPUTS_PATH:-/var/lib/polymarket/live/probability_inputs.json}"
INTERVAL_SECONDS="${POLYMARKET_CUDA_PROBABILITY_INTERVAL_SECONDS:-1.0}"
LIMIT="${POLYMARKET_CUDA_PROBABILITY_LIMIT:-24}"
VALID_SECONDS="${POLYMARKET_CUDA_PROBABILITY_VALID_SECONDS:-30}"
MAX_INPUT_SNAPSHOT_AGE_SECONDS="${POLYMARKET_CUDA_PROBABILITY_MAX_INPUT_SNAPSHOT_AGE_SECONDS:-10.0}"

exec polymarket-engine run-cuda-probability-worker \
  --duckdb-path "$DB_PATH" \
  --probability-status-path "$PROBABILITY_STATUS_PATH" \
  --probability-inputs-path "$PROBABILITY_INPUTS_PATH" \
  --interval-seconds "$INTERVAL_SECONDS" \
  --limit "$LIMIT" \
  --valid-seconds "$VALID_SECONDS" \
  --max-input-snapshot-age-seconds "$MAX_INPUT_SNAPSHOT_AGE_SECONDS"
