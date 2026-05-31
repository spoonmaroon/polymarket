# Part Two Live Collectors

Part Two turns the Part One data foundation into read-only live collection.

## First Supported Command

```bash
mkdir -p data/raw
touch data/raw/.polymarket_archive_root
uv run polymarket-engine collect --assets BTC,ETH --duration 10
```

The first network runner records:

- Polymarket BTC/ETH 5-minute market snapshots discovered by deterministic slugs.
- Polymarket CLOB order book snapshots for Up and Down token ids.
- Coinbase BTC/ETH ticker updates for live proxy price movement.
- Polymarket RTDS/reference updates when the RTDS stream emits messages.
- DuckDB ingest-file rows under `ops.ingest_files`.
- Immutable raw Parquet files under `data/raw/`.

## Source Rules

- Polymarket website chart prices are not model truth.
- Coinbase is the first live proxy feed for BTC/ETH price movement.
- Polymarket RTDS is the first settlement/reference feed candidate.
- Binance.com is disabled by default on this machine because it returned `HTTP 451`.
- Every source event must preserve both source timestamp and local receive timestamp.
- Raw writes are crash-durable: `.parquet.tmp` files are atomically published and orphaned temporary files are cleaned at startup.
- WebSocket outages are handled with capped reconnect backoff; other sources continue running when one feed disconnects.

## Safety

Part Two does not trade, does not build model probabilities, and does not place orders.
