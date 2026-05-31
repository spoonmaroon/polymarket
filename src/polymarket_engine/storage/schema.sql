CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS core;
CREATE SCHEMA IF NOT EXISTS features;
CREATE SCHEMA IF NOT EXISTS validation;

CREATE TABLE IF NOT EXISTS ops.ingest_files (
    file_id VARCHAR PRIMARY KEY,
    source_key VARCHAR NOT NULL,
    stream_key VARCHAR NOT NULL,
    partition_date DATE NOT NULL,
    partition_hour UTINYINT NOT NULL,
    path VARCHAR NOT NULL,
    sha256 VARCHAR NOT NULL,
    row_count UBIGINT NOT NULL,
    first_event_ts TIMESTAMPTZ,
    last_event_ts TIMESTAMPTZ,
    written_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ops.ingest_checkpoints (
    source_key VARCHAR NOT NULL,
    stream_key VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    last_event_ts TIMESTAMPTZ,
    last_sequence VARCHAR,
    updated_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source_key, stream_key, symbol)
);

CREATE TABLE IF NOT EXISTS core.contracts (
    contract_id VARCHAR PRIMARY KEY,
    venue VARCHAR NOT NULL,
    asset VARCHAR NOT NULL,
    side VARCHAR NOT NULL,
    threshold DOUBLE NOT NULL,
    expiry_ts TIMESTAMPTZ NOT NULL,
    settlement_source VARCHAR NOT NULL,
    token_id VARCHAR NOT NULL,
    rule_text VARCHAR NOT NULL,
    rule_hash VARCHAR NOT NULL,
    first_seen_ts TIMESTAMPTZ NOT NULL,
    last_seen_ts TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS core.contract_rules (
    market_id VARCHAR PRIMARY KEY,
    condition_id VARCHAR NOT NULL,
    slug VARCHAR NOT NULL,
    asset VARCHAR NOT NULL,
    contract_type VARCHAR NOT NULL,
    start_ts TIMESTAMPTZ NOT NULL,
    end_ts TIMESTAMPTZ NOT NULL,
    expiry_ts TIMESTAMPTZ NOT NULL,
    threshold_type VARCHAR NOT NULL,
    threshold_price DOUBLE,
    comparison_operator_up VARCHAR NOT NULL,
    comparison_operator_down VARCHAR NOT NULL,
    settlement_source_name VARCHAR NOT NULL,
    settlement_source_url VARCHAR NOT NULL,
    settlement_symbol VARCHAR NOT NULL,
    outcome_token_ids_json VARCHAR NOT NULL,
    rule_text VARCHAR NOT NULL,
    rule_hash VARCHAR NOT NULL,
    parser_version VARCHAR NOT NULL,
    accepted BOOLEAN NOT NULL,
    reject_reason VARCHAR,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS core.price_ticks (
    source_key VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    event_ts TIMESTAMPTZ NOT NULL,
    observed_ts TIMESTAMPTZ NOT NULL,
    price DOUBLE NOT NULL,
    bid DOUBLE,
    ask DOUBLE,
    sequence VARCHAR,
    raw_file_id VARCHAR,
    PRIMARY KEY (source_key, symbol, event_ts, observed_ts)
);

CREATE TABLE IF NOT EXISTS core.orderbook_snapshots (
    venue VARCHAR NOT NULL,
    contract_id VARCHAR NOT NULL,
    token_id VARCHAR NOT NULL,
    event_ts TIMESTAMPTZ NOT NULL,
    observed_ts TIMESTAMPTZ NOT NULL,
    best_bid DOUBLE,
    best_ask DOUBLE,
    bid_size_top DOUBLE,
    ask_size_top DOUBLE,
    spread DOUBLE,
    depth_json VARCHAR NOT NULL,
    raw_file_id VARCHAR,
    PRIMARY KEY (venue, token_id, event_ts, observed_ts)
);

CREATE TABLE IF NOT EXISTS features.asof_state_inputs (
    state_id VARCHAR PRIMARY KEY,
    contract_id VARCHAR NOT NULL,
    asof_ts TIMESTAMPTZ NOT NULL,
    asset VARCHAR NOT NULL,
    side VARCHAR NOT NULL,
    threshold DOUBLE NOT NULL,
    seconds_left DOUBLE NOT NULL,
    settlement_price DOUBLE NOT NULL,
    settlement_source_key VARCHAR NOT NULL,
    binance_price DOUBLE,
    coinbase_price DOUBLE,
    source_disagreement_bps DOUBLE,
    best_bid DOUBLE,
    best_ask DOUBLE,
    executable_price DOUBLE,
    spread DOUBLE,
    quote_age_ms DOUBLE,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS validation.contract_labels (
    contract_id VARCHAR PRIMARY KEY,
    resolved_side VARCHAR NOT NULL,
    settlement_price DOUBLE NOT NULL,
    settlement_ts TIMESTAMPTZ NOT NULL,
    label_source VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);
