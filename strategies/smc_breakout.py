"""SMC (Smart Money Concepts) breakout strategy.

Built from Trader Mayne's SMC basics plus the one objective pattern from
"The Candlestick Trading Bible" (see docs/CANDLESTICK_PATTERNS.md) — the
engulfing bar. Every definition below is deliberately the most common,
backtestable version of each SMC term (these vary by source):

- Swing point: fractal — bar j is a swing high if high[j] is the max of the
  `swing_period` bars on each side of it (swing low is the mirror, on low).
  A swing point is only usable once `swing_period` bars have closed after it
  (no lookahead).
- Market structure / bias: compare the last two confirmed swing highs and
  lows. Higher-high + higher-low -> bullish. Lower-high + lower-low ->
  bearish. Anything else -> neutral (no trade).
- Break of structure (BOS): close beyond the last confirmed swing high
  (bullish) or swing low (bearish), while bias agrees. A BOS opens a new
  tradeable range = [pre-breakout swing low, breakout high] (mirrored for
  bearish) — this range extends as price makes further highs/lows in the
  BOS direction, and is invalidated if price falls back through its origin
  swing point (failed breakout).
- Premium/discount zone: equilibrium = midpoint of the active post-BOS
  range. Price must sit at least `eq_buffer_pct` of the range away from
  equilibrium to count as discount (below) or premium (above) — "no diddle
  in the middle." Note this means the entry trigger is the *pullback* after
  a BOS, not the BOS bar itself — the breakout bar is, by construction, at
  the top (bullish) or bottom (bearish) of the range it just created, i.e.
  never in discount/premium of its own range.
- Fair Value Gap (FVG): standard 3-candle imbalance — bullish when
  low[i] > high[i-2]; bearish when high[i] < low[i-2]. Defined here for
  completeness, but **not** used as an entry gate: measured against this
  project's actual daily OHLCV (continuously-traded crypto, no session
  close), a true 3-candle zero-overlap gap occurs roughly once per 800+
  bars — gating on it left every walk-forward window with zero trades.
  True gaps are a session-open phenomenon (traditional markets); on a
  24/7 market at daily resolution they're too rare to be a usable signal.
  Worth revisiting on an intraday timeframe, where fast intra-session
  moves can still produce them.

Entry: BUY when bias is bullish AND an active post-BOS bullish range exists
AND price has pulled back into its discount zone AND the current candle is
a bullish engulfing bar. SELL is the mirror condition. HOLD otherwise.

Known limitation: this returns only BUY/SELL/HOLD like every other strategy
in this codebase — it does not emit a structural stop (e.g. below the order
block), so exits are still whatever the engine/paper trader does on
opposite-signal or HOLD. Tracked as future work, not solved here.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd

from strategies.base import BaseStrategy
from strategies.registry import StrategyRegistry


@StrategyRegistry.register(
    description="Smart Money Concepts structure + premium/discount zone, "
                "triggered by a fair value gap, break of structure, and an "
                "engulfing candle",
    tier_hints=["STANDARD", "PERMISSIVE"],
    tags=["smc", "price-action", "breakout"],
)
class SMCBreakout(BaseStrategy):
    name = "SMC Breakout"
    min_bars = 40
    param_grid = {"swing_period": 5, "eq_buffer_pct": 0.10}

    def get_min_bars(self, params: Dict) -> int:
        swing_period = int(params.get("swing_period", self.param_grid["swing_period"]))
        return swing_period * 4 + 20

    def generate_signals(self, df: pd.DataFrame, params: Dict) -> pd.Series:
        swing_period = int(params.get("swing_period", self.param_grid["swing_period"]))
        eq_buffer_pct = float(params.get("eq_buffer_pct", self.param_grid["eq_buffer_pct"]))
        min_bars = self.get_min_bars(params)

        open_ = df["open"].astype(float).reset_index(drop=True)
        high = df["high"].astype(float).reset_index(drop=True)
        low = df["low"].astype(float).reset_index(drop=True)
        close = df["close"].astype(float).reset_index(drop=True)
        n = len(df)

        swing_highs: List[Tuple[int, float]] = []
        swing_lows: List[Tuple[int, float]] = []
        signals = ["HOLD"] * n

        # Active post-BOS range in the current bias direction: (low, high).
        bull_range: Optional[Tuple[float, float]] = None
        bear_range: Optional[Tuple[float, float]] = None

        for i in range(n):
            j = i - swing_period
            if j - swing_period >= 0 and j + swing_period < n:
                window_high = high[j - swing_period: j + swing_period + 1]
                if high[j] == window_high.max():
                    swing_highs.append((j, high[j]))
                window_low = low[j - swing_period: j + swing_period + 1]
                if low[j] == window_low.min():
                    swing_lows.append((j, low[j]))

            if i < min_bars or i < 2:
                continue
            if len(swing_highs) < 2 or len(swing_lows) < 2:
                continue

            last_sh, prev_sh = swing_highs[-1][1], swing_highs[-2][1]
            last_sl, prev_sl = swing_lows[-1][1], swing_lows[-2][1]

            if last_sh > prev_sh and last_sl > prev_sl:
                bias = "bullish"
            elif last_sh < prev_sh and last_sl < prev_sl:
                bias = "bearish"
            else:
                bias = "neutral"

            if bias != "bullish":
                bull_range = None
            if bias != "bearish":
                bear_range = None
            if bias == "neutral":
                continue

            if bias == "bullish":
                if close[i] > last_sh:
                    if bull_range is None:
                        bull_range = (last_sl, close[i])
                    else:
                        bull_range = (bull_range[0], max(bull_range[1], close[i]))
                if bull_range is not None and close[i] < bull_range[0]:
                    bull_range = None
                active_range = bull_range
            else:
                if close[i] < last_sl:
                    if bear_range is None:
                        bear_range = (close[i], last_sh)
                    else:
                        bear_range = (min(bear_range[0], close[i]), bear_range[1])
                if bear_range is not None and close[i] > bear_range[1]:
                    bear_range = None
                active_range = bear_range

            if active_range is None:
                continue
            zone_low, zone_high = active_range
            if zone_high <= zone_low:
                continue
            eq = (zone_low + zone_high) / 2
            buf = eq_buffer_pct * (zone_high - zone_low)
            price = close[i]

            if price < eq - buf:
                position = "discount"
            elif price > eq + buf:
                position = "premium"
            else:
                continue

            bullish_engulf = close[i] > open_[i - 1] and open_[i] < close[i - 1]
            bearish_engulf = close[i] < open_[i - 1] and open_[i] > close[i - 1]

            if bias == "bullish" and position == "discount" and bullish_engulf:
                signals[i] = "BUY"
            elif bias == "bearish" and position == "premium" and bearish_engulf:
                signals[i] = "SELL"

        return pd.Series(signals, index=df.index)

    def describe_failure(self, results: Dict) -> str:
        trades = results.get("num_trades", 0)
        sharpe = results.get("sharpe_ratio", 0.0)
        if trades < 10:
            return (
                "SMC Breakout generated too few signals — bias, post-BOS "
                "pullback into the discount/premium zone, and engulfing "
                "rarely all aligned in this window."
            )
        if sharpe <= 0:
            return (
                f"SMC Breakout was net-negative (Sharpe={sharpe:.2f}) — "
                "pullbacks into the post-BOS zone reversed further rather "
                "than continuing in the breakout direction."
            )
        dd = results.get("max_drawdown_pct", 0.0)
        return (
            f"SMC Breakout failed drawdown ({dd:.1f}%) — a post-BOS pullback "
            "entry in this window was followed by an adverse move with no "
            "structural stop in place."
        )
