# Contract Rule Parser Design

## Purpose

The next build slice is a strict Polymarket crypto Up/Down rule parser. Before any probability model can be trusted, the engine must prove that it read the venue-defined contract correctly. The model does not choose the threshold, change the settlement source, alter the time window, or reinterpret the market after the fact.

This parser is deliberately narrow. It accepts current short-dated crypto Up/Down wording and a small set of equivalent phrases. Anything outside that grammar is rejected and logged with a reason.

## Supported Market Family

The first accepted family is Polymarket BTC/ETH/SOL short-dated Up/Down markets whose description states:

- "Up" wins if the asset price at the end of the title's time range is greater than or equal to the price at the beginning of that range.
- "Down" wins otherwise.
- The resolution source is Chainlink Data Streams for the relevant asset pair.

Equivalent wording may be accepted only when it preserves the same semantics. Examples:

- "greater than or equal to"
- "at or above"
- "not below"

The parser must not accept vague phrases such as "higher", "above", or "up" unless the tie rule is explicit elsewhere in the same rule text.

## Normalized Rule Object

The parser outputs a normalized rule object with these fields:

- `market_id`
- `condition_id`
- `slug`
- `asset`
- `contract_type`
- `start_ts`
- `end_ts`
- `expiry_ts`
- `threshold_type`
- `threshold_price`
- `comparison_operator_up`
- `comparison_operator_down`
- `settlement_source_name`
- `settlement_source_url`
- `settlement_symbol`
- `outcome_token_ids`
- `rule_text`
- `rule_hash`
- `parser_version`
- `accepted`
- `reject_reason`

For the current crypto Up/Down rule family:

- `contract_type = "crypto_up_down_start_price"`
- `threshold_type = "start_price"`
- `threshold_price = None`
- `comparison_operator_up = ">="`
- `comparison_operator_down = "<"`

This means `K` is not a fixed price from Gamma. `K` is the Chainlink start-reference price at the start timestamp. The model must later resolve that reference price from the settlement-source data.

## Required Validation

The parser accepts a market only if all of these are true:

- The asset is BTC, ETH, or SOL.
- The slug matches the asset and 5-minute Up/Down format.
- The market has exactly two outcomes: `Up` and `Down`.
- The token IDs match the outcomes one-to-one.
- The description contains the supported start-price rule.
- The description or metadata names the correct Chainlink Data Streams source.
- The source URL maps to the expected settlement symbol.
- The start and end times can be derived from `eventStartTime`, `endDate`, or the title.
- The end time and title window agree.
- The raw rule text can be hashed.

## Rejection Rules

The parser rejects a market if:

- Rule text is missing.
- Outcomes are not exactly `Up` and `Down`.
- Token IDs are missing or mismatched.
- Asset cannot be identified.
- Time window cannot be identified.
- Settlement source is missing, unsupported, or inconsistent with the asset.
- Tie rule is ambiguous.
- The rule appears to use a fixed threshold instead of start-reference comparison.
- The market family is not crypto Up/Down.

Rejected markets are not probability inputs. They may still be stored as raw market metadata for audit and future parser work.

## Live Collection And Latency

Information collection is fine latency-wise. The current collector already records Gamma metadata, CLOB order books, RTDS Chainlink updates, and Coinbase proxy ticks. A Dublin or London VPS can improve read-only collection latency and uptime by keeping WebSocket connections close to venue infrastructure.

Trading execution is separate from data collection. The parser and collector should remain useful whether the deployment runs locally, on a home server, or on a compliant low-latency VPS. Docker helps package the service, but persistence, secrets, clocks, restart policy, and health monitoring still need explicit deployment design.

## Out Of Scope

This slice does not:

- calculate probabilities;
- compute start-reference prices;
- place orders;
- connect authenticated accounts;
- parse non-crypto markets;
- parse arbitrary natural-language contracts.

The next step after this parser is an as-of contract-state builder that joins the normalized rule object to Chainlink/RTDS prices, CLOB quotes, and proxy feeds.
