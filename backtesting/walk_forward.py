"""Walk-forward validation runner for RAFund ML4T.

This module executes the backtest engine over a series of train/test folds and
aggregates out-of-sample performance metrics for validation gate reporting.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any, Dict, List, Optional

import pandas as pd
import structlog

from backtesting.engine import BacktestEngine
from backtesting.splitter import TimeSeriesSplitter

logger = structlog.get_logger(__name__)


@dataclass
class FoldResult:
    fold: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    oos_sharpe: float
    oos_max_drawdown: float
    oos_total_return: float
    oos_win_rate: float
    oos_trade_count: int
    prop_firm_state: Any
    account_failed: bool
    equity_curve: List[Dict[str, object]]


@dataclass
class AggregateMetrics:
    mean_oos_sharpe: float
    median_oos_sharpe: float
    mean_oos_max_drawdown: float
    worst_oos_drawdown: float
    pct_folds_profitable: float
    pct_folds_account_failed: float
    total_oos_trades: int
    consistency_score: float


@dataclass
class WalkForwardResult:
    folds: List[FoldResult]
    aggregate: AggregateMetrics
    passed_gate: bool
    gate_reason: str


class WalkForwardRunner:
    def __init__(
        self,
        strategy: Any,
        splitter: TimeSeriesSplitter,
        engine_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.strategy = strategy
        self.splitter = splitter
        self.engine_config = engine_config or {}
        self.engine = BacktestEngine(**self.engine_config)

    def run(self, df: pd.DataFrame) -> WalkForwardResult:
        folds = self.splitter.split(df)
        if not folds:
            raise ValueError("No walk-forward folds generated from input data")

        fold_results: List[FoldResult] = []
        for fold in folds:
            train_df = fold["train_df"].copy()
            test_df = fold["test_df"].copy()

            if hasattr(self.strategy, "fit"):
                self.strategy.fit(train_df)
            elif hasattr(self.strategy, "prepare"):
                self.strategy.prepare(train_df)

            signals = self.strategy.generate_signals(test_df)
            results = self.engine.run(test_df, signals)

            fold_result = FoldResult(
                fold=fold["fold"],
                train_start=fold["train_start"].date(),
                train_end=fold["train_end"].date(),
                test_start=fold["test_start"].date(),
                test_end=fold["test_end"].date(),
                oos_sharpe=results.get("sharpe_ratio", 0.0),
                oos_max_drawdown=results.get("max_drawdown_pct", 0.0),
                oos_total_return=results.get("total_return_pct", 0.0),
                oos_win_rate=results.get("win_rate", 0.0),
                oos_trade_count=results.get("num_trades", 0),
                prop_firm_state=results.get("prop_firm_state"),
                account_failed=getattr(results.get("prop_firm_state"), "account_failed", False),
                equity_curve=results.get("equity_curve", []),
            )
            fold_results.append(fold_result)

            logger.info(
                "walk_forward_fold_result",
                fold=fold_result.fold,
                test_start=str(fold_result.test_start),
                test_end=str(fold_result.test_end),
                oos_sharpe=fold_result.oos_sharpe,
                oos_max_drawdown=fold_result.oos_max_drawdown,
                oos_total_return=fold_result.oos_total_return,
                oos_trade_count=fold_result.oos_trade_count,
                account_failed=fold_result.account_failed,
            )

        aggregate = self._aggregate_metrics(fold_results)
        passed_gate, gate_reason = self._evaluate_gate(aggregate)

        logger.info(
            "walk_forward_summary",
            mean_oos_sharpe=aggregate.mean_oos_sharpe,
            worst_oos_drawdown=aggregate.worst_oos_drawdown,
            pct_folds_profitable=aggregate.pct_folds_profitable,
            pct_folds_account_failed=aggregate.pct_folds_account_failed,
            passed_gate=passed_gate,
            gate_reason=gate_reason,
        )

        return WalkForwardResult(
            folds=fold_results,
            aggregate=aggregate,
            passed_gate=passed_gate,
            gate_reason=gate_reason,
        )

    def _aggregate_metrics(self, fold_results: List[FoldResult]) -> AggregateMetrics:
        sharpe_values = [f.oos_sharpe for f in fold_results]
        drawdowns = [f.oos_max_drawdown for f in fold_results]
        profitable = [1 for f in fold_results if f.oos_total_return > 0]
        account_failed = [1 for f in fold_results if f.account_failed]

        return AggregateMetrics(
            mean_oos_sharpe=float(sum(sharpe_values) / len(sharpe_values)) if sharpe_values else 0.0,
            median_oos_sharpe=float(median(sharpe_values)) if sharpe_values else 0.0,
            mean_oos_max_drawdown=float(sum(drawdowns) / len(drawdowns)) if drawdowns else 0.0,
            worst_oos_drawdown=float(max(drawdowns)) if drawdowns else 0.0,
            pct_folds_profitable=float(sum(profitable) / len(fold_results) * 100) if fold_results else 0.0,
            pct_folds_account_failed=float(sum(account_failed) / len(fold_results) * 100) if fold_results else 0.0,
            total_oos_trades=sum(f.oos_trade_count for f in fold_results),
            consistency_score=float(sum(1 for f in fold_results if f.oos_sharpe > 0) / len(fold_results) * 100) if fold_results else 0.0,
        )

    def _evaluate_gate(self, aggregate: AggregateMetrics) -> tuple[bool, str]:
        if aggregate.mean_oos_sharpe >= 1.0 and aggregate.worst_oos_drawdown <= 6.0:
            return True, "PASS: mean OOS Sharpe >= 1.0 and worst OOS drawdown <= 6%"

        reasons: List[str] = []
        if aggregate.mean_oos_sharpe < 1.0:
            reasons.append(f"mean OOS Sharpe {aggregate.mean_oos_sharpe:.2f} < 1.0")
        if aggregate.worst_oos_drawdown > 6.0:
            reasons.append(f"worst OOS drawdown {aggregate.worst_oos_drawdown:.2f}% > 6%")
        return False, "FAIL: " + "; ".join(reasons)
