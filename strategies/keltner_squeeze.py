"""Keltner Squeeze (volatility compression breakout) strategy."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from strategies.base import BaseStrategy
from strategies.registry import StrategyRegistry


@StrategyRegistry.register(
    description="Momentum breakout from Bollinger/Keltner squeeze compression",
    tier_hints=["STANDARD", "PERMISSIVE"],
    tags=["volatility", "momentum"],
)
class KeltnerSqueeze(BaseStrategy):
    name = "Keltner Squeeze"
    min_bars = 25
    param_grid = {"ema_period": 20, "atr_period": 14, "bb_std": 2.0, "kc_multiplier": 1.5}

    def get_min_bars(self, params: Dict) -> int:
        ema = int(params.get("ema_period", self.param_grid["ema_period"]))
        atr = int(params.get("atr_period", self.param_grid["atr_period"]))
        return max(ema, atr) + 5

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
        ema_period = int(params.get("ema_period", self.param_grid["ema_period"]))
        atr_period = int(params.get("atr_period", self.param_grid["atr_period"]))
        bb_std = float(params.get("bb_std", self.param_grid["bb_std"]))
        kc_mult = float(params.get("kc_multiplier", self.param_grid["kc_multiplier"]))
        warmup = max(ema_period, atr_period) + 5

        close = df["close"].astype(float).reset_index(drop=True)
        atr = self._compute_atr(df, atr_period)

        # Bollinger Bands
        bb_mid = close.rolling(ema_period).mean()
        bb_std_series = close.rolling(ema_period).std()
        bb_upper = bb_mid + bb_std * bb_std_series
        bb_lower = bb_mid - bb_std * bb_std_series

        # Keltner Channels
        kc_mid = close.ewm(span=ema_period, adjust=False).mean()
        kc_upper = kc_mid + kc_mult * atr
        kc_lower = kc_mid - kc_mult * atr

        # Squeeze: BB inside KC
        in_squeeze = (bb_upper < kc_upper) & (bb_lower > kc_lower)

        signals = ["HOLD"] * len(df)
        for i in range(warmup, len(df)):
            if pd.isna(bb_upper.iloc[i]) or pd.isna(kc_upper.iloc[i]):
                continue
            # Breakout: squeeze just released and BBands expand outside KC
            was_squeezed = in_squeeze.iloc[i - 1] if i > 0 else False
            now_free = not in_squeeze.iloc[i]
            if was_squeezed and now_free:
                if close.iloc[i] > kc_mid.iloc[i]:
                    signals[i] = "BUY"
                else:
                    signals[i] = "SELL"

        return pd.Series(signals, index=df.index)

    def describe_failure(self, results: Dict) -> str:
        trades = results.get("num_trades", 0)
        sharpe = results.get("sharpe_ratio", 0.0)
        if trades < 10:
            return (
                "Keltner Squeeze generated too few breakouts — "
                "BBands never compressed inside KC or released in this window."
            )
        if sharpe <= 0:
            return (
                f"Keltner Squeeze breakouts were net-negative (Sharpe={sharpe:.2f}) — "
                "compression released in the wrong direction or faded quickly."
            )
        dd = results.get("max_drawdown_pct", 0.0)
        return (
            f"Keltner Squeeze failed drawdown ({dd:.1f}%) — "
            "a squeeze breakout reversed sharply after entry."
        )
