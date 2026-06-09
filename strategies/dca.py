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

    # DCA will always fail CONSERVATIVE drawdown (crypto drawdowns 50-85%).
    # Only valid for STANDARD and PERMISSIVE tiers.
    tier_note = "STANDARD/PERMISSIVE only — never prop-compatible"

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
            f"DCA failed CONSERVATIVE drawdown ({dd:.1f}%) — expected, as crypto assets "
            "have historical drawdowns of 50-85%. This strategy is STANDARD/PERMISSIVE only."
        )
