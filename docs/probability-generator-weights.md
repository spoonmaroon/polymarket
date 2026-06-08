
## Terminal probability versus risk-adjusted score

`p_finish` is the terminal fair-value probability for the binary payoff. For a matched UP/DOWN market window, UP and DOWN terminal probabilities should sum to approximately 1.0 after pair normalization. Small gaps can come from rounding or missing counterpart rows; a large gap is a runtime diagnostic, not a third outcome.

`risk_adjusted_p_finish` is a stress-haircuted score. It can be below `p_finish` because stress overlays are adversarial path-risk diagnostics. It is not allowed to remove probability mass from both UP and DOWN while still being labeled as the primary probability.

`p_no_touch` remains a path-survival metric for risk gates. It is not the payout probability and is not expected to complement across UP and DOWN.
