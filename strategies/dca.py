"""Dollar Cost Averaging strategy (long-only accumulation)."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from strategies.base import BaseStrategy
from strategies.registry import StrategyRegistry


@StrategyRegistry.register(
    description="Periodic fixed-interval buying for long-term accumulation",
    tier_hints=["CONSERVATIVE", "STANDARD"],
    tags=["passive", "accumulation"],
)
class DCAStrategy(BaseStrategy):
    name = "Dollar Cost Averaging"
    min_bars = 1
    param_grid = {"interval_days": 7}

    def generate_signals(self, df: pd.DataFrame, params: Dict) -> pd.Series:
        interval = int(params.get("interval_days", self.param_grid["interval_days"]))
        signals = ["HOLD"] * len(df)
        # Bar 0 is warmup; first BUY at bar `interval`
        for i in range(interval, len(df), interval):
            signals[i] = "BUY"
        return pd.Series(signals, index=df.index)

    def describe_failure(self, results: Dict) -> str:
        dd = results.get("max_drawdown_pct", 0.0)
        return (
            f"DCA's drawdown ({dd:.1f}%) reflects full exposure to crypto's "
            "historical 50-85% drawdowns — buying on a schedule doesn't avoid them."
        )
