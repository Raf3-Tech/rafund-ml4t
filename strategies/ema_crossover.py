"""EMA Crossover strategy."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from strategies.base import BaseStrategy


class EMACrossover(BaseStrategy):
    name = "EMA Crossover"
    min_bars = 50
    param_grid = {"fast": 20, "slow": 50}

    _FAST_GRID = [10, 20, 50]
    _SLOW_GRID = [50, 100, 200]

    def get_min_bars(self, params: Dict) -> int:
        return params.get("slow", self.param_grid["slow"])

    def generate_signals(self, df: pd.DataFrame, params: Dict) -> pd.Series:
        fast = int(params.get("fast", self.param_grid["fast"]))
        slow = int(params.get("slow", self.param_grid["slow"]))
        warmup = slow

        close = df["close"].astype(float).reset_index(drop=True)
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()

        signals = ["HOLD"] * len(df)
        for i in range(warmup, len(df)):
            prev_above = ema_fast.iloc[i - 1] > ema_slow.iloc[i - 1]
            curr_above = ema_fast.iloc[i] > ema_slow.iloc[i]
            if not prev_above and curr_above:
                signals[i] = "BUY"
            elif prev_above and not curr_above:
                signals[i] = "SELL"

        return pd.Series(signals, index=df.index)

    def describe_failure(self, results: Dict) -> str:
        trades = results.get("num_trades", 0)
        sharpe = results.get("sharpe_ratio", 0.0)
        dd = results.get("max_drawdown_pct", 0.0)
        if trades < 10:
            return (
                "EMA Crossover generated no trades in this window because the asset "
                "was in a tight range with too few EMA crossovers."
            )
        if sharpe <= 0:
            return (
                f"EMA Crossover produced negative Sharpe ({sharpe:.2f}) — "
                "crossover signals were mostly false in this regime."
            )
        return (
            f"EMA Crossover failed drawdown constraint ({dd:.1f}%) — "
            "trend reversed sharply after entry."
        )
