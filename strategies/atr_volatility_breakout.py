"""ATR Volatility Breakout strategy."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from strategies.base import BaseStrategy


class ATRVolatilityBreakout(BaseStrategy):
    name = "ATR Volatility Breakout"
    min_bars = 14
    param_grid = {"atr_period": 14, "multiplier": 1.5}

    def get_min_bars(self, params: Dict) -> int:
        return int(params.get("atr_period", self.param_grid["atr_period"]))

    def _compute_atr(self, df: pd.DataFrame, period: int) -> pd.Series:
        high = df["high"].astype(float).reset_index(drop=True)
        low = df["low"].astype(float).reset_index(drop=True)
        close = df["close"].astype(float).reset_index(drop=True)
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        return tr.ewm(span=period, adjust=False).mean()

    def generate_signals(self, df: pd.DataFrame, params: Dict) -> pd.Series:
        atr_period = int(params.get("atr_period", self.param_grid["atr_period"]))
        multiplier = float(params.get("multiplier", self.param_grid["multiplier"]))

        close = df["close"].astype(float).reset_index(drop=True)
        atr = self._compute_atr(df, atr_period)
        daily_move = close.diff().abs()

        signals = ["HOLD"] * len(df)
        for i in range(atr_period, len(df)):
            if pd.isna(atr.iloc[i]) or pd.isna(daily_move.iloc[i]):
                continue
            if daily_move.iloc[i] > multiplier * atr.iloc[i]:
                if close.iloc[i] > close.iloc[i - 1]:
                    signals[i] = "BUY"
                else:
                    signals[i] = "SELL"

        return pd.Series(signals, index=df.index)

    def describe_failure(self, results: Dict) -> str:
        trades = results.get("num_trades", 0)
        sharpe = results.get("sharpe_ratio", 0.0)
        if trades < 10:
            return (
                "ATR Volatility Breakout generated too few signals — "
                "daily price moves rarely exceeded the ATR multiplier threshold."
            )
        if sharpe <= 0:
            return (
                f"ATR Volatility Breakout was net-negative (Sharpe={sharpe:.2f}) — "
                "momentum bursts reversed quickly rather than continuing."
            )
        dd = results.get("max_drawdown_pct", 0.0)
        return (
            f"ATR Volatility Breakout failed drawdown ({dd:.1f}%) — "
            "a large adverse volatility spike caused a drawdown breach."
        )
