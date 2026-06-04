# Outcomes Day Sections And Backfill Design

## Goal

Make the cockpit Outcomes tab usable as history grows by allowing the current day to be closed, splitting expanded days into larger local-time sections, and backfilling missing official outcomes and `K` strike values without adding load to the fast runtime path.

## Current Behavior

The TUI already groups outcome rows by local day and supports Enter or Space to toggle a selected day header. The latest day is also treated as expanded by default inside the display helper. That means today's day group is effectively forced open: removing it from the expanded set does not close it because the helper reopens the first group.

Outcome rows come from `/api/runtime/outcomes`, which normally reads the normalizer-owned `data/live/outcomes.json` status file. That is the right low-contention runtime path. Deep historical repair should not be pushed into the normalizer's 0.1 second loop.

## Display Model

The Outcomes tab should render a visible tree:

```text
- Jun 04 2026 (288)
   - Overnight 00:00-05:59 (72)
      BTC 5m 00:05  K 63500.12  Winner UP  resolved
      ETH 5m 00:05  K 1793.71   Winner DOWN resolved
   + Morning 06:00-11:59 (72)
   + Afternoon 12:00-17:59 (72)
   + Evening 18:00-23:59 (72)
+ Jun 03 2026 (288)
```

Rows are based on local expiry time, not UTC display time. Storage remains UTC.

The default state should be:

- current local day expanded on first load;
- current day can be closed and stays closed until reopened;
- older days collapsed on first load;
- when a day is expanded for the first time, its current or most recent section is expanded by default;
- section expansion state is independent per day.

Use four fixed local-day sections:

- `Overnight 00:00-05:59`
- `Morning 06:00-11:59`
- `Afternoon 12:00-17:59`
- `Evening 18:00-23:59`

This gives predictable navigation and keeps a full day compact without making one bucket per 5-minute contract.

## Navigation Rules

Up and Down should move only across visible display rows. Selection must never point at rows hidden inside a collapsed day or collapsed section.

Enter or Space toggles the selected expandable row:

- on a day row, expand or collapse that day;
- on a section row, expand or collapse that section;
- on an outcome row, do nothing.

After collapsing a day or section containing the selected child, selection should land on the collapsed header row. After runtime updates or backfill changes the row set, selection should clamp to a visible row.

## Backfill Scope

Backfill should repair both missing fields:

- official winner fields from the Polymarket/CLOB market payload where Up or Down is marked `winner=true`;
- `K` strike fields from the Chainlink reference observation at or before the contract start timestamp, using both source event time and local observed time as as-of bounds.

Backfill must not compute an official winner locally from Chainlink prices. Local price comparisons can be stored only as diagnostics if a later design explicitly enables that; source-of-truth outcome labels stay official-only.

## K Update Semantics

For one contract, `K` is a start-reference value, not a live moving target. It should not update every time BTC or ETH moves. It may change only from missing to resolved when the system later observes or backfills the valid Chainlink start-reference tick for that contract.

The live target cache should keep retrying missing `K` values for current and recently expired windows by using the same bounded as-of lookup:

- if `asof_ts < start_ts`, show `pending`;
- if no Chainlink tick exists with `event_ts <= start_ts` and `observed_ts <= asof_ts`, keep `pending`;
- once such a tick exists, persist and display that `K`;
- after `K` is resolved for a contract, future refreshes must not replace it with later market prices.

The backfill command should use the same rule for historical rows. This repairs old `pending` values without letting historical `K` drift.

## Backfill Execution Model

Backfill should be an explicit offline command or maintenance job, not a fast-loop side effect. The live normalizer should keep its small pending-outcome sweeper for recently expired markets, while the backfill command walks older recorded markets in bounded batches.

The command should support:

- a start/end expiry date range;
- a limit for batch size;
- a dry-run mode that reports missing `K`, pending official winners, and rows repaired;
- a write mode that upserts repaired rows into `validation.market_outcome_history` and rewrites `data/live/outcomes.json`;
- source limits and request timeouts so Polymarket metadata fetches cannot hang a maintenance run.

Recommended first command shape:

```bash
uv run polymarket-engine backfill-outcomes \
  --duckdb-path data/db/polymarket.duckdb \
  --outcomes-path data/live/outcomes.json \
  --start-date 2026-06-01 \
  --end-date 2026-06-04 \
  --limit 500 \
  --write
```

## Runtime API Behavior

`/api/runtime/outcomes` should remain read-only and low-contention. It can keep reading `data/live/outcomes.json` first. The status file should contain enough rows for day grouping, and the TUI should request a larger history limit without increasing the live SSE cadence.

If the status file is missing or invalid, the API may fall back to a bounded read from DuckDB. That fallback is for debugging and recovery, not the normal TUI path.

## Tests

The implementation should be test-first:

- TUI display helper tests proving today can be collapsed;
- TUI display helper tests proving day rows contain section rows and section rows contain outcomes;
- state tests proving Up and Down selection only moves over visible rows;
- render tests proving day and section headers show counts and expanded/collapsed markers;
- Python target-cache tests proving missing current-window `K` is retried until the valid start-reference tick appears;
- Python target-cache and outcome tests proving resolved `K` does not drift to later BTC/ETH prices;
- Python validation tests proving backfill fills missing `K` without computing official winners;
- Python validation tests proving official winners are still sourced only from Polymarket/CLOB payloads;
- CLI tests proving dry-run does not mutate DuckDB or the status file;
- API tests proving a larger status file can be limited by the query parameter.

## Out Of Scope

This design does not change the Market tab, order-book rendering, live trading, paper trading, probability modeling, or Monte Carlo work. It also does not add local outcome computation as truth.
