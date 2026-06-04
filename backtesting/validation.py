"""Single-entry walk-forward validation for RAFund ML4T.

This module ties the individual backtesting building blocks together behind one
callable, :func:`run_validation`:

    splitter (folds) -> walk-forward OOS engine runs -> significance test
    -> optional equity report

It does not replace :class:`~backtesting.walk_forward.WalkForwardRunner` (which
``models.validator`` depends on); it composes it. The result aggregates the
walk-forward gate, the statistical-significance verdict, and the path to any
generated report into a single :class:`ValidationResult`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pandas as pd
import structlog

from backtesting.significance import SignificanceResult, test_strategy_significance
from backtesting.splitter import TimeSeriesSplitter
from backtesting.walk_forward import WalkForwardResult, WalkForwardRunner

logger = structlog.get_logger(__name__)

DATE_COL = "timestamp"


@dataclass
class ValidationResult:
    """Combined output of a full walk-forward validation run."""

    walk_forward: WalkForwardResult
    significance: Optional[SignificanceResult]
    report_path: Optional[str]
    n_folds: int

    @property
    def passed_gate(self) -> bool:
        return self.walk_forward.passed_gate

    @property
    def gate_reason(self) -> str:
        return self.walk_forward.gate_reason

    @property
    def aggregate(self):  # -> AggregateMetrics
        return self.walk_forward.aggregate

    @property
    def folds(self):  # -> List[FoldResult]
        return self.walk_forward.folds

    @property
    def is_significant(self) -> bool:
        """True only if the gate passed and returns are statistically significant."""
        return self.passed_gate and bool(self.significance and self.significance.significant)

    def summary(self) -> Dict[str, Any]:
        """A flat, log/JSON-friendly snapshot of the validation outcome."""
        agg = self.walk_forward.aggregate
        result: Dict[str, Any] = {
            "n_folds": self.n_folds,
            "passed_gate": self.passed_gate,
            "gate_reason": self.gate_reason,
            "mean_oos_sharpe": agg.mean_oos_sharpe,
            "median_oos_sharpe": agg.median_oos_sharpe,
            "worst_oos_drawdown": agg.worst_oos_drawdown,
            "pct_folds_profitable": agg.pct_folds_profitable,
            "pct_folds_account_failed": agg.pct_folds_account_failed,
            "total_oos_trades": agg.total_oos_trades,
            "consistency_score": agg.consistency_score,
            "report_path": self.report_path,
        }
        if self.significance is not None:
            result.update(
                {
                    "significant": self.significance.significant,
                    "t_stat": self.significance.t_stat,
                    "p_value": self.significance.p_value,
                    "permutation_p_value": self.significance.permutation_p_value,
                    "significance_interpretation": self.significance.interpretation,
                }
            )
        else:
            result["significant"] = None
        result["is_significant"] = self.is_significant
        return result


def _fold_return_series(walk_forward: WalkForwardResult) -> List[pd.Series]:
    """Extract one OOS daily-return series per fold for the significance test."""
    series: List[pd.Series] = []
    for fold in walk_forward.folds:
        if not fold.equity_curve:
            continue
        returns = pd.Series(
            [row.get("daily_return", 0.0) for row in fold.equity_curve],
            dtype="float64",
        ).dropna()
        if not returns.empty:
            series.append(returns)
    return series


def run_validation(
    prices_df: pd.DataFrame,
    strategy: Any,
    *,
    splitter: Optional[TimeSeriesSplitter] = None,
    train_months: int = 12,
    test_months: int = 3,
    step_months: int = 1,
    min_train_months: int = 6,
    engine_config: Optional[Dict[str, Any]] = None,
    run_significance: bool = True,
    n_permutations: int = 1000,
    alpha: float = 0.05,
    significance_seed: Optional[int] = None,
    generate_report: bool = False,
    strategy_name: str = "strategy",
    output_dir: str = "results/",
) -> ValidationResult:
    """Run an end-to-end walk-forward validation of ``strategy`` on ``prices_df``.

    The strategy must expose ``generate_signals(test_df) -> pd.DataFrame`` and may
    optionally expose ``fit(train_df)`` or ``prepare(train_df)`` for per-fold
    training (see :class:`~backtesting.walk_forward.WalkForwardRunner`).

    Args:
        prices_df: Aligned price/feature frame containing a ``timestamp`` column.
        strategy: Object implementing the walk-forward strategy contract above.
        splitter: Pre-built splitter; if omitted one is built from the
            ``*_months`` arguments.
        train_months, test_months, step_months, min_train_months: Used only when
            ``splitter`` is not supplied.
        engine_config: Keyword overrides for :class:`BacktestEngine`.
        run_significance: When True (default) run the significance test on the
            pooled OOS returns. Skipped (result ``None``) if no returns exist.
        n_permutations, alpha, significance_seed: Significance-test parameters.
        generate_report: When True, render the equity report to ``output_dir``.
        strategy_name, output_dir: Report naming/location.

    Returns:
        A :class:`ValidationResult` bundling the walk-forward result, the
        significance verdict (or ``None``), and the report path (or ``None``).

    Raises:
        TypeError: ``prices_df`` is not a DataFrame.
        ValueError: ``prices_df`` is empty, lacks a ``timestamp`` column, or the
            splitter produces no folds.
    """
    if not isinstance(prices_df, pd.DataFrame):
        raise TypeError("prices_df must be a pandas DataFrame")
    if prices_df.empty:
        raise ValueError("prices_df is empty")
    if DATE_COL not in prices_df.columns:
        raise ValueError(f"prices_df must contain a '{DATE_COL}' column")

    if splitter is None:
        splitter = TimeSeriesSplitter(
            train_months=train_months,
            test_months=test_months,
            step_months=step_months,
            min_train_months=min_train_months,
        )

    runner = WalkForwardRunner(strategy, splitter, engine_config)
    walk_forward = runner.run(prices_df)

    significance: Optional[SignificanceResult] = None
    if run_significance:
        fold_returns = _fold_return_series(walk_forward)
        if fold_returns:
            significance = test_strategy_significance(
                fold_returns,
                n_permutations=n_permutations,
                alpha=alpha,
                seed=significance_seed,
            )
        else:
            logger.warning("validation_significance_skipped", reason="no OOS returns")

    report_path: Optional[str] = None
    if generate_report:
        # Imported lazily so validation does not hard-depend on matplotlib.
        from backtesting.reporting import generate_equity_report

        report_path = generate_equity_report(walk_forward, strategy_name, output_dir) or None

    result = ValidationResult(
        walk_forward=walk_forward,
        significance=significance,
        report_path=report_path,
        n_folds=len(walk_forward.folds),
    )

    logger.info("validation_complete", **result.summary())
    return result
