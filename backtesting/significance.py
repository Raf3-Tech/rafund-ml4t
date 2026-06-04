"""Statistical significance testing for RAFund ML4T walk-forward results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

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
    seed: Optional[int] = None,
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

    # Sign-flip permutation test for a positive edge. Reordering the returns
    # leaves the mean and std unchanged, so the previous order-shuffle was a
    # no-op (every permuted Sharpe equalled the actual one). Under the null of
    # returns symmetric about zero, randomly flipping each return's sign gives a
    # proper reference distribution for the realised Sharpe.
    returns_arr = all_returns.to_numpy(dtype=float)
    n = returns_arr.size
    rng = np.random.default_rng(seed)
    if n < 2 or std_oos_return == 0.0:
        permutation_p_value = 1.0
    else:
        signs = rng.choice((-1.0, 1.0), size=(n_permutations, n))
        permuted = returns_arr * signs
        perm_mean = permuted.mean(axis=1)
        perm_std = permuted.std(axis=1, ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            permuted_sharpes = np.where(perm_std > 0, perm_mean / perm_std * np.sqrt(252), 0.0)
        permutation_p_value = float(
            (np.sum(permuted_sharpes >= actual_sharpe) + 1) / (n_permutations + 1)
        )

    significant = bool((p_value < alpha) and (permutation_p_value < alpha) and (mean_oos_return > 0))

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
