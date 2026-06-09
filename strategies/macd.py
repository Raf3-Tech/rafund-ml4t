"""MACD strategy."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from strategies.base import BaseStrategy
from strategies.registry import StrategyRegistry


@StrategyRegistry.register(
    description="MACD histogram crossover for trend momentum",
    tier_hints=["STANDARD", "PERMISSIVE"],
    tags=["trend", "momentum"],
)
class MACDStrategy(BaseStrategy):
    name = "MACD"
    min_bars = 35
    param_grid = {"fast": 12, "slow": 26, "signal": 9}

    def get_min_bars(self, params: Dict) -> int:
        slow = int(params.get("slow", self.param_grid["slow"]))
        signal = int(params.get("signal", self.param_grid["signal"]))
        return slow + signal

    def generate_signals(self, df: pd.DataFrame, params: Dict) -> pd.Series:
        fast = int(params.get("fast", self.param_grid["fast"]))
        slow = int(params.get("slow", self.param_grid["slow"]))
        signal_period = int(params.get("signal", self.param_grid["signal"]))
        warmup = slow + signal_period

        close = df["close"].astype(float).reset_index(drop=True)
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()

        signals = ["HOLD"] * len(df)
        for i in range(warmup, len(df)):
            prev_above = macd_line.iloc[i - 1] > signal_line.iloc[i - 1]
            curr_above = macd_line.iloc[i] > signal_line.iloc[i]
            if not prev_above and curr_above:
                signals[i] = "BUY"
            elif prev_above and not curr_above:
                signals[i] = "SELL"

        return pd.Series(signals, index=df.index)

    def describe_failure(self, results: Dict) -> str:
        trades = results.get("num_trades", 0)
        sharpe = results.get("sharpe_ratio", 0.0)
        if trades < 10:
            return "MACD generated insufficient trades — histogram stayed flat throughout the window."
        if sharpe <= 0:
            return f"MACD crossover signals were net-negative (Sharpe={sharpe:.2f}) in this market regime."
        dd = results.get("max_drawdown_pct", 0.0)
        return f"MACD failed drawdown ({dd:.1f}%) — whipsawing market generated consecutive losing trades."
