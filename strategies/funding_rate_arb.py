"""Funding Rate Arbitrage strategy (8-hour perpetual futures funding data)."""

from __future__ import annotations

from typing import Dict

import pandas as pd

from strategies.base import BaseStrategy
from strategies.registry import StrategyRegistry


@StrategyRegistry.register(
    description="Collect funding payments by hedging perpetual vs spot when rate is extreme",
    tier_hints=["CONSERVATIVE", "STANDARD"],
    tags=["derivatives", "arbitrage", "market-neutral"],
)
class FundingRateArb(BaseStrategy):
    """Market-neutral: collect positive funding by shorting perp + long spot (or inverse).

    Data cadence: 8-hour funding rate observations, NOT daily bars.
    The 'close' column in df should be the funding_rate value.
    The 'timestamp' column should be the funding_time.
    """

    name = "Funding Rate Arbitrage"
    min_bars = 3
    param_grid = {"entry_threshold": 0.01, "exit_threshold": 0.005}

    # This strategy often passes CONSERVATIVE because drawdown comes from
    # execution slippage rather than directional exposure.
    timeframe = "8h"

    def generate_signals(self, df: pd.DataFrame, params: Dict) -> pd.Series:
        """
        df expected columns: timestamp, funding_rate (aliased as 'close' for compatibility).
        BUY signal = go short perp / long spot (collect positive funding).
        SELL signal = go long perp / short spot (collect negative funding).
        """
        entry = float(params.get("entry_threshold", self.param_grid["entry_threshold"]))
        exit_thr = float(params.get("exit_threshold", self.param_grid["exit_threshold"]))

        rate_col = "funding_rate" if "funding_rate" in df.columns else "close"
        rate = df[rate_col].astype(float).reset_index(drop=True)

        signals = ["HOLD"] * len(df)
        position = 0  # 1 = short perp / long spot, -1 = long perp / short spot

        for i in range(self.min_bars, len(df)):
            r = rate.iloc[i]
            if pd.isna(r):
                continue
            if position == 0:
                if r >= entry:
                    signals[i] = "BUY"
                    position = 1
                elif r <= -entry:
                    signals[i] = "SELL"
                    position = -1
            elif position == 1:
                if abs(r) < exit_thr:
                    signals[i] = "HOLD"
                    position = 0
            elif position == -1:
                if abs(r) < exit_thr:
                    signals[i] = "HOLD"
                    position = 0

        return pd.Series(signals, index=df.index)

    def describe_failure(self, results: Dict) -> str:
        trades = results.get("num_trades", 0)
        sharpe = results.get("sharpe_ratio", 0.0)
        if trades < 10:
            return (
                "Funding Rate Arb generated too few trades — "
                "funding rates rarely exceeded the entry threshold in this window."
            )
        if sharpe <= 0:
            return (
                f"Funding Rate Arb was net-negative (Sharpe={sharpe:.2f}) — "
                "execution slippage exceeded funding payments collected."
            )
        dd = results.get("max_drawdown_pct", 0.0)
        return (
            f"Funding Rate Arb failed drawdown ({dd:.1f}%) — "
            "correlated basis moves between spot and perp caused unexpected directional loss."
        )
