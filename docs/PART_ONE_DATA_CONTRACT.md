# Part One Data Contract

Part One collects only the data required to reconstruct BTC/ETH binary-contract states as they were observable at time `t`.

## Included

- Polymarket market metadata for BTC and ETH binary contracts.
- Polymarket executable order book data.
- Polymarket market WebSocket updates.
- Venue-supported Chainlink-style BTC/ETH price stream when available.
- Binance BTCUSDT and ETHUSDT proxy ticks.
- Coinbase BTC-USD and ETH-USD proxy ticks.

## Excluded

- ETF options / GEX context.
- Jupiter prediction markets.
- Direct Chainlink Data Streams.
- News/headline NLP.

## Database

Part One uses DuckDB plus Parquet.

- Parquet stores immutable raw source events.
- DuckDB stores normalized contracts, prices, order books, feature snapshots, labels, and replay metadata.
- Live runtime state stays in memory and is periodically persisted.

## Leakage Rule

When replaying a contract at time `t`, the engine may only use data timestamped at or before `t`.
Future BTC movement, final settlement, future Polymarket prices, and future order book changes are labels only.

## Chainlink Volatility Rule

For BTC/ETH binary contracts, realized volatility and `sigma_tau` are built from the
venue-named Chainlink reference stream only: `polymarket_rtds_chainlink`.
Coinbase, Binance, RTDS Binance, and any other exchange proxy are source-quality
diagnostics, not volatility inputs.

Historical proxy rows may only support validation when they exactly match the
same timestamped Chainlink value. Even then, they are not added as extra return
observations, because duplicate proxy rows would overweight one price move and
distort the volatility window. If Chainlink history is missing, the engine must
mark volatility as missing instead of silently substituting another feed.
