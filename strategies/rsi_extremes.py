"""RSI Extremes strategy."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from strategies.base import BaseStrategy
from strategies.registry import StrategyRegistry


@StrategyRegistry.register(
    description="Counter-trend entries at RSI oversold/overbought extremes",
    tier_hints=["CONSERVATIVE", "STANDARD"],
    tags=["mean-reversion", "oscillator"],
)
class RSIExtremes(BaseStrategy):
    name = "RSI Extremes"
    min_bars = 14
    param_grid = {"period": 14, "oversold": 30, "overbought": 70}

    def get_min_bars(self, params: Dict) -> int:
        return int(params.get("period", self.param_grid["period"]))

    def _compute_rsi(self, close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1.0 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1.0 / period, adjust=False).mean()
        rs = gain / loss.replace(0, float("inf"))
        return 100.0 - 100.0 / (1.0 + rs)

    def generate_signals(self, df: pd.DataFrame, params: Dict) -> pd.Series:
        period = int(params.get("period", self.param_grid["period"]))
        oversold = float(params.get("oversold", self.param_grid["oversold"]))
        overbought = float(params.get("overbought", self.param_grid["overbought"]))

        close = df["close"].astype(float).reset_index(drop=True)
        rsi = self._compute_rsi(close, period)

        signals = ["HOLD"] * len(df)
        for i in range(period, len(df)):
            if pd.isna(rsi.iloc[i]):
                continue
            if rsi.iloc[i] < oversold:
                signals[i] = "BUY"
            elif rsi.iloc[i] > overbought:
                signals[i] = "SELL"

        return pd.Series(signals, index=df.index)

    def describe_failure(self, results: Dict) -> str:
        trades = results.get("num_trades", 0)
        sharpe = results.get("sharpe_ratio", 0.0)
        if trades < 10:
            return (
                "RSI Extremes generated too few signals — RSI stayed in the neutral zone "
                "throughout the window (no extreme oversold/overbought readings)."
            )
        if sharpe <= 0:
            return (
                f"RSI Extremes was net-negative (Sharpe={sharpe:.2f}) — "
                "oversold readings continued lower instead of reverting."
            )
        dd = results.get("max_drawdown_pct", 0.0)
        return (
            f"RSI Extremes failed drawdown ({dd:.1f}%) — "
            "a prolonged trending move kept RSI at extremes without reverting."
        )
