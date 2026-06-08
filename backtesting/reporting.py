"""Equity curve reporting for Raf3nd ML4T walk-forward validation."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import structlog

from backtesting.walk_forward import WalkForwardResult
from config.constants import PERIODS_PER_YEAR

logger = structlog.get_logger(__name__)


def generate_equity_report(
    result: WalkForwardResult,
    strategy_name: str,
    output_dir: str = "results/",
) -> str:
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("matplotlib_not_installed", strategy=strategy_name)
        return ""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    png_path = output_path / f"equity_{strategy_name}_{timestamp}.png"
    csv_path = output_path / f"equity_{strategy_name}_{timestamp}.csv"

    all_folds = []
    for fold in result.folds:
        curve = pd.DataFrame(fold.equity_curve)
        if curve.empty:
            continue
        curve = curve.copy()
        curve["fold"] = fold.fold
        all_folds.append(curve)

    if not all_folds:
        logger.warning("no_equity_curves_to_report", strategy=strategy_name)
        return ""

    combined = pd.concat(all_folds, ignore_index=True)
    combined["timestamp"] = pd.to_datetime(combined["timestamp"])
    combined = combined.sort_values("timestamp")
    combined["rolling_sharpe"] = (
        combined["daily_return"].rolling(window=30, min_periods=5).mean()
        / combined["daily_return"].rolling(window=30, min_periods=5).std()
        * np.sqrt(PERIODS_PER_YEAR)
    )
    combined["drawdown_pct"] = (
        combined["equity"] / combined["equity"].cummax() - 1.0
    ) * 100.0

    fig, axes = plt.subplots(3, 1, figsize=(14, 14), sharex=True)

    for fold in result.folds:
        curve = pd.DataFrame(fold.equity_curve)
        if curve.empty:
            continue
        curve["timestamp"] = pd.to_datetime(curve["timestamp"])
        axes[0].plot(curve["timestamp"], curve["equity"], label=f"Fold {fold.fold}")
    axes[0].set_title(f"OOS Equity Curves — {strategy_name}")
    axes[0].set_ylabel("Equity")
    axes[0].legend(loc="upper left", fontsize="small")
    axes[0].grid(True)

    axes[1].plot(combined["timestamp"], combined["rolling_sharpe"], color="tab:blue")
    axes[1].set_title("Rolling 30-day Sharpe Ratio")
    axes[1].set_ylabel("Sharpe")
    axes[1].grid(True)

    axes[2].plot(combined["timestamp"], combined["drawdown_pct"], color="tab:red")
    axes[2].axhline(-6.0, color="black", linestyle="--", linewidth=1)
    axes[2].set_title("Drawdown Curve")
    axes[2].set_ylabel("Drawdown (%)")
    axes[2].set_xlabel("Date")
    axes[2].grid(True)

    text = (
        f"Mean Sharpe: {result.aggregate.mean_oos_sharpe:.2f}\n"
        f"Worst Drawdown: {result.aggregate.worst_oos_drawdown:.2f}%\n"
        f"Folds Profitable: {result.aggregate.pct_folds_profitable:.1f}%\n"
        f"Account Failures: {result.aggregate.pct_folds_account_failed:.1f}%\n"
        f"Gate: {'PASS' if result.passed_gate else 'FAIL'}"
    )
    axes[0].text(
        0.01,
        0.95,
        text,
        transform=axes[0].transAxes,
        fontsize=10,
        verticalalignment="top",
        bbox={"boxstyle": "round", "facecolor": "white", "alpha": 0.8},
    )

    plt.tight_layout()
    fig.savefig(png_path, dpi=150)
    plt.close(fig)

    combined["date"] = combined["timestamp"].dt.date
    combined[["date", "fold", "equity", "drawdown_pct", "daily_return"]].to_csv(csv_path, index=False)

    logger.info("equity_report_saved", png=str(png_path), csv=str(csv_path))
    return str(png_path)
