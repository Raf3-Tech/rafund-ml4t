# RAFund Strategy Diagnosis: Why You're Losing Money

## Executive Summary

Your strategy is **architecturally broken**, not just underperforming.

**The real problem:**
- You're not trading mean reversion
- You're trading the **rolling window**
- The z-score decrease you interpret as "reversion" is actually the window shifting

---

## The Single Trade Lifecycle (What Actually Happens)

### Entry
```
Date:    2024-03-26
Z-score: 2.02 ← Signal fires
Action:  BUY $10k of BTC, SHORT $10k of ETH
Thesis:  "Spread is 2 std devs above mean, will revert"
```

### Reality Check (Entry Moment)
```
Actual spread:     -79.20
Rolling mean:      -79.71
Rolling std:       0.255
Gap from mean:     +0.51 (2 sigma extended, looks bad)
```

**What you expect:**
```
"BTC/ETH spread will revert to -79.71"
→ BTC price drops or ETH price rises
→ You profit as spread tightens back
```

**What actually happens over 31 days:**

```
Day 0  │ Spread: -79.20 │ Mean: -79.71 │ Z: +2.02 │ [ENTRY]
       │                │              │         │
Day 5  │ Spread: -79.44 │ Mean: -79.69 │ Z: +0.91 │
       │                │              │         │ <- Window rolled forward
Day 10 │ Spread: -79.20 │ Mean: -79.62 │ Z: +1.75 │    Old data removed,
       │                │              │         │    New data added
Day 15 │ Spread: -79.04 │ Mean: -79.55 │ Z: +2.09 │
Day 20 │ Spread: -79.03 │ Mean: -79.49 │ Z: +1.75 │
       │                │              │         │
Day 31 │ Spread: ?      │ Mean: ?      │ Z: ~0.4 │ [EXIT]
```

### Exit
```
Date:    2024-04-26
Z-score: 0.40 ← Signal fires (threshold is 0.5)
Action:  CLOSE position
Result:  -$69 loss (-0.34%)
```

---

## The Core Issue (Mathematically)

### What You Think Is Happening

Z-score formula: $Z_t = \frac{\text{Spread}_t - \text{Mean}_{t-60:t}}{\text{Std}_{t-60:t}}$

You interpret: *"Z decreased from 2.02 to 0.40 → spread reverted!"*

### What's Actually Happening

Three things can make Z decrease:

1. **Spread reverts** (desired)
   ```
   Spread: -79.20 → -79.70
   Mean stays -79.71
   Z: 2.02 → 0.02 ✓ good signal
   ```

2. **Mean shifts up** (rolling window chasing)
   ```
   Spread: -79.20 → -79.00 (gets worse for you!)
   Mean:  -79.71 → -79.30 (window rolled forward, old data removed)
   Z: 2.02 → 0.76 ✗ false signal
   ```

3. **Volatility changes** (std shifts)
   ```
   Spread barely moves
   Std shrinks due to market calming
   Z automatically decreases "miraculously"
   ```

---

## Your Diagnostic Output: The Smoking Gun

From the analysis:

### Window Size 60 Days (Your current setting)
```
Entry Z-score:  2.02
Spread actual:  -79.20
Mean:           -79.71
```

**20 days later:**
```
Spread changed: +0.16 (moved against you)
Mean shifted:   +0.22 (window rolled, chasing the spread)
```

**Conclusion:**
> The z-score decreased, BUT the spread moved the wrong way.
> You exited a losing position thinking it reverted.
> This is not mean reversion—this is **window drift**.

---

## Why This Destroys Your PnL

### In Your Backtest
- **Returns: -52%** (catastrophic loss)
- **Max Drawdown: -98%** (account evaporation)
- **Win rate: 9%** (1 in 11 trades profit)
- **Sharpe: 3.76** (mathematically IMPOSSIBLE given -52% return)

### Why The Metrics Lie
Your Sharpe calculation was incorrect (we fixed it), but the real problem is what trades actually look like:

```
Entry:  Z-score 2.5 (looks extreme)
Exit:   Z-score 0.3 (looks like reversion)
Reality: Spread moved -$200 against the position
Result:  -$87 loss
```

The strategy is:
1. ✓ Identifying extremes (Z > 2.0 works)
2. ✗ NOT waiting for mean reversion
3. ✗ Exiting too early due to rolling window chasing
4. ✓ But charging commission on every trade

---

## What You MUST Fix (Priority Order)

### Fix #1: Detect Actual Reversion (Not Window Drift)

**Current (BROKEN):**
```python
exit_condition = z_score < exit_threshold  # Just checks threshold
```

**Correct:**
```python
# Check that spread actually moved toward the mean
spread_moved_right_direction = (
    (spread - entry_spread) * position_sign < 0  # Spread improving
)
# AND threshold crossed
exit_condition = spread_moved_right_direction and z_score < exit_threshold
```

### Fix #2: Use Fixed-Window Baseline, Not Rolling

**Current (BROKEN):**
```python
mean = spread.rolling(60).mean()  # Changes every day!
std = spread.rolling(60).std()
```

**Better:**
```python
# Use first N days as training period
training_end = 60
mean = spread[:training_end].mean()  # Fixed
std = spread[:training_end].std()    # Fixed
z_score = (spread - mean) / std      # Consistent benchmark
```

Alternative: Use Kalman filter or co-integration tests (advanced).

### Fix #3: Enforce Dollar Neutrality

Already mostly there, but verify:
```python
capital_per_leg = initial_capital * 0.10

qty_long = capital_per_leg / price_long
qty_short = capital_per_leg / price_short

# Check dollar amounts are equal
assert abs(qty_long * price_long - qty_short * price_short) < 1
```

### Fix #4: Add Position Lifecycle Logging

Print EVERY trade:
```
[2024-03-26] ENTRY
  Z-score: 2.02
  Long 5.27 BTC @ $1,898.80 = $10,000
  Short 101.39 ETH @ $98.63 = $10,000
  Expected: mean revert to -79.71
  
[2024-04-26] EXIT
  Z-score: 0.40
  Spread: -79.03 (didn't revert, moved -0.17 against)
  PnL: $-69
  Log: FALSE SIGNAL (window drift, not reversion)
```

---

## Immediate Action Plan

1. **Today**: Fix the fixed-window baseline (1 hour)
2. **Today**: Add spread reversion validation (30 min)
3. **Today**: Commit and test with synthetic data
4. **Tomorrow**: Re-run backtest with real data (3-5 years)
5. **Then**: Monitor for actual profitability

---

## The Hard Truth

You have:
- ✓ Correct infrastructure (backtest engine, data pipeline)
- ✓ Working signal logic (z-score calculation)
- ✗ **Wrong assumption**: rolling window = valid benchmark

This is why careful diagnostic matters. You were one line away from fixing it:

```python
# Change this one line:
mean = spread.rolling(60).mean()     # ← Dynamic (WRONG)
# To this:
mean = spread.iloc[:60].mean()       # ← Fixed (RIGHT)
```

---

## Next Step

I'll implement both fixes and show you the before/after on the same data.
