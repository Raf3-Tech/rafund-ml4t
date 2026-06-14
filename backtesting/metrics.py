"""Shared performance metrics computation (Phase D consolidation).

Both BacktestEngine and WalkForwardWindowEngine use this to turn an equity
curve into the standard metrics dict, eliminating the duplicate logic that
previously lived in window_engine._stats_from_equity.
"""

from __future__ import annotations

from typing import Dict, List

import numpy as np
import pandas as pd


def compute_performance_metrics(
    equity_curve: List[float],
    num_wins: int,
    num_trades: int,
    annualization: int,
) -> Dict:
    """Compute standard performance metrics from a unit-normalised equity curve.

    Args:
        equity_curve: Sequence of equity values. Should start at 1.0 for
            unit-normalised series; BacktestEngine callers normalize before
            calling so total_return_pct is meaningful.
        num_wins: Number of closed trades with positive P&L.
        num_trades: Total number of closed trades.
        annualization: Periods per year (e.g. 365 for daily, 1095 for 8h).

    Returns:
        Dict with keys: total_return_pct, sharpe_ratio, max_drawdown_pct,
        win_rate_pct, num_trades.
    """
    eq = pd.Series(equity_curve, dtype=float)
    if len(eq) < 2:
        return empty_metrics()
    rets = eq.pct_change().dropna()
    mean_r = float(rets.mean()) if len(rets) else 0.0
    std_r = float(rets.std()) if len(rets) else 0.0
    sharpe = float(mean_r / std_r * np.sqrt(annualization)) if std_r > 0 else 0.0
    cummax = eq.cummax()
    max_dd = float(abs(((eq - cummax) / cummax).min()) * 100)
    total_return = float((eq.iloc[-1] - eq.iloc[0]) / eq.iloc[0] * 100)
    win_rate = float(num_wins / num_trades * 100) if num_trades > 0 else 0.0
    return {
        "total_return_pct": total_return,
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": max_dd,
        "win_rate_pct": win_rate,
        "num_trades": num_trades,
    }


def empty_metrics() -> Dict:
    return {
        "total_return_pct": 0.0,
        "sharpe_ratio": 0.0,
        "max_drawdown_pct": 0.0,
        "win_rate_pct": 0.0,
        "num_trades": 0,
    }
