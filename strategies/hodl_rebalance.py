"""HODL with periodic rebalance strategy (single-asset: buy and hold)."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from strategies.base import BaseStrategy
from strategies.registry import StrategyRegistry


@StrategyRegistry.register(
    description="Buy-and-hold with periodic rebalance back to target weight",
    tier_hints=["CONSERVATIVE"],
    tags=["passive", "rebalance"],
)
class HODLRebalance(BaseStrategy):
    name = "HODL with Rebalance"
    min_bars = 1
    param_grid = {"rebalance_days": 30}

    def generate_signals(self, df: pd.DataFrame, params: Dict) -> pd.Series:
        # Single-asset: BUY on bar 1 (bar 0 is warmup), HOLD forever.
        signals = ["HOLD"] * len(df)
        if len(df) > 1:
            signals[1] = "BUY"
        return pd.Series(signals, index=df.index)

    def describe_failure(self, results: Dict) -> str:
        dd = results.get("max_drawdown_pct", 0.0)
        return (
            f"HODL's drawdown ({dd:.1f}%) is just the asset's own volatility — "
            "buy-and-hold has no mechanism to limit it."
        )
