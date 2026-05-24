"""Statistical significance testing for RAFund ML4T walk-forward results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd
from scipy.stats import ttest_1samp


@dataclass
class SignificanceResult:
    t_stat: float
    p_value: float
    significant: bool
    permutation_p_value: float
    mean_oos_return: float
    std_oos_return: float
    interpretation: str


def test_strategy_significance(
    fold_returns: List[pd.Series],
    n_permutations: int = 1000,
    alpha: float = 0.05,
) -> SignificanceResult:
    if not fold_returns:
        raise ValueError("fold_returns must contain at least one return series")

    all_returns = pd.concat(fold_returns, ignore_index=True).dropna()
    if all_returns.empty:
        raise ValueError("Combined return series is empty")

    mean_oos_return = float(all_returns.mean())
    std_oos_return = float(all_returns.std(ddof=1))
    t_stat, p_value = ttest_1samp(all_returns, 0.0, nan_policy="omit")

    actual_sharpe = mean_oos_return / std_oos_return * np.sqrt(252) if std_oos_return > 0 else 0.0
    permuted_sharpes = []
    for _ in range(n_permutations):
        permuted = all_returns.sample(frac=1.0, replace=False).reset_index(drop=True)
        perm_mean = permuted.mean()
        perm_std = permuted.std(ddof=1)
        permuted_sharpes.append(float(perm_mean / perm_std * np.sqrt(252)) if perm_std > 0 else 0.0)

    permuted_sharpes = np.array(permuted_sharpes)
    permutation_p_value = float(
        (np.sum(np.abs(permuted_sharpes) >= abs(actual_sharpe)) + 1) / (n_permutations + 1)
    )

    significant = (p_value < alpha) and (permutation_p_value < alpha)

    if significant:
        interpretation = f"Strategy returns are statistically significant (p={p_value:.3f}). Proceed to Phase 4."
    elif p_value < alpha:
        interpretation = "Marginal significance — permutation test failed. Consider more data."
    else:
        interpretation = "Returns are not distinguishable from noise. Do not proceed."

    return SignificanceResult(
        t_stat=float(t_stat),
        p_value=float(p_value),
        significant=significant,
        permutation_p_value=permutation_p_value,
        mean_oos_return=mean_oos_return,
        std_oos_return=std_oos_return,
        interpretation=interpretation,
    )
