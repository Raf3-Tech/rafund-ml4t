"""Bollinger Band mean-reversion strategy."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from strategies.base import BaseStrategy
from strategies.registry import StrategyRegistry


@StrategyRegistry.register(
    description="Mean-reversion on Bollinger Band extremes",
    tier_hints=["CONSERVATIVE", "STANDARD"],
    tags=["mean-reversion"],
)
class BollingerReversion(BaseStrategy):
    name = "Bollinger Band Reversion"
    min_bars = 20
    param_grid = {"period": 20, "std_dev": 2.0}

    def get_min_bars(self, params: Dict) -> int:
        return int(params.get("period", self.param_grid["period"]))

    def generate_signals(self, df: pd.DataFrame, params: Dict) -> pd.Series:
        period = int(params.get("period", self.param_grid["period"]))
        std_dev = float(params.get("std_dev", self.param_grid["std_dev"]))

        close = df["close"].astype(float).reset_index(drop=True)
        mid = close.rolling(period).mean()
        std = close.rolling(period).std()
        upper = mid + std_dev * std
        lower = mid - std_dev * std

        signals = ["HOLD"] * len(df)
        for i in range(period, len(df)):
            if pd.isna(upper.iloc[i]) or pd.isna(lower.iloc[i]):
                continue
            if close.iloc[i] <= lower.iloc[i]:
                signals[i] = "BUY"
            elif close.iloc[i] >= upper.iloc[i]:
                signals[i] = "SELL"

        return pd.Series(signals, index=df.index)

    def describe_failure(self, results: Dict) -> str:
        trades = results.get("num_trades", 0)
        sharpe = results.get("sharpe_ratio", 0.0)
        if trades < 10:
            return (
                "Bollinger Band Reversion had too few band touches — "
                "price stayed inside the bands in a low-volatility environment."
            )
        if sharpe <= 0:
            return (
                f"Bollinger Band Reversion was net-negative (Sharpe={sharpe:.2f}) — "
                "the market was trending rather than ranging so mean reversion failed."
            )
        dd = results.get("max_drawdown_pct", 0.0)
        return (
            f"Bollinger Band Reversion failed drawdown ({dd:.1f}%) — "
            "a strong trending move extended far below the lower band without reverting."
        )
