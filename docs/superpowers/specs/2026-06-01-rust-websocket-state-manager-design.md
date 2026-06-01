# Rust WebSocket State Manager Design

## Goal

Replace the one-shot Rust probe with an always-on runtime state manager that keeps BTC and ETH 5-minute Polymarket binary contracts warm before rollover. The hot decision path must use already-known contract IDs, already-subscribed CLOB market WebSocket order-book state, and already-fresh Chainlink RTDS reference prices.

## Design Summary

The system should not discover the market after a contract becomes current. BTC/ETH 5-minute contract slugs are deterministic, so the runtime can predict current, next, and next-next windows from UTC epoch time. It should prefetch token IDs by Gamma before the window is needed, subscribe to those token IDs on the CLOB market WebSocket, and keep top-of-book state in memory.

At rollover, the manager swaps pointers: `next` becomes `current`, `next_next` becomes `next`, and a new future window begins warming. REST remains allowed for startup, token discovery, and backup snapshots. REST must not be required during the rollover decision path.

## Data Sources

### Primary Settlement/Reference Price

- Source: Polymarket RTDS Chainlink stream.
- Symbols: `btc/usd` and `eth/usd`.
- Use: settlement reference state, realized volatility input, `sigma_tau` input, price freshness.
- Rule: this source is the truth for BTC/ETH contract scoring. Proxy feeds cannot replace it.

### Proxy Price Checks

- Source: RTDS Binance proxy stream when available, plus external proxy sources only outside the hot path.
- Symbols: `btcusdt` and `ethusdt` for Binance proxy.
- Use: source disagreement and diagnostics.
- Rule: proxy feeds may block, warn, or increase required edge later. They must not drive settlement reference price or volatility.

### Polymarket Contract Discovery

- Source: deterministic slug construction plus Gamma token lookup.
- Slug pattern: `{asset-lower}-updown-{interval}-{window_start_epoch}`.
- Assets: `BTC`, `ETH`.
- Intervals: first target is `5m`; `15m` should stay supported by the same window math.
- Use: resolve token IDs and rule metadata before the window is needed.

### Polymarket Order Book

- Primary source: Polymarket CLOB market WebSocket.
- Subscribe by asset IDs/token IDs for BTC/ETH UP/DOWN across current, next, and next-next windows.
- Event types to handle: `book`, `price_change`, `best_bid_ask`.
- Dynamic behavior: subscribe to new future token IDs before rollover and unsubscribe expired token IDs after their labels are no longer needed.
- Backup source: REST order-book snapshots on a slow interval for repair and reconciliation.

## Runtime Model

The runtime holds a `WarmState`:

- `current`: current window contracts and in-memory books.
- `next`: next window contracts and in-memory books.
- `next_next`: next-next window contracts and in-memory books.
- `chainlink_prices`: latest BTC/USD and ETH/USD RTDS ticks with observed timestamp and source timestamp.
- `proxy_prices`: optional BTC/USDT and ETH/USDT diagnostics.
- `subscription_state`: token IDs currently subscribed on the CLOB market WebSocket.
- `health`: feed freshness, reconnect count, API round-trip metrics, and rollover status.

The manager runs four loops:

1. Window scheduler: computes current/next/next-next windows and triggers prefetch before rollover.
2. Contract resolver: resolves token IDs from Gamma for missing future windows.
3. CLOB market WebSocket loop: maintains one long-lived market channel subscription and updates normalized top-of-book state.
4. RTDS price loop: maintains Chainlink BTC/USD and ETH/USD state, plus optional proxy diagnostics.

## Rollover Behavior

The manager should prewarm future contracts before they are active. For a 5-minute contract, the next contract should be fully resolved and subscribed at least 30 seconds before the current contract expires. If the next contract is not warmed by the configured cutoff, the runtime should set a health flag and block live trading for that rollover.

Rollover should be a local state transition:

```text
current <- next
next <- next_next
next_next <- newly resolved future window
```

The transition must not depend on a fresh REST call. REST can repair missing data after the fact, but it cannot be a requirement for being ready at the boundary.

## Latency Target

Spoon measurements showed REST request/response is too slow for execution. The state manager is designed so the hot path reads from memory:

- Chainlink state: latest RTDS tick already stored.
- Order book state: latest CLOB WebSocket book/best-bid-ask already stored.
- Contract state: current and next token IDs already known.

Expected hot-path latency should be dominated by local computation and order submission, not market discovery or order-book fetching.

## Failure Rules

- If Chainlink BTC/USD or ETH/USD is stale, block affected asset decisions.
- If CLOB book state is stale, block affected contract decisions.
- If current contract exists but next contract is not warmed before cutoff, block rollover execution.
- If proxy price disagrees materially with Chainlink, flag source disagreement but do not replace Chainlink.
- If WebSocket disconnects, reconnect with capped backoff and continue using stale flags to gate decisions.
- If REST backup disagrees with WebSocket top of book, log discrepancy and prefer the freshest valid source only after validation.

## Test Strategy

The implementation should be test-first:

- deterministic window math;
- current/next/next-next contract scheduling;
- CLOB subscription payload generation;
- CLOB event parsing for `book`, `price_change`, and `best_bid_ask`;
- in-memory book update behavior;
- RTDS Chainlink parsing for BTC/USD and ETH/USD;
- rollover without REST in the hot transition;
- stale-feed blocking flags;
- live smoke that proves the state manager can warm BTC/ETH current and next windows.

## Out Of Scope

This slice does not implement live trading, probability modeling, order signing, or market making. It builds the low-latency state layer those systems need.

