# Graph Report - /Users/goon/polymarket  (2026-06-01)

## Corpus Check
- 165 files · ~367,769 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1480 nodes · 2241 edges · 90 communities (71 shown, 19 thin omitted)
- Extraction: 78% EXTRACTED · 21% INFERRED · 1% AMBIGUOUS · INFERRED: 481 edges (avg confidence: 0.76)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Replay Storage Core|Replay Storage Core]]
- [[_COMMUNITY_Probability Methodology|Probability Methodology]]
- [[_COMMUNITY_Raw Event Writer|Raw Event Writer]]
- [[_COMMUNITY_Live Collector Runtime|Live Collector Runtime]]
- [[_COMMUNITY_Collector CLI|Collector CLI]]
- [[_COMMUNITY_Polymarket Adapters|Polymarket Adapters]]
- [[_COMMUNITY_Decision Gates|Decision Gates]]
- [[_COMMUNITY_Live BTC Inputs|Live BTC Inputs]]
- [[_COMMUNITY_Volatility Snapshot|Volatility Snapshot]]
- [[_COMMUNITY_Contract Discovery|Contract Discovery]]
- [[_COMMUNITY_XGBoost Ablations|XGBoost Ablations]]
- [[_COMMUNITY_Walk Forward Monte Carlo|Walk Forward Monte Carlo]]
- [[_COMMUNITY_Pricing Formulas|Pricing Formulas]]
- [[_COMMUNITY_Monte Carlo Outputs|Monte Carlo Outputs]]
- [[_COMMUNITY_Cached Probability Grid|Cached Probability Grid]]
- [[_COMMUNITY_Live Shadow Backtest|Live Shadow Backtest]]
- [[_COMMUNITY_Validation Metrics|Validation Metrics]]
- [[_COMMUNITY_Noise Controls|Noise Controls]]
- [[_COMMUNITY_Contract Rule Parser|Contract Rule Parser]]
- [[_COMMUNITY_Settlement Source Contract|Settlement Source Contract]]
- [[_COMMUNITY_Replay Labels|Replay Labels]]
- [[_COMMUNITY_Reference Bibliography|Reference Bibliography]]
- [[_COMMUNITY_Monitoring Plan|Monitoring Plan]]
- [[_COMMUNITY_Terminal Monitor|Terminal Monitor]]
- [[_COMMUNITY_Monte Carlo Variables|Monte Carlo Variables]]
- [[_COMMUNITY_Project Thesis|Project Thesis]]
- [[_COMMUNITY_Settlement Hierarchy|Settlement Hierarchy]]
- [[_COMMUNITY_Decision Layer Outputs|Decision Layer Outputs]]
- [[_COMMUNITY_TypeScript Config|TypeScript Config]]
- [[_COMMUNITY_Baseline Probabilities|Baseline Probabilities]]
- [[_COMMUNITY_Spoon Deployment|Spoon Deployment]]
- [[_COMMUNITY_RTDS Price Feed|RTDS Price Feed]]
- [[_COMMUNITY_Path Survival Outputs|Path Survival Outputs]]
- [[_COMMUNITY_As Of State|As Of State]]
- [[_COMMUNITY_Scope Diagram|Scope Diagram]]
- [[_COMMUNITY_External Review Outputs|External Review Outputs]]
- [[_COMMUNITY_Risk Gate Layout|Risk Gate Layout]]
- [[_COMMUNITY_Coinbase Feed|Coinbase Feed]]
- [[_COMMUNITY_Data Contract|Data Contract]]
- [[_COMMUNITY_Collector Status JSON|Collector Status JSON]]
- [[_COMMUNITY_Health Status JSON|Health Status JSON]]
- [[_COMMUNITY_Runtime Status JSON|Runtime Status JSON]]
- [[_COMMUNITY_System Map|System Map]]
- [[_COMMUNITY_Execution Risk|Execution Risk]]
- [[_COMMUNITY_Fast Path Architecture|Fast Path Architecture]]
- [[_COMMUNITY_Build Plan|Build Plan]]
- [[_COMMUNITY_External Data Sources|External Data Sources]]
- [[_COMMUNITY_Literature Thesis|Literature Thesis]]
- [[_COMMUNITY_C++ Probability Core|C++ Probability Core]]
- [[_COMMUNITY_Read Only Operations|Read Only Operations]]
- [[_COMMUNITY_Reconnect Backoff|Reconnect Backoff]]
- [[_COMMUNITY_Atomic Writes|Atomic Writes]]
- [[_COMMUNITY_Binance Feed|Binance Feed]]
- [[_COMMUNITY_Calibration Protocol|Calibration Protocol]]
- [[_COMMUNITY_Robust Volatility Literature|Robust Volatility Literature]]
- [[_COMMUNITY_Monte Carlo Diagnostics|Monte Carlo Diagnostics]]
- [[_COMMUNITY_Status Checker|Status Checker]]
- [[_COMMUNITY_Migration Script Tests|Migration Script Tests]]
- [[_COMMUNITY_Retention Policy|Retention Policy]]
- [[_COMMUNITY_Path Generation Defaults|Path Generation Defaults]]
- [[_COMMUNITY_Install Scripts|Install Scripts]]
- [[_COMMUNITY_Apple Toolchain|Apple Toolchain]]
- [[_COMMUNITY_Time Step Decision|Time Step Decision]]
- [[_COMMUNITY_React Entry Point|React Entry Point]]
- [[_COMMUNITY_Domain Package|Domain Package]]
- [[_COMMUNITY_Ingest Package|Ingest Package]]
- [[_COMMUNITY_Closed Form Checks|Closed Form Checks]]
- [[_COMMUNITY_Tradability Guardrail|Tradability Guardrail]]
- [[_COMMUNITY_Path Distribution|Path Distribution]]
- [[_COMMUNITY_Robust Mid Price|Robust Mid Price]]
- [[_COMMUNITY_ETF Vol Check|ETF Vol Check]]
- [[_COMMUNITY_ETF Flow Features|ETF Flow Features]]
- [[_COMMUNITY_Feed Jump Controls|Feed Jump Controls]]
- [[_COMMUNITY_Option Context Blocks|Option Context Blocks]]
- [[_COMMUNITY_ETF IV Change|ETF IV Change]]
- [[_COMMUNITY_Engine Package|Engine Package]]
- [[_COMMUNITY_Feature Package|Feature Package]]
- [[_COMMUNITY_Venue Package|Venue Package]]
- [[_COMMUNITY_Storage Package|Storage Package]]
- [[_COMMUNITY_Exit Strategy Machine|Exit Strategy Machine]]

## God Nodes (most connected - your core abstractions)
1. `DuckDbIngestStore` - 53 edges
2. `PriceObservation` - 48 edges
3. `build_decision_state()` - 22 edges
4. `build_volatility_snapshot()` - 22 edges
5. `LiveCollectorConfig` - 21 edges
6. `CollectorEvent` - 20 edges
7. `parse_polymarket_crypto_updown_rule()` - 20 edges
8. `compilerOptions` - 16 edges
9. `BufferedRawEventWriter` - 16 edges
10. `OrderBookObservation` - 16 edges

## Surprising Connections (you probably didn't know these)
- `Generated Multi-Asset Research Paper PDF` --semantically_similar_to--> `Multi-Asset Remaining-Path Probability Framework`  [EXTRACTED] [semantically similar]
  reports/generated/BTC_Binary_Path_Probability_Incomplete_Research_Paper.pdf → graphify-out/converted/BTC_Binary_Path_Probability_Incomplete_Research_Paper_d182482a.md
- `Generated External Review Brief PDF` --semantically_similar_to--> `External Review Strategy Brief`  [EXTRACTED] [semantically similar]
  reports/generated/BTC_Binary_Path_Probability_Review_Brief.pdf → graphify-out/converted/BTC_Binary_Path_Probability_Review_Brief_bf321672.md
- `Cached Monte Carlo Probability Grids` --semantically_similar_to--> `DecisionState`  [AMBIGUOUS] [semantically similar]
  graphify-out/converted/BTC_Binary_Path_Probability_Incomplete_Research_Paper.before_mc_vars_etf_plan_20260530_5d7d3414.md → docs/superpowers/plans/2026-06-01-section-4-volatility-sigma-tau.md
- `QA Rewrite BTC Binary Path Probability Paper PDF` --semantically_similar_to--> `BTC Remaining-Path Probability Framework Draft`  [EXTRACTED] [semantically similar]
  reports/generated/qa_4_5_4_6_rewrite/Polymarket idea.pdf → graphify-out/converted/BTC_Binary_Path_Probability_Incomplete_Research_Paper.before_zpath_formula_reconcile_20260529_171225_de80cdf3.md
- `Decision Gates Figure` --semantically_similar_to--> `Review Brief Decision Policy Tree`  [EXTRACTED] [semantically similar]
  reports/generated/visual_qa_contract_state/BTC_Binary_Path_Probability_Incomplete_Research_Paper.pdf → graphify-out/converted/BTC_Binary_Path_Probability_Review_Brief_bf321672.md

## Hyperedges (group relationships)
- **Remaining Path Decision Outputs** — binary_core_probability_outputs, binary_p_finish, binary_p_no_touch, binary_z_path, binary_sigma_tau, binary_edge_after_costs, external_review_decision_policy_tree [EXTRACTED 1.00]
- **As-Of Replay Storage Contract** — part_one_leakage_rule, binary_asof_state_construction, decision_state_storage_plan, raw_parquet_event_lake, duckdb_normalized_store, asof_replay_labels, binary_validation_ablation [INFERRED 0.88]
- **Spoon Read-Only Collector Stack** — part_two_live_collectors, clob_ws_primary_orderbook_feed, dockercompose_collector_service, dockercompose_persistent_data_mounts, spoon_deployment_runbook, terminal_monitor_status_file [INFERRED 0.86]
- **Core Probability Outputs** — btc_paper_p_finish, btc_paper_p_no_touch, btc_paper_z_path, btc_paper_sigma_tau, btc_paper_executable_edge [EXTRACTED 0.93]
- **As-Of State Construction Pipeline** — contract_rule_parser_design_strict_parser, contract_rule_parser_design_normalized_rule_object, prior_distribution_settlement_source_hierarchy, section4_plan_price_ticks_before, section4_plan_volatility_snapshot, section4_plan_decision_state [INFERRED 0.78]
- **Calibration and Validation Stack** — btc_paper_asof_monte_carlo, btc_paper_cached_probability_grids, btc_paper_live_shadow_backtester, btc_paper_xgboost_challenger, prior_distribution_calibration_metrics [EXTRACTED 0.84]
- **Core Probability Outputs** — incomplete_p_finish, incomplete_p_no_touch, incomplete_z_path, incomplete_sigma_tau, incomplete_executable_edge [EXTRACTED 0.97]
- **As-Of Shadow Validation Loop** — incomplete_as_of_state_builder, incomplete_monte_carlo_path_generators, before_deterministic_decision_tree, visual_live_shadow_backtester, incomplete_validation_metrics [EXTRACTED 0.90]
- **Market Data And Context Stack** — incomplete_settlement_source_hierarchy, incomplete_polymarket_order_book_execution_model, binance_coinbase_kraken_feeds, before_etf_options_context, before_gex_infrastructure_reuse [EXTRACTED 0.88]
- **Shared Variable Set** — page_4_shared_variables, page_4_k_threshold, page_4_s_t_current_price, page_4_s_t_final_price, page_4_tau_time_to_expiry, page_4_side_contract_direction, page_4_s_u_simulated_path, page_4_n_simulated_paths, page_4_sigma_tau_expected_log_movement, page_4_p_exec_executable_contract_price, page_4_indicator_function [EXTRACTED 1.00]
- **Monte Carlo Probability Outputs** — page_4_monte_carlo_primary_estimator, page_4_p_finish, page_4_p_no_touch, page_4_s_u_simulated_path, page_4_k_threshold, page_4_danger_line [EXTRACTED 1.00]
- **As-Of Backtest Guardrail** — page_4_anti_overfit_principle, page_4_as_of_replay_data_rule, page_4_future_outcomes_labels_only, page_4_first_historical_backtest_exclusion, page_4_settlement_source_price_guardrail [EXTRACTED 1.00]
- **p_finish Monte Carlo Calculation** — page_5_p_finish, page_5_simulated_terminal_price, page_5_win_indicator, page_5_p_finish_mc_formula, page_5_terminal_win_probability [EXTRACTED 1.00]
- **p_no_touch Monte Carlo Calculation** — page_5_p_no_touch, page_5_full_simulated_btc_path, page_5_survive_indicator, page_5_p_no_touch_mc_formula, page_5_path_survival_probability, page_5_danger_line [EXTRACTED 1.00]
- **Trade Entry Guardrail System** — page_5_p_finish_mc_formula, page_5_p_no_touch_mc_formula, page_5_raw_pre_cost_fair_value, page_5_non_probability_trade_inputs, page_5_not_tradable_decision_guardrail, page_5_decision_layer_wait_block_edge [EXTRACTED 1.00]
- **z_path Normalization Formula Group** — page_6_z_path, page_6_d_side, page_6_d_up_formula, page_6_d_down_formula, page_6_z_path_formula, page_6_sigma_tau [EXTRACTED 1.00]
- **Speed Layer Design Group** — page_6_fast_per_tick_update, page_6_cached_monte_carlo_grid, page_6_lookup_interpolation, page_6_conditional_full_refresh, page_6_cached_monte_carlo_conditional_refresh [EXTRACTED 1.00]
- **Closed-Form Sanity Check Group** — page_6_closed_form_baselines_sanity_checks, page_6_p_finish_formula, page_6_terminal_probability_sanity_check, page_6_p_no_touch_formula, page_6_driftless_no_touch_baseline [EXTRACTED 1.00]
- **Decision Layer Output Bundle** — page_7_core_outputs_decision_layer, page_7_p_finish_mc, page_7_p_no_touch_mc, page_7_z_path, page_7_mc_uncertainty, page_7_raw_fair_value, page_7_edge_before_costs, page_7_decision_layer [EXTRACTED 1.00]
- **As-Of State Construction** — page_7_market_state_inputs_state_construction, page_7_raw_btc_data, page_7_venue_market_data, page_7_etf_options_context, page_7_decision_time_t, page_7_clean_as_of_state, page_7_probability_engine [EXTRACTED 1.00]
- **Settlement Source Validation** — page_7_settlement_source_price_inputs, page_7_settlement_source_price, page_7_validated_proxy, page_7_source_timestamp, page_7_source_dislocation, page_7_source_quality_flag, page_7_wrong_proxy_target_risk [EXTRACTED 1.00]
- **Live BTC State Inputs** — page_8_btc_spot_feeds, page_8_trade_or_quote_timestamp, page_8_robust_s_t, page_8_feed_disagreement, page_8_data_granularity, page_8_live_btc_tick_inputs [EXTRACTED 1.00]
- **sigma_tau Window Blend** — page_8_short_window_realized_vol, page_8_medium_window_realized_vol, page_8_longer_window_realized_vol, page_8_regime_multiplier, page_8_sigma_tau_weighted_blend_formula, page_8_sigma_tau [EXTRACTED 1.00]
- **Order-Book Execution Guardrails** — page_8_best_bid_best_ask, page_8_spread, page_8_available_depth, page_8_quote_age, page_8_prediction_market_inputs [EXTRACTED 1.00]
- **ETF Context Adjustment Targets** — page_9_etf_options_context_layer, page_9_sigma_tau, page_9_p_no_touch, page_9_model_uncertainty, page_9_required_edge [EXTRACTED 1.00]
- **Noise Control First Treatment Matrix** — page_9_data_quality_noise_controls, page_9_bad_tick_feed_jump, page_9_bid_ask_bounce, page_9_low_volatility_false_calm, page_9_high_volatility_regime_shift, page_9_small_path_bucket, page_9_latency_slippage_fill_risk [EXTRACTED 1.00]
- **As-Of Empirical Monte Carlo Design** — page_9_asof_walk_forward_empirical_monte_carlo, page_9_cached_live_probability_grids, page_9_historical_replay_live_shadow_timestamp, page_9_empirical_sampling_historical_btc_paths, page_9_no_future_information_guardrail [EXTRACTED 1.00]

## Communities (90 total, 19 thin omitted)

### Community 0 - "Replay Storage Core"
Cohesion: 0.05
Nodes (77): NormalizedContractRule, ContractSpec, DecisionState, _json_ready(), OrderBookObservation, PriceObservation, _require_optional_utc(), _require_utc() (+69 more)

### Community 1 - "Probability Methodology"
Cohesion: 0.05
Nodes (66): Deterministic Decision Tree, BTC ETF Options Context, Research Falsification Criteria, BTC Remaining-Path Probability Framework Draft, GEX Infrastructure Reuse Plan, GEX Reuse Is Infrastructure Not Strategy Rationale, Binance Coinbase Kraken Spot WebSocket Feeds, Black Scholes Merton Digital Option Theory (+58 more)

### Community 2 - "Raw Event Writer"
Cohesion: 0.05
Nodes (36): DataSource, part_one_sources(), SourceRole, SourceStatus, test_part_one_sources_are_locked(), Enum, CollectorEvent, SourceHealth (+28 more)

### Community 3 - "Live Collector Runtime"
Cohesion: 0.06
Nodes (45): collection_deadline(), _freshness_row(), _is_rtds_socket_idle(), LiveCollectorResult, _merge_status_from_markets(), _optional_float(), _optional_sequence(), _orderbook_freshness_rows() (+37 more)

### Community 4 - "Collector CLI"
Cohesion: 0.07
Nodes (32): LiveCollectorConfig, test_live_collector_allows_forever_duration(), test_live_collector_defaults_to_current_and_next_windows(), test_live_collector_rejects_invalid_loop_intervals(), test_live_collector_rejects_invalid_rest_backup_and_timezone(), test_live_collector_rejects_unsupported_contract_interval(), _await_runner(), _CancellableTask (+24 more)

### Community 5 - "Polymarket Adapters"
Cohesion: 0.08
Nodes (42): MarketToken, _fetch_clob_book_event(), _best_ask(), _best_bid(), BookTop, build_market_ws_subscription(), clob_book_event(), clob_book_top() (+34 more)

### Community 6 - "Decision Gates"
Cohesion: 0.05
Nodes (47): As-Of Volatility Skew Risk Appetite Layer, Block Decision, Blue Numbered Section Headings, Bounded Contract Selection Logic, BTC Binary Path Probability Engine, Contract State Document Page, Core BTC Versus ETF Options Context Ablation, Decision Gates and Execution Logic (+39 more)

### Community 7 - "Live BTC Inputs"
Cohesion: 0.06
Nodes (45): available_depth, best_bid and best_ask, BTC Log Returns Before Decision Time, BTC Spot Feeds, Contract Edge Disappears After Crossing Spread, Contract Near Heavily Traded Level, Danger Line Crossing Before Expiry, data_granularity (+37 more)

### Community 8 - "Volatility Snapshot"
Cohesion: 0.12
Nodes (35): _price(), test_build_volatility_snapshot_does_not_duplicate_exact_proxy_matches(), test_build_volatility_snapshot_filters_reference_prices_by_symbol_when_requested(), test_build_volatility_snapshot_ignores_rtds_binance_proxy_mismatch(), test_build_volatility_snapshot_is_asof_safe_and_labels_regime(), test_build_volatility_snapshot_observed_ts_is_latest_observed_allowed_price(), test_build_volatility_snapshot_rejects_non_utc_asof_ts(), test_build_volatility_snapshot_resets_return_history_after_stale_chainlink_gap() (+27 more)

### Community 9 - "Contract Discovery"
Cohesion: 0.07
Nodes (35): crypto_5m_slugs(), crypto_updown_slugs(), _decode_json_list(), extract_market_tokens(), fetch_crypto_5m_markets(), fetch_crypto_updown_markets(), _fetch_market_slug(), floor_to_5m_epoch() (+27 more)

### Community 10 - "XGBoost Ablations"
Cohesion: 0.07
Nodes (38): Ablation Matrix, Backtest and Ablation Design, Backtest Rules Table, Block or Demand More Edge Before Trading, BTC History Reconstructs Market State, Calibrated Probabilities Requirement, calibration_adjustment Target, Candidate Rule Requires Monte Carlo Edge First (+30 more)

### Community 11 - "Walk Forward Monte Carlo"
Cohesion: 0.08
Nodes (37): As-Of Walk-Forward Empirical Monte Carlo, Short-Dated Crypto Binary Payoff, Cached Monte Carlo Probability Grids, Closed-Form Probability Baselines, Decision Gates and Execution Logic, ETF Options and GEX Context, Executable Edge After Costs, GEX Infrastructure Reuse Plan (+29 more)

### Community 12 - "Pricing Formulas"
Cohesion: 0.08
Nodes (36): Analytical Probability Baselines, One Dollar Binary Payoff Expected Value, Blue Bold Section Heading Style, BTC Binary Path Probability Engine, BTC Price Data, Centered Equation Layout, Closed Form Baselines and Outputs, Closed Form Sanity Check for Monte Carlo (+28 more)

### Community 13 - "Monte Carlo Outputs"
Cohesion: 0.07
Nodes (35): Anti-Overfit Principle, As-Of Historical and Live Shocks, As-Of Monte Carlo Research Question, As-Of Replay Data Rule, Block or Widen Uncertainty Buffer, Closed-Form Formulas, Core Model Outputs and Monte Carlo Calculations, Current State Update (+27 more)

### Community 14 - "Cached Probability Grid"
Cohesion: 0.06
Nodes (35): Cache Lookup, Cached Monte Carlo and Conditional Refresh, Cached Monte Carlo Grid, Closed-Form Baselines for Sanity Checks, Closed-Form Formulas as Comparison Tools Only, Conditional Full Refresh, d_DOWN = ln(K / S_t), d_side Favorable Log Distance (+27 more)

### Community 15 - "Live Shadow Backtest"
Cohesion: 0.09
Nodes (34): Asynchronous Decision Logging, BTC Binary Path Probability Engine, BTC Market Data At Or Before t, Cached Grid Lookup Path, Cheap Live Decision Loop, Decision Score Outputs, Executable Venue Price Comparison, Binance Coinbase Kraken WebSockets (+26 more)

### Community 16 - "Validation Metrics"
Cohesion: 0.1
Nodes (34): Ablation Results, Brier Score and Log Loss, BTC Binary Path Probability Engine, BTC Tick or 1-Second History Availability, Calibration Curve, Closed-Form Baseline, Decision Gates Redundancy Question, Drawdown and Daily Loss (+26 more)

### Community 17 - "Noise Controls"
Cohesion: 0.06
Nodes (34): As-Of Walk-Forward Empirical Monte Carlo, Later Backtest Scoring, Cached Live Probability Grids, Core BTC Engine Versus ETF Context Ablation, Data-Quality and Noise Controls, Downside or Upside Stress Affecting Path Survival, Empirical Sampling from Historical BTC Paths, ETF Options Context Inputs (+26 more)

### Community 18 - "Contract Rule Parser"
Cohesion: 0.12
Nodes (26): _asset_from_description(), ContractRuleRejected, _decode_json_list(), _parse_datetime(), parse_polymarket_crypto_updown_rule(), rule_text_hash(), _asset(), _comparison() (+18 more)

### Community 19 - "Settlement Source Contract"
Cohesion: 0.1
Nodes (30): Backtest Against Executable Market Prices, Binary Payoff Table, BTC DOWN Payoff Formula 1 if S_T Less Than K, BTC UP Payoff Formula 1 if S_T Greater Than K, Contract Rule Matching Guardrail, Stored Field current_settlement_price, Data Required Before Modeling Section, Data Source Fields Purpose Table (+22 more)

### Community 20 - "Replay Labels"
Cohesion: 0.1
Nodes (28): BTC Binary Path Probability Engine Page, BTC State, Chronological Walk-Forward Splits, Contract State, Danger Line Touch Label, Data Quality Features Partially Cut Off, Decision State, Distance-Time State Features (+20 more)

### Community 21 - "Reference Bibliography"
Cohesion: 0.12
Nodes (27): Ait-Sahalia, Mykland, and Zhang 2005 Sampling Noise Paper, Barndorff-Nielsen et al. 2008 Realized Kernels Paper, Binance Spot WebSocket Streams, Bollerslev 1986 GARCH Paper, BTC Binary Path Probability Engine, Bulleted Reference Layout, Chen and Guestrin 2016 XGBoost Paper, Coinbase Advanced Trade WebSocket Feeds (+19 more)

### Community 22 - "Monitoring Plan"
Cohesion: 0.1
Nodes (24): Backtest and Ablation Design, Core Model Outputs and Monitoring Components, Data Quality and Noise Controls, Data Required Before Modeling, Decision Gates and Execution Logic, ETF Options and VIX Implementation Plan, ETH 45-Minute Binary Contract Example, Exchange and Market Data Sources (+16 more)

### Community 23 - "Terminal Monitor"
Cohesion: 0.17
Nodes (22): _block_stale_disagreements(), _connect_read_only_with_retry(), _dict_rows(), fetch_monitor_snapshot(), _fmt_float(), _fmt_int(), MonitorSnapshot, _optional_int() (+14 more)

### Community 24 - "Monte Carlo Variables"
Cohesion: 0.13
Nodes (23): Noise layer: bad tick or feed jump with source agreement and stale-feed checks, Noise layer: bid/ask bounce with robust mid or median state construction, Incomplete Research Draft: BTC Binary Path Probability Engine, Contract state variables: side, seconds_left, horizon, threshold K, Core Monte Carlo start set for first implementation, Data-quality state variables: source_quality_flag, data_granularity, feed_disagreement, stale_price_flag, Distance state variables: S_t, d_side, z_path, ETF options variables should remain optional until passing ablation testing (+15 more)

### Community 25 - "Project Thesis"
Cohesion: 0.1
Nodes (23): Abstract Section, As-Of Methodology, Blue Bordered Status Callout, BTC 5-Minute and 15-Minute Binaries, BTC UP/DOWN Contract, Contract-Specific Pricing Variables, Core Probability Outputs, Decision Gate for Tradeability (+15 more)

### Community 26 - "Settlement Hierarchy"
Cohesion: 0.11
Nodes (21): Venue-Defined Settlement Source, As-Of Contract-State Builder, Chainlink Data Streams Settlement Source, BTC ETH SOL Short-Dated Up/Down Market Family, Normalized Rule Object, Parser Rejection Rules, Start-Reference Threshold K, Strict Polymarket Crypto Up/Down Rule Parser (+13 more)

### Community 27 - "Decision Layer Outputs"
Cohesion: 0.11
Nodes (21): Core Outputs Passed To The Decision Layer, Fees Slippage Latency And Model Uncertainty, Decision Layer, Large Disagreement Logging, edge_before_costs, Low p_no_touch_MC And Weak z_path, 2 * Phi(z_path) - 1 Main Estimator, Thin Order Books And Nearby Support Resistance (+13 more)

### Community 28 - "TypeScript Config"
Cohesion: 0.11
Nodes (18): compilerOptions, allowJs, allowSyntheticDefaultImports, esModuleInterop, forceConsistentCasingInFileNames, isolatedModules, jsx, lib (+10 more)

### Community 29 - "Baseline Probabilities"
Cohesion: 0.14
Nodes (18): As-Of Walk-Forward Empirical Estimation, Black and Scholes 1973 Digital Payoff Probability Reference, Bollerslev 1986 Volatility Clustering Reference, BTC Binary Path Probability Engine, Corsi 2009 Realized Volatility Scaling Reference, Digital Option Pricing Logic, Page 6 Document Page, Driftless Brownian Motion (+10 more)

### Community 30 - "Spoon Deployment"
Cohesion: 0.15
Nodes (17): CLOB WebSocket Primary Order Book Feed, CLOB WebSocket And Spoon Deployment Plan, CLOB WebSocket Runtime Environment, Docker Compose Collector Service, Collector Persistent Data Mounts, GEX-Style GitHub CI Plan, GitHub Tests CI Pipeline, Docker VPS Migration Requirements (+9 more)

### Community 31 - "RTDS Price Feed"
Cohesion: 0.18
Nodes (14): _send_rtds_heartbeats(), build_rtds_subscriptions(), rtds_heartbeat_message(), rtds_price_events(), _source_key(), _symbol(), _symbol_asset(), test_build_rtds_subscriptions_uses_chainlink_and_binance_proxy_topics() (+6 more)

### Community 32 - "Path Survival Outputs"
Cohesion: 0.15
Nodes (17): Contract Winning Side Of K, Danger Line, Decision Layer Wait Block Or Demand More Edge, DOWN Finish Event S_T < K, Remaining Path Stable Enough To Justify Entry, Full Simulated BTC Path S_u^(i) From t To T, High p_finish_MC But Low p_no_touch_MC State, p_finish (+9 more)

### Community 33 - "As Of State"
Cohesion: 0.12
Nodes (17): Clean As-Of State, S_t Current Settlement-Source BTC Price, Decision Time T, Optional ETF Options Context, Future Candles Settlement Prices And Summaries Are Evaluation Labels Not Inputs, Market-State Inputs And State Construction, Probability Engine, Raw BTC Data (+9 more)

### Community 34 - "Scope Diagram"
Cohesion: 0.19
Nodes (16): BTC 5-Minute Binary Contract Model Schematic, BTC UP/DOWN Binary Contracts, Contract State Not General BTC Forecast Thesis, Execution Gates Panel, Model Overlay Panel, Page 2 Image, Read-Only Logging and Paper Trading Mode, Path Risk and Execution Cost (+8 more)

### Community 35 - "External Review Outputs"
Cohesion: 0.16
Nodes (15): Core Probability Outputs, Executable Edge After Costs, Monte Carlo Primary Estimator, Order Book Execution Model, p_finish Terminal Win Probability, p_no_touch Path Survival Probability, Multiple Path Generator Ensemble, sigma_tau Remaining Movement Scale (+7 more)

### Community 36 - "Risk Gate Layout"
Cohesion: 0.17
Nodes (15): As-of Monte Carlo Snapshot, As-of State Variables, BTC Binary Path Probability Engine, Cached Probability Grid, Conditioning Variables, Conservative Required-edge Threshold, Decision Boundary Chart, Decision Layer and Risk Gates (+7 more)

### Community 37 - "Coinbase Feed"
Cohesion: 0.2
Nodes (10): build_coinbase_ticker_subscription(), coinbase_ticker_events(), test_build_coinbase_ticker_subscription(), test_coinbase_ticker_events_parse_real_channel_shape(), CoinbaseTick, _optional_float(), parse_coinbase_ticker(), parse_coinbase_ticker_message() (+2 more)

### Community 38 - "Data Contract"
Cohesion: 0.23
Nodes (12): DuckDB Normalized Store, Historical Data And Overfit Rules, Part One Data Contract, DuckDB Plus Parquet Storage Contract, Part One Excluded Data Sources, Part One Included Data Sources, As-Of Replay Leakage Rule, Part One Data Sources And Databases Plan (+4 more)

### Community 39 - "Collector Status JSON"
Cohesion: 0.18
Nodes (10): contracts, generated_at, ingest_counts, orderbooks, prices, source_errors, coinbase_advanced_ws, normalized:polymarket_clob:orderbook_snapshot (+2 more)

### Community 40 - "Health Status JSON"
Cohesion: 0.2
Nodes (9): contracts, generated_at, ingest_counts, normalized_health, orderbook_freshness, orderbooks, prices, source_errors (+1 more)

### Community 41 - "Runtime Status JSON"
Cohesion: 0.2
Nodes (9): contracts, generated_at, ingest_counts, normalized_health, orderbook_freshness, orderbooks, prices, source_errors (+1 more)

### Community 42 - "System Map"
Cohesion: 0.2
Nodes (10): Failure Modes And Operational Safety Controls, Chainlink Settlement Reference Stream, Chainlink-Only Volatility Rule, Execution Policy Router, Keep Venue Differences Out Of The Math, Compiled Probability Core Decision Plane, Plan Risk Register, Settlement Price Layer (+2 more)

### Community 43 - "Execution Risk"
Cohesion: 0.27
Nodes (10): Conservative Required Edge Rule, High-Volatility Regime Shift, Latency Slippage and Fill Uncertainty, Low-Volatility False Calm, Required Edge Formula with Spread Latency Noise and Monte Carlo Uncertainty Buffers, Volatility and Execution Risk Mitigation Table, Thin Comparable-Path Bucket, Threshold Proximity Risk from Truncated Table Row (+2 more)

### Community 44 - "Fast Path Architecture"
Cohesion: 0.27
Nodes (10): Cached Grids and Refresh Rules, Cached Probability Grids Instead of Monte Carlo on Every Tick, C++ Hot Loop for State to Z-Path to Cached Probability Lookup to Decision, Fast Live Path, Fast Live Path and Speed Architecture, Grid Refresh Trigger and Action Table, Monte Carlo as Primary Estimator but Not Every-Tick Decision Path, Refresh Triggers for New Contracts Buckets Volatility Near Entry and Stale Cache (+2 more)

### Community 45 - "Build Plan"
Cohesion: 0.29
Nodes (8): As-Of State Construction, Strict Contract Rule Parser, BTC/ETH Binary Contract Engine Build Plan, Source Preservation Rule, Contract Rule Parser Implementation Plan, Decision State And Normalized Storage Plan, Candidate Market Discovery, Read First Documentation Index

### Community 46 - "External Data Sources"
Cohesion: 0.29
Nodes (8): Binance Spot WebSocket Source, Coinbase Advanced Trade WebSocket Source, External Review Data Sources, Part Two Source Rules, Executable Order Book Pricing, Polymarket Order Book Documentation, Polymarket RTDS Documentation, Executable Price Not Midpoint Rationale

### Community 47 - "Literature Thesis"
Cohesion: 0.29
Nodes (7): Andersen Bollerslev Diebold Labys Realized Volatility, Avellaneda Stoikov Market Making, Complete Project Thesis, Price The Contract Not The Coin, Polymarket Crypto Binary Strategy Overview, Reiner And Rubinstein Barrier Options, Wolfers And Zitzewitz Prediction Markets

### Community 48 - "C++ Probability Core"
Cohesion: 0.29
Nodes (7): Python Orchestration And C++ Hot Loop Split, C++20 Standard Requirement, Probability Core CMake Target, Probability Core Static Archive Link Step, Fast Live Path And Slow Research Path Split, Barebones Local Setup Commands, Probability Core Target Directory

### Community 49 - "Read Only Operations"
Cohesion: 0.29
Nodes (7): Always-On Collector And Monitor Plan, Always-On Collector Command, Part Two Live Collectors, Local Operator Cockpit UI, Read-Only First Rationale, Live Read-Only Collection Command, Atomic Status File Terminal Monitor

### Community 50 - "Reconnect Backoff"
Cohesion: 0.43
Nodes (5): compute_reconnect_delay(), test_first_reconnect_delay_starts_at_base_without_jitter(), test_reconnect_delay_applies_symmetric_jitter(), test_reconnect_delay_grows_exponentially_without_jitter(), test_reconnect_delay_is_capped_before_jitter()

### Community 51 - "Atomic Writes"
Cohesion: 0.48
Nodes (5): durable_link(), durable_replace(), _fsync_dir(), _fsync_file(), test_durable_replace_consumes_tmp_and_publishes_final()

### Community 52 - "Binance Feed"
Cohesion: 0.43
Nodes (5): NormalizedPriceTick, parse_binance_book_ticker(), parse_binance_trade(), test_parse_binance_book_ticker(), test_parse_binance_trade()

### Community 53 - "Calibration Protocol"
Cohesion: 0.4
Nodes (5): Post-Expiry As-Of Replay Labels, Validation And Ablation Protocol, XGBoost Challenger And Calibration Protocol, Monte Carlo Primary XGBoost Challenger Contract, XGBoost Shadow-First Rationale

### Community 54 - "Robust Volatility Literature"
Cohesion: 0.4
Nodes (5): BTC Binary Path Probability Engine, Noise-Robust Volatility Estimators Including Kernels Pre-Averaging and Two-Scale Realized Volatility, Practical Estimator Validation Against Heavier Noise-Robust Alternatives, Technical Report Page Layout with Dense Text Tables and Blue Numbered Sections, Visual QA Contract State Page 11

### Community 55 - "Monte Carlo Diagnostics"
Cohesion: 0.4
Nodes (5): Final-Window Wick Risk, Max Adverse Excursion, Monte Carlo, Monte Carlo Diagnostics, Uncertainty Bands

### Community 56 - "Status Checker"
Cohesion: 0.7
Nodes (4): main(), _parse_timestamp(), _reject_bad_freshness_rows(), _reject_required_source_errors()

### Community 57 - "Migration Script Tests"
Cohesion: 0.8
Nodes (4): _local_repo(), test_migration_script_does_not_use_gnu_only_rsync_info_flag(), test_migration_script_fails_when_rsync_fails(), _write_executable()

### Community 59 - "Path Generation Defaults"
Cohesion: 0.5
Nodes (4): Historical BTC Path Fragments, Later Stress Overlays If Needed, Path Generation Defaults, Path Model Decision

### Community 61 - "Apple Toolchain"
Cohesion: 0.67
Nodes (3): Apple Make Toolchain, CMake Debug Build Cache, Darwin ARM64 Configure Environment

### Community 62 - "Time Step Decision"
Cohesion: 0.67
Nodes (3): Incomplete 5- Time Step Fallback, 1-Second Steps When Reliable Data Exists, Time Step Decision

## Ambiguous Edges - Review These
- `DecisionState` → `Cached Monte Carlo Probability Grids`  [AMBIGUOUS]
  graphify-out/converted/BTC_Binary_Path_Probability_Incomplete_Research_Paper.before_mc_vars_etf_plan_20260530_5d7d3414.md · relation: semantically_similar_to
- `BTC ETF Options Context` → `Local Secrets Policy`  [AMBIGUOUS]
  secrets/README.md · relation: conceptually_related_to
- `Review Brief Data Lineage` → `Local Secrets Policy`  [AMBIGUOUS]
  secrets/README.md · relation: conceptually_related_to
- `Decision Boundary Chart` → `Cached Probability Grid`  [AMBIGUOUS]
  reports/generated/qa_4_5_4_6_rewrite/page-7.png · relation: conceptually_related_to
- `Short-Dated BTC Binary Markets` → `Possible ETH/BTC Example Mismatch`  [AMBIGUOUS]
  reports/generated/visual_qa_contract_state/contact-sheet.png · relation: conceptually_related_to
- `ETH 45-Minute Binary Contract Example` → `Possible ETH/BTC Example Mismatch`  [AMBIGUOUS]
  reports/generated/visual_qa_contract_state/contact-sheet.png · relation: references
- `Polymarket Idea Report Page 1` → `Polymarket Profit Concentration Claim`  [AMBIGUOUS]
  reports/generated/visual_qa_contract_state/page-1.png · relation: rationale_for
- `Noise layer: bid/ask bounce with robust mid or median state construction` → `Noise handling table continues or is cropped at the bottom after the bid/ask bounce row`  [AMBIGUOUS]
  reports/generated/visual_qa_contract_state/page-10.png · relation: references
- `Threshold Proximity Risk from Truncated Table Row` → `Volatility and Execution Risk Mitigation Table`  [AMBIGUOUS]
  reports/generated/visual_qa_contract_state/page-11.png · relation: references
- `Python Research Layer` → `Research Layer Capabilities`  [AMBIGUOUS]
  reports/generated/visual_qa_contract_state/page-12.png · relation: references
- `Live Shadow Row Schema` → `Field Group And Fields Header`  [AMBIGUOUS]
  reports/generated/visual_qa_contract_state/page-12.png · relation: references
- `XGBoost Calibration Feature Set` → `Data Quality Features Partially Cut Off`  [AMBIGUOUS]
  reports/generated/visual_qa_contract_state/page-13.png · relation: references
- `ETF Context Missing-Depth Flag` → `ETF IV Stress, Skew Stress, and Flow Flag Variables`  [AMBIGUOUS]
  reports/generated/visual_qa_contract_state/page-14.png · relation: references
- `Historical Polymarket BTC Data Reconstruction` → `Polymarket Market WebSocket Market Channel Documentation`  [AMBIGUOUS]
  reports/generated/visual_qa_contract_state/page-16.png · relation: conceptually_related_to
- `BTC Binary Path Probability Engine` → `Descriptive GEX Assumptions Rather Than Dealer-Position Truth`  [AMBIGUOUS]
  reports/generated/visual_qa_contract_state/page-17.png · relation: conceptually_related_to
- `Variable Key K S_t S_T P_exec` → `AMBIGUOUS Subscript Notation Reading For S_t And S_T`  [AMBIGUOUS]
  reports/generated/visual_qa_contract_state/page-3.png · relation: conceptually_related_to
- `Time Step Decision` → `Incomplete 5- Time Step Fallback`  [AMBIGUOUS]
  reports/generated/visual_qa_contract_state/page-9.png · relation: references

## Knowledge Gaps
- **370 isolated node(s):** `name`, `version`, `private`, `dev`, `build` (+365 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **19 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `DecisionState` and `Cached Monte Carlo Probability Grids`?**
  _Edge tagged AMBIGUOUS (relation: semantically_similar_to) - confidence is low._
- **What is the exact relationship between `BTC ETF Options Context` and `Local Secrets Policy`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Review Brief Data Lineage` and `Local Secrets Policy`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Decision Boundary Chart` and `Cached Probability Grid`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Short-Dated BTC Binary Markets` and `Possible ETH/BTC Example Mismatch`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `ETH 45-Minute Binary Contract Example` and `Possible ETH/BTC Example Mismatch`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **What is the exact relationship between `Polymarket Idea Report Page 1` and `Polymarket Profit Concentration Claim`?**
  _Edge tagged AMBIGUOUS (relation: rationale_for) - confidence is low._