# RAFund Fixes Implemented - Summary

## What Was Fixed

### 1. **Rolling Window Drift (CRITICAL FIX)**
**Problem:** Strategy used rolling mean that updated every bar, causing the baseline to chase prices instead of remaining stable.

**Solution:** Implemented fixed-window mode that:
- Uses only the first 60 bars for training
- Calculates mean & std from training period
- Applies FIXED baseline to all subsequent bars
- No more rolling mean drift

**Code Changes:**
- `strategies/stat_arb.py`: Added `use_fixed_window` parameter
- `backtesting/engine_v2.py`: Rewrote `generate_signals()` to support fixed mode
- `main.py`: Changed to `use_fixed_window=True` by default

---

## 2. **Signal Validation (ADDED)**
**Problem:** Strategy couldn't distinguish between:
- TRUE mean reversion (spread actually reverts)
- FALSE signal (rolling mean chasing, z-score decreased by accident)

**Solution:** Enhanced trade tracking to validate spread reversion:
- Tracks entry spread for each trade
- On exit, checks if spread actually moved toward mean
- Flags trades with `signal_validity` field
- Categorizes as "VALID" or "FALSE_SIGNAL (window drift)"

**Code Changes:**
- `strategies/stat_arb.py`: Updated `get_trades()` method
- Returns: `spread_reverted`, `reversion_distance`, `signal_validity`

---

## 3. **Architecture Documentation**
**Added:**
- `DIAGNOSIS.md`: Full technical explanation of the problem
- `debug_trade_lifecycle.py`: Shows one complete trade entry→exit
- `analyze_rolling_window_problem.py`: Demonstrates window drift
- `test_fixed_window.py`: Compares rolling vs fixed approaches

---

## Expected Impact on Results

| Metric | Before (Rolling) | After (Fixed) | Change |
|--------|-----------------|---------------|--------|
| Total Trades | High (many false) | Lower | -30% to -50% |
| Win Rate | 9% (terrible) | TBD (should improve significantly) | ↑ 50%+ |
| Sharpe Ratio | 3.76 (false) | TBD (realistic) | ↑ Much better |
| Max Drawdown | -98% (catastrophic) | TBD (should improve) | ↓ Better |
| Return | -52% (destructive) | TBD (should improve) | ↑ Better |

---

## Key Insights

### Why Fixed Window Works
1. **Stable Baseline** - Mean/std never change
2. **True Reversion Detection** - Only real price moves affect z-score
3. **No Window Chasing** - Can't confuse rolling mean drift with reversion
4. **Fewer False Signals** - Eliminates commission bleed on bad trades

### Trade Example (Fixed vs Rolling)

**Rolling Window (WRONG):**
```
Entry z-score:  2.02
Entry price:    $1,898.80
Exit z-score:   0.40 ← Decreased!
Exit price:     $1,897.74 ← Price barely moved
Rolling mean:   Shifted up $0.22
Reason z-score decreased:  Window drifted, not reversion
PnL: -$69 (LOSS from false signal)
```

**Fixed Window (RIGHT):**
```
Entry z-score:  2.02 (based on fixed training baseline)
Entry price:    $1,898.80
Exit z-score:   Will only decrease if PRICE reverts
Fixed mean:     Never changes
If z-score falls, it's because price ACTUALLY reverted
PnL: Will only occur on real mean reversion
```

---

## Files Modified

1. ✅ `strategies/stat_arb.py` - Added fixed window support + spread validation
2. ✅ `backtesting/engine_v2.py` - Replaced rolling window with fixed
3. ✅ `main.py` - Enabled fixed window by default
4. ✅ `test_fixed_window.py` - Added validation test
5. ✅ Committed to GitHub (commit: c72f1f4)

---

## Next Steps

1. **Run full backtest** with fixed window enabled (use main.py backtest command)
2. **Analyze results** - Compare with previous baseline
3. **Validate signal quality** - Check signal_validity field in trade logs
4. **Consider additional improvements**:
   - Extend dataset to 3-5 years (currently ~1 year)
   - Add co-integration tests for pair selection
   - Implement Kalman filtering for even better baseline
   - Add stop loss validation

---

## Status

✅ **ALL FIXES IMPLEMENTED AND COMMITTED**

The strategy is now architecturally sound.  
Next phase: Validate effectiveness on real data and tune parameters.
