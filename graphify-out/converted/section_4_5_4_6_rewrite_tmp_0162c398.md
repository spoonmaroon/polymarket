<!-- converted from section_4_5_4_6_rewrite_tmp.docx -->

## 4.5 Movement Scale for Monte Carlo
This section is not trying to predict whether BTC goes up or down. It only estimates how much BTC can still move before expiry. The Monte Carlo engine needs this estimate before it can simulate future paths.
The key variable is .
Plain meaning:
= expected remaining BTC movement before expiry.
A larger  means the simulated paths should be wider and more dangerous.
A smaller  means the simulated paths should be tighter, but never risk-free.
The model first blends recent realized-volatility windows:

Plain meaning:
= short-window realized volatility.
= medium-window realized volatility.
= longer-window realized volatility.
= weights assigned to those windows.
The weights are chosen before testing. They are model settings, not magic constants.
The first version should use simple preset weights, such as , , and . These weights should later be tested with walk-forward validation, not optimized until they only fit old Polymarket contracts.
Then the model scales volatility to the time left:

Plain meaning:
= time left until expiry.
= square-root time scaling. Volatility usually grows with the square root of time, not straight time.
= minimum movement assumption. This prevents the model from becoming too confident during quiet periods.
= volatility-regime multiplier. It increases the movement estimate when volatility is expanding and can reduce it when volatility is calming.
In plain English, 4.5 says:
Estimate recent BTC volatility, scale it to the time left, apply a minimum safety floor, adjust for the current volatility regime, and use that as the path width for Monte Carlo.
This is a statistical volatility estimate, not machine learning. XGBoost can later be added as a challenger or calibration layer, but it is not the first source of .
## 4.6 Executable Edge After Costs
A probability estimate is not automatically a trade. The model must compare its probability against the actual executable market price.
For a one-dollar binary payoff, the expected payoff before costs is:

Plain meaning:
If the model believes the contract wins 74 percent of the time, then the raw fair value is about 0.74 dollars.
This is before spread, fees, slippage, latency, and uncertainty buffers.
The usable edge after costs is:

Plain meaning:
= final model probability after Monte Carlo, calibration, and risk adjustments.
= actual executable contract price, not the midpoint.
= spread crossing, fees, slippage, latency, and uncertainty buffer.
= edge left after paying the market and costs.
Example:

The model estimates a 74 percent fair probability, the contract can be bought at 68 cents, and costs are estimated at 3 cents. The remaining edge is 3 cents per contract. If  is too small or negative, the system should not enter even if the direction looks correct.
## 4.7 Optional Closed-Form Baselines
The formulas in this section are not the main engine. They are simple analytical baselines used to check whether the Monte Carlo output is reasonable.
The main model is still:
Simulate many possible BTC paths.
Count how many finish on the correct side.
Count how many avoid crossing the danger line.
Compare the resulting probability against the executable market price.
The closed-form formulas ask a simpler question: if BTC behaved like a clean normal or lognormal process, what probability would we get?
First define the logged, side-adjusted distance from the threshold:

Plain meaning:
= current settlement-source BTC price.
= contract threshold.
is positive when BTC is on the favorable side.
Log distance is used because BTC movement is proportional. A 100 dollar move does not mean the same thing at every BTC price level.
The terminal baseline assumes the final side-adjusted distance is:

Plain meaning:
= final side-adjusted distance at expiry.
= current cushion from the threshold.
= expected drift over the remaining window. The first version should usually set this near zero.
= random BTC movement between now and expiry.
means the shortcut formula pretends the random movement is normally distributed with movement scale .
The contract finishes in the money when . The shortcut terminal probability is:

Plain meaning:
= standard normal probability converter.
The fraction inside  is the current cushion, adjusted for drift, divided by expected remaining movement.
This gives a clean first-pass probability, but it ignores jumps, wicks, feed disagreement, and volatility clustering.
The no-touch baseline uses:

Plain meaning:
= current cushion divided by expected remaining movement.
If  is near 0, BTC is close to the danger line.
If  is near 1, the cushion is about one expected remaining move.
If  is near 2, the cushion is about two expected remaining moves.
The closed-form no-touch shortcut is:

Plain meaning:
asks whether BTC ends on the correct side.
asks whether BTC avoids crossing back through the threshold before expiry.
Therefore  is stricter than .
These formulas are useful only as sanity checks. The empirical Monte Carlo engine remains the primary estimator because BTC paths are not clean normal curves. BTC can jump, wick, cluster volatility, and disagree across feeds. If the formulas and Monte Carlo disagree sharply, the system should log the disagreement and inspect the regime, path shape, or data quality.