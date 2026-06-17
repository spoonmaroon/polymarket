# Hybrid Monte Carlo, ML Calibration, and BART Roadmap

## Purpose

This document captures the repository-ready roadmap for the offline calibration
lane. Monte Carlo remains the base probability engine. ML improves calibration
and decision discipline. BART stays a research-only uncertainty benchmark until
the simpler models show walk-forward value.

## Core Recommendation

Do not replace Monte Carlo with a black-box model. The target stack is:

1. Monte Carlo base probabilities and path-risk signals.
2. Offline calibration models that learn when Monte Carlo is biased.
3. A decision layer that applies executable-price, cost, and uncertainty
   buffers before any paper-trade evaluation.

The live runtime still depends on the existing Monte Carlo outputs:

- `p_finish_mc`
- `p_no_touch_mc`
- `z_path`
- `sigma_tau`
- generator dispersion and related path diagnostics

## What Needs Calibration

The main problem is probability calibration, not simply raw classification
accuracy. A model can look good overall and still fail in dangerous slices, so
reporting needs bucketed evaluation.

Required slices:

- time to expiry
- distance from threshold
- `z_path`
- volatility regime
- asset and side
- spread, depth, and order-book imbalance
- threshold congestion and final-window states
- source-quality and stale-data states

Required metrics:

- Brier score
- log loss
- reliability / calibration curve
- expected calibration error
- bucket-level realized win rate and sample counts

## Monte Carlo Retention Criteria

Monte Carlo should stay in place because these contracts are path-dependent in
practice. When calibration is poor, the first question is whether the path
generator is too narrow, too calm, or missing tail risk rather than whether the
system should abandon simulation entirely.

Likely failure modes:

- volatility floor too low
- final-window chaos under-modeled
- missing fat tails or wick stress
- sparse high-congestion buckets treated as overconfident
- missing order-book or source-quality context

## Target Architecture

The intended offline architecture is:

1. Build replay-safe as-of decision states.
2. Run Monte Carlo to produce base probabilities and path-risk features.
3. Train a calibration or meta-model to estimate `p_finish_final`.
4. Feed calibrated probabilities into executable-edge analysis.
5. Keep uncertainty-aware models offline until they prove out-of-sample value.

The ML layer should estimate calibrated win probability. It should not emit
direct buy/sell decisions.

## Replay-Safe Dataset Requirements

Every training row must reflect only information available at or before the
decision timestamp. Future settlement data and later market movement are labels,
not features.

Important fields include:

- identity and timing: `state_id`, contract metadata, `asof_ts`, `expiry_ts`
- Monte Carlo outputs: `p_finish_mc`, `p_no_touch_mc`, `z_path`, `sigma_tau`
- market structure: spread, bid/ask, midpoint, depth, imbalance, quote age
- volatility and congestion context
- replay / runtime quality markers and skip-block reasons
- final label and resolved outcome

## Model Roadmap

### Phase 0: Runtime Stability and Clean Logging

Do not trust ML until replay correctness, threshold stability, sigma validity,
freshness checks, and label integrity are solid. Corrupt states will poison the
training set.

### Phase 1: Raw Monte Carlo Calibration Reports

Generate slice-based reports for the base Monte Carlo probabilities to locate
overconfidence and underconfidence before introducing more complex models.

### Phase 2: Logistic Regression Baseline

Start with a simple, interpretable calibrator:

- fast to train
- hard to overfit relative to richer models
- good first check that the Monte Carlo outputs contain useful signal

### Phase 3: XGBoost / LightGBM Benchmark

Move next to a stronger tabular model that can learn nonlinear interactions
across Monte Carlo outputs, volatility context, and order-book structure.

This remains an offline calibration workflow first, not a live trading change.

### Phase 4: BART Offline Uncertainty Benchmark

BART is for uncertainty-aware research, not for immediate runtime adoption. Its
job is to answer whether posterior uncertainty helps detect false confidence,
sparse dangerous buckets, and unstable threshold states better than the simpler
models.

Useful BART outputs:

- posterior mean probability
- lower confidence bound
- posterior width
- derived uncertainty score

If BART does not improve calibration or uncertainty usefulness out-of-sample,
the main stack should remain Monte Carlo plus the simpler calibrator.

## Validation Rules

Use walk-forward evaluation only. No random shuffle.

Required rules:

- features must be as-of the decision timestamp
- no future labels in features
- no later Polymarket prices as decision-time inputs
- no leakage across overlapping nearby windows
- compare raw Monte Carlo, logistic, XGBoost / LightGBM, and BART on the same
  walk-forward splits

## Practical Implementation Order

Implementation status: the active build plan is `docs/superpowers/plans/2026-06-15-backtest-replay-xgboost-calibration.md`. The first shipped scope is replay-safe dataset export, offline backtest, logistic calibration, and XGBoost calibration. BART remains an offline benchmark after the simpler models have walk-forward evidence.

1. Keep the existing Monte Carlo outputs and path-risk signals.
2. Log every replay-safe as-of decision state with final labels.
3. Build slice-based calibration reports.
4. Fix obvious Monte Carlo calibration failures and stale-input issues.
5. Train the logistic regression baseline.
6. Train the XGBoost / LightGBM benchmark.
7. Apply walk-forward probability calibration where needed.
8. Explore `MC_Calibrator_BART_v1` offline.
9. Feed conservative probabilities or uncertainty buffers into edge analysis.
10. Keep this lane read-only and offline until the research evidence justifies
    anything further.

## Decision Rule Guardrail

The decision layer should stay explicit:

`edge = calibrated_probability - executable_price - costs - uncertainty_buffer - path_risk_buffer`

Uncertainty-aware models only help if they improve that guarded edge estimate.
They do not replace the guardrail.
