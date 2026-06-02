FROM rust:1.91-bookworm AS builder

WORKDIR /app/rust

COPY rust/Cargo.toml rust/Cargo.lock ./
COPY rust/crates ./crates

RUN cargo build --release -p polymarket-live-probe

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /app/rust/target/release/polymarket-live-probe /usr/local/bin/polymarket-live-probe
COPY deploy/collector/collector-entrypoint.sh /usr/local/bin/collector-entrypoint.sh

RUN chmod 755 /usr/local/bin/collector-entrypoint.sh

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/collector-entrypoint.sh"]
