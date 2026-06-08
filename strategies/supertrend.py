"""Supertrend strategy."""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy


class SupertrendStrategy(BaseStrategy):
    name = "Supertrend"
    min_bars = 11
    param_grid = {"atr_period": 10, "multiplier": 3.0}

    def get_min_bars(self, params: Dict) -> int:
        return int(params.get("atr_period", self.param_grid["atr_period"])) + 1

    def _compute_supertrend(
        self, df: pd.DataFrame, atr_period: int, multiplier: float
    ) -> pd.Series:
        high = df["high"].astype(float).reset_index(drop=True)
        low = df["low"].astype(float).reset_index(drop=True)
        close = df["close"].astype(float).reset_index(drop=True)

        # True Range
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.ewm(span=atr_period, adjust=False).mean()

        hl2 = (high + low) / 2.0
        upper_basic = hl2 + multiplier * atr
        lower_basic = hl2 - multiplier * atr

        upper_band = upper_basic.copy()
        lower_band = lower_basic.copy()

        for i in range(1, len(close)):
            upper_band.iloc[i] = (
                upper_basic.iloc[i]
                if upper_basic.iloc[i] < upper_band.iloc[i - 1] or close.iloc[i - 1] > upper_band.iloc[i - 1]
                else upper_band.iloc[i - 1]
            )
            lower_band.iloc[i] = (
                lower_basic.iloc[i]
                if lower_basic.iloc[i] > lower_band.iloc[i - 1] or close.iloc[i - 1] < lower_band.iloc[i - 1]
                else lower_band.iloc[i - 1]
            )

        # Supertrend direction: 1 = bullish (close above), -1 = bearish
        trend = pd.Series(1, index=close.index)
        for i in range(1, len(close)):
            if trend.iloc[i - 1] == 1:
                trend.iloc[i] = 1 if close.iloc[i] >= lower_band.iloc[i] else -1
            else:
                trend.iloc[i] = -1 if close.iloc[i] <= upper_band.iloc[i] else 1

        return trend

    def generate_signals(self, df: pd.DataFrame, params: Dict) -> pd.Series:
        atr_period = int(params.get("atr_period", self.param_grid["atr_period"]))
        multiplier = float(params.get("multiplier", self.param_grid["multiplier"]))
        warmup = atr_period + 1

        trend = self._compute_supertrend(df, atr_period, multiplier)

        signals = ["HOLD"] * len(df)
        for i in range(warmup, len(df)):
            if trend.iloc[i] == 1 and trend.iloc[i - 1] == -1:
                signals[i] = "BUY"
            elif trend.iloc[i] == -1 and trend.iloc[i - 1] == 1:
                signals[i] = "SELL"

        return pd.Series(signals, index=df.index)

    def describe_failure(self, results: Dict) -> str:
        trades = results.get("num_trades", 0)
        sharpe = results.get("sharpe_ratio", 0.0)
        if trades < 10:
            return "Supertrend generated few direction changes — price action was choppy with no sustained trend."
        if sharpe <= 0:
            return f"Supertrend signals were net-negative (Sharpe={sharpe:.2f}) — frequent trend flips eroded returns."
        dd = results.get("max_drawdown_pct", 0.0)
        return f"Supertrend failed drawdown constraint ({dd:.1f}%) — a sustained adverse trend incurred large losses."
