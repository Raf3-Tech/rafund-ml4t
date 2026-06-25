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
        """BUY on calendar days where epoch-day % interval_days == 0.

        Anchored to absolute calendar time rather than position within `df`
        — live/paper trading always passes a freshly-sliced "last N bars"
        window, so a position-relative schedule (e.g. "every 7th row") would
        land on the same bar-offset every single cycle and could permanently
        miss every buy day. Calendar-anchoring makes the schedule the same
        regardless of where the window happens to start.
        """
        interval = int(params.get("interval_days", self.param_grid["interval_days"]))
        epoch_day = (
            pd.to_datetime(df["timestamp"], utc=True).dt.floor("D") - pd.Timestamp("1970-01-01", tz="UTC")
        ).dt.days
        signals = (epoch_day % interval == 0).map({True: "BUY", False: "HOLD"})
        signals.iloc[: self.min_bars] = "HOLD"  # warmup
        return signals

    def describe_failure(self, results: Dict) -> str:
        dd = results.get("max_drawdown_pct", 0.0)
        return (
            f"DCA's drawdown ({dd:.1f}%) reflects full exposure to crypto's "
            "historical 50-85% drawdowns — buying on a schedule doesn't avoid them."
        )
