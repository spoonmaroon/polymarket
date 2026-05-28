# Architecture Visualization

Date: 2026-05-28

These diagrams are editable Mermaid blocks. Obsidian, GitHub, and many Markdown
viewers can render them directly.

## Full System Map

```mermaid
flowchart TB
    subgraph Source["Source Plane"]
        PM["Polymarket CLOB<br/>markets, books, trades"]
        JUP["Jupiter Prediction API<br/>events, markets, orders"]
        CRYPTO["Crypto spot/candle feeds<br/>Coinbase/Binance-style APIs"]
        ORACLE["Settlement/oracle source<br/>Chainlink or venue rule"]
        ETF["BTC ETF options<br/>IBIT/FBTC/ARKB/BITB"]
    end

    subgraph Adapter["Adapter Plane"]
        VA["Venue Adapters<br/>normalize market/order data"]
        PA["Price Adapters<br/>settlement ticks + support feeds"]
        OA["OptionsContextAdapter<br/>GEX-style options features"]
    end

    subgraph State["State Plane"]
        MR["MarketRegistry<br/>active tradable universe"]
        RB["In-memory ring buffers<br/>ticks, books, candles"]
        LOG["Append-only raw event log"]
        COLD["Parquet cold store"]
        DUCK["DuckDB research queries"]
    end

    subgraph Features["Feature Plane"]
        VOL["VolatilityEngine<br/>10s/30s/120s/300s RV"]
        STRUCT["StructureFilter<br/>5m/15m support/resistance"]
        BOOK["BookState<br/>bid/ask/depth/spread"]
        LAT["LatencyState<br/>source and venue delay"]
    end

    subgraph Core["Decision Plane"]
        CPP["C++ probability_core<br/>p_finish, p_no_touch, edge, risk"]
    end

    subgraph Exec["Execution Plane"]
        POLICY["ExecutionPolicyRouter<br/>read-only / paper / supervised"]
        PAPER["PaperExecutionAdapter<br/>simulated executable fills"]
        LIVE["LiveExecutionAdapter<br/>disabled by default"]
        KILL["Kill switch + risk gates"]
    end

    subgraph Research["Research Plane"]
        LEDGER["Decision + fill ledger"]
        REPORTS["Calibration reports<br/>Brier/log-loss/ablation"]
        DASH["Dashboard/API<br/>inspection and monitoring"]
    end

    PM --> VA
    JUP --> VA
    CRYPTO --> PA
    ORACLE --> PA
    ETF --> OA

    VA --> MR
    VA --> BOOK
    PA --> RB
    OA --> RB

    MR --> CPP
    RB --> VOL
    RB --> STRUCT
    BOOK --> CPP
    VOL --> CPP
    STRUCT --> CPP
    LAT --> CPP
    RB --> LAT

    CPP --> POLICY
    POLICY --> PAPER
    POLICY --> LIVE
    KILL --> POLICY

    PAPER --> LEDGER
    LIVE --> LEDGER
    CPP --> LEDGER
    LEDGER --> COLD
    LOG --> COLD
    COLD --> DUCK
    DUCK --> REPORTS
    LEDGER --> REPORTS
    REPORTS --> DASH
```

## Hot Decision Loop

```mermaid
sequenceDiagram
    participant Venue as Venue Book WS/API
    participant Oracle as Settlement Price Feed
    participant Adapter as Python Adapters
    participant State as Ring Buffers
    participant Core as C++ probability_core
    participant Policy as Policy Router
    participant Paper as Paper Adapter
    participant Ledger as Append-only Ledger

    Venue->>Adapter: order book update
    Oracle->>Adapter: settlement-source tick
    Adapter->>State: normalize and update live state
    State->>Core: DecisionInput struct
    Core->>Core: p_finish + p_no_touch + edge + risk
    Core->>Policy: DecisionOutput + OrderIntent
    Policy->>Policy: legal, stale, kill, risk, calibration gates
    Policy->>Paper: route to paper fill simulation
    Paper->>Ledger: simulated fill / missed fill
    Core->>Ledger: decision record
```

## Compiled Core Boundary

```mermaid
flowchart LR
    subgraph Python["Python / IO / Research"]
        ADAPT["Adapters<br/>Polymarket, Jupiter, spot feeds"]
        STORE["Storage<br/>logs, Parquet, DuckDB"]
        REPORT["Reports<br/>calibration, ablation"]
        DASH["Dashboard/API"]
    end

    subgraph FFI["Stable FFI Boundary"]
        IN["DecisionInput"]
        OUT["DecisionOutput"]
    end

    subgraph CPP["C++ probability_core"]
        VOL["Rolling vol state"]
        PATH["Path probability<br/>p_finish / p_no_touch"]
        STRUCT["Structure checks"]
        EDGE["Fair value + edge"]
        RISK["Risk + order intent"]
    end

    ADAPT --> IN
    IN --> VOL
    VOL --> PATH
    PATH --> STRUCT
    STRUCT --> EDGE
    EDGE --> RISK
    RISK --> OUT
    OUT --> STORE
    STORE --> REPORT
    REPORT --> DASH
```

## Venue Adapter Normalization

```mermaid
flowchart TB
    PMRAW["Polymarket raw event/book/order data"]
    JUPRAW["Jupiter raw event/market/order data"]
    FUTURE["Future venue raw data"]

    PMA["PolymarketAdapter"]
    JUPA["JupiterPredictionAdapter"]
    FA["FutureVenueAdapter"]

    NORM["Normalized Internal Contracts"]

    MARKET["MarketRecord"]
    BOOK["OrderBookSnapshot"]
    FEE["FeeSchedule"]
    INTENT["OrderIntent"]
    FILL["FillReport"]

    PMRAW --> PMA
    JUPRAW --> JUPA
    FUTURE --> FA

    PMA --> NORM
    JUPA --> NORM
    FA --> NORM

    NORM --> MARKET
    NORM --> BOOK
    NORM --> FEE
    NORM --> INTENT
    NORM --> FILL
```

## Research Feedback Loop

```mermaid
flowchart LR
    OBS["Market observations"]
    DEC["Decisions"]
    FILL["Paper/live fills"]
    OUT["Final outcomes"]

    DATA["Research dataset<br/>joined by market_id + timestamp"]
    CAL["Calibration reports<br/>p_finish / p_no_touch"]
    COST["Cost reports<br/>spread, fees, slippage"]
    ABL["Ablation reports<br/>core vs structure vs ETF context"]
    EDIT["Architecture / config changes"]

    OBS --> DATA
    DEC --> DATA
    FILL --> DATA
    OUT --> DATA

    DATA --> CAL
    DATA --> COST
    DATA --> ABL

    CAL --> EDIT
    COST --> EDIT
    ABL --> EDIT
    EDIT --> OBS
```

## Execution Safety Gates

```mermaid
flowchart TB
    INTENT["OrderIntent from compiled core"]
    LEGAL{"Venue legal mode ok?"}
    MODE{"Execution mode allows action?"}
    KILL{"Kill switch clear?"}
    DATA{"Data fresh?"}
    RISK{"Risk limits ok?"}
    CAL{"Calibration gate passed?"}
    APPROVE{"Supervised approval required?"}

    REJECT["Reject + log reason"]
    PAPER["Paper route"]
    LIVE["Live route"]

    INTENT --> LEGAL
    LEGAL -- no --> REJECT
    LEGAL -- yes --> MODE
    MODE -- read_only --> REJECT
    MODE -- paper --> KILL
    MODE -- supervised_live --> KILL
    KILL -- no --> REJECT
    KILL -- yes --> DATA
    DATA -- no --> REJECT
    DATA -- yes --> RISK
    RISK -- no --> REJECT
    RISK -- yes --> CAL
    CAL -- no --> REJECT
    CAL -- yes --> APPROVE
    APPROVE -- paper mode --> PAPER
    APPROVE -- approved live --> LIVE
    APPROVE -- not approved --> REJECT
```

