"""Donchian Channel Breakout strategy."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from strategies.base import BaseStrategy
from strategies.registry import StrategyRegistry


@StrategyRegistry.register(
    description="Price breakout above/below N-period Donchian channel",
    tier_hints=["STANDARD", "PERMISSIVE"],
    tags=["trend", "breakout"],
)
class DonchianBreakout(BaseStrategy):
    name = "Donchian Breakout"
    min_bars = 20
    param_grid = {"period": 20}

    def get_min_bars(self, params: Dict) -> int:
        return int(params.get("period", self.param_grid["period"]))

    def generate_signals(self, df: pd.DataFrame, params: Dict) -> pd.Series:
        period = int(params.get("period", self.param_grid["period"]))

        high = df["high"].astype(float).reset_index(drop=True)
        low = df["low"].astype(float).reset_index(drop=True)
        close = df["close"].astype(float).reset_index(drop=True)

        upper = high.rolling(period).max().shift(1)
        lower = low.rolling(period).min().shift(1)

        signals = ["HOLD"] * len(df)
        for i in range(period, len(df)):
            if pd.isna(upper.iloc[i]) or pd.isna(lower.iloc[i]):
                continue
            if close.iloc[i] > upper.iloc[i]:
                signals[i] = "BUY"
            elif close.iloc[i] < lower.iloc[i]:
                signals[i] = "SELL"

        return pd.Series(signals, index=df.index)

    def describe_failure(self, results: Dict) -> str:
        trades = results.get("num_trades", 0)
        sharpe = results.get("sharpe_ratio", 0.0)
        if trades < 10:
            return (
                "Donchian Breakout generated too few breakouts — "
                "price remained inside the channel throughout the window."
            )
        if sharpe <= 0:
            return (
                f"Donchian Breakout signals were net-negative (Sharpe={sharpe:.2f}) — "
                "breakouts reverted instead of continuing."
            )
        dd = results.get("max_drawdown_pct", 0.0)
        return f"Donchian Breakout failed drawdown ({dd:.1f}%) — false breakout followed by sharp reversal."
