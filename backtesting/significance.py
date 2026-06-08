"""Statistical significance testing for Raf3nd ML4T walk-forward results.

This module answers one question rigorously: *are the out-of-sample returns
distinguishable from luck?* It combines several complementary tests so a single
fragile assumption cannot wave a strategy through:

  * a one-sample t-test on the pooled OOS returns (parametric, IID),
  * a sign-flip permutation test on the realised Sharpe (non-parametric),
  * a **stationary bootstrap** (Politis & Romano 1994) that preserves
    autocorrelation — crypto OOS returns from carried positions are NOT IID,
  * a **Monte Carlo** null built from a zero-mean Normal with the sample's
    own volatility,
  * the **Probabilistic Sharpe Ratio** (Bailey & López de Prado) which accounts
    for sample length, skew and kurtosis, and
  * the **Deflated Sharpe Ratio** which additionally penalises the number of
    independent strategy configurations tried (multiple-testing / selection
    bias).

``significant`` keeps its original, backwards-compatible meaning. ``robust_significant``
is the stricter gate the promotion pipeline uses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd
from scipy.stats import kurtosis, norm, skew, ttest_1samp

from config.constants import PERIODS_PER_YEAR

_EULER_MASCHERONI = 0.5772156649015329


@dataclass
class SignificanceResult:
    t_stat: float
    p_value: float
    significant: bool
    permutation_p_value: float
    mean_oos_return: float
    std_oos_return: float
    interpretation: str
    # --- extended diagnostics (added for the quant-audit hardening) ---------
    stationary_bootstrap_p_value: float = 1.0
    monte_carlo_p_value: float = 1.0
    probabilistic_sharpe_ratio: float = 0.0
    deflated_sharpe_ratio: float = 0.0
    robust_significant: bool = False


def _per_period_sharpe(returns: np.ndarray) -> float:
    """Non-annualized (per-period) Sharpe of a return array."""
    if returns.size < 2:
        return 0.0
    std = float(np.std(returns, ddof=1))
    if std == 0.0:
        return 0.0
    return float(np.mean(returns) / std)


def _psr_denominator(returns: np.ndarray, sr_hat: float) -> float:
    """The ``1 - skew*SR + ((kurt-1)/4)*SR^2`` term shared by PSR and DSR.

    Returns ``nan`` when it is non-positive (degenerate), so callers can guard.
    """
    if returns.size < 3:
        return float("nan")
    g3 = float(skew(returns, bias=False))
    g4 = float(kurtosis(returns, fisher=False, bias=False))  # non-excess kurtosis
    denom = 1.0 - g3 * sr_hat + ((g4 - 1.0) / 4.0) * (sr_hat ** 2)
    if denom <= 0.0 or not math.isfinite(denom):
        return float("nan")
    return denom


def _probabilistic_sharpe_ratio(returns: np.ndarray, sr_benchmark: float = 0.0) -> float:
    """PSR: P(true per-period Sharpe > ``sr_benchmark``)."""
    n = returns.size
    if n < 3:
        return 0.0
    sr_hat = _per_period_sharpe(returns)
    denom = _psr_denominator(returns, sr_hat)
    if denom is None or not math.isfinite(denom):
        return 0.0
    z = (sr_hat - sr_benchmark) * math.sqrt(n - 1) / math.sqrt(denom)
    return float(norm.cdf(z))


def _deflated_sharpe_ratio(returns: np.ndarray, n_trials: int) -> float:
    """DSR: PSR against the *expected maximum* Sharpe under the null across
    ``n_trials`` independent configurations (Bailey & López de Prado 2014).

    When ``n_trials == 1`` the benchmark collapses to 0 and DSR == PSR.
    """
    n = returns.size
    if n < 3:
        return 0.0
    sr_hat = _per_period_sharpe(returns)
    denom = _psr_denominator(returns, sr_hat)
    if denom is None or not math.isfinite(denom):
        return 0.0

    n_trials = max(1, int(n_trials))
    if n_trials == 1:
        sr0 = 0.0
    else:
        # Variance of the *estimated* Sharpe (same denominator as PSR).
        var_sr = denom / (n - 1)
        sigma_sr = math.sqrt(var_sr)
        # Expected maximum of n_trials standard normals (Gumbel approximation).
        z1 = norm.ppf(1.0 - 1.0 / n_trials)
        z2 = norm.ppf(1.0 - 1.0 / (n_trials * math.e))
        sr0 = sigma_sr * ((1.0 - _EULER_MASCHERONI) * z1 + _EULER_MASCHERONI * z2)

    z = (sr_hat - sr0) * math.sqrt(n - 1) / math.sqrt(denom)
    return float(norm.cdf(z))


def _stationary_bootstrap_p_value(
    returns: np.ndarray, n_bootstrap: int, block_size: int, rng: np.random.Generator
) -> float:
    """One-sided p-value for H0: mean OOS return <= 0 via the stationary
    bootstrap (geometric block lengths preserve autocorrelation)."""
    n = returns.size
    if n < 2:
        return 1.0
    p = 1.0 / max(1, block_size)  # geometric restart probability
    # Vectorised across the bootstrap dimension: loop only over the n time
    # steps, advancing all n_bootstrap resample paths at once. Each step either
    # continues the current block (prev+1) or restarts at a fresh random index.
    idx = np.empty((n_bootstrap, n), dtype=np.int64)
    idx[:, 0] = rng.integers(0, n, size=n_bootstrap)
    for t in range(1, n):
        restart = rng.random(n_bootstrap) < p
        cont = (idx[:, t - 1] + 1) % n
        fresh = rng.integers(0, n, size=n_bootstrap)
        idx[:, t] = np.where(restart, fresh, cont)
    means = returns[idx].mean(axis=1)
    # Centre the bootstrap distribution at 0 (the null) and ask how often a
    # null resample mean reaches the observed mean.
    observed = returns.mean()
    centered = means - means.mean()
    return float((np.sum(centered >= observed) + 1) / (n_bootstrap + 1))


def _monte_carlo_p_value(
    returns: np.ndarray, n_montecarlo: int, rng: np.random.Generator
) -> float:
    """Monte Carlo null: zero-mean Normal paths with the sample's own std.
    p = fraction of synthetic Sharpes >= the actual Sharpe."""
    n = returns.size
    std = float(np.std(returns, ddof=1)) if n >= 2 else 0.0
    if n < 2 or std == 0.0:
        return 1.0
    actual_sharpe = _per_period_sharpe(returns)
    synthetic = rng.normal(0.0, std, size=(n_montecarlo, n))
    syn_mean = synthetic.mean(axis=1)
    syn_std = synthetic.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        syn_sharpe = np.where(syn_std > 0, syn_mean / syn_std, 0.0)
    return float((np.sum(syn_sharpe >= actual_sharpe) + 1) / (n_montecarlo + 1))


def test_strategy_significance(
    fold_returns: List[pd.Series],
    n_permutations: int = 1000,
    alpha: float = 0.05,
    seed: Optional[int] = None,
    n_bootstrap: int = 1000,
    block_size: int = 10,
    n_montecarlo: int = 1000,
    n_trials: int = 1,
) -> SignificanceResult:
    if not fold_returns:
        raise ValueError("fold_returns must contain at least one return series")

    all_returns = pd.concat(fold_returns, ignore_index=True).dropna()
    if all_returns.empty:
        raise ValueError("Combined return series is empty")

    mean_oos_return = float(all_returns.mean())
    std_oos_return = float(all_returns.std(ddof=1))
    t_stat, p_value = ttest_1samp(all_returns, 0.0, nan_policy="omit")

    returns_arr = all_returns.to_numpy(dtype=float)
    n = returns_arr.size
    actual_sharpe = (
        mean_oos_return / std_oos_return * np.sqrt(PERIODS_PER_YEAR)
        if std_oos_return > 0
        else 0.0
    )

    rng = np.random.default_rng(seed)

    # Sign-flip permutation test for a positive edge. Under the null of returns
    # symmetric about zero, randomly flipping each return's sign gives a proper
    # reference distribution for the realised Sharpe.
    if n < 2 or std_oos_return == 0.0:
        permutation_p_value = 1.0
    else:
        signs = rng.choice((-1.0, 1.0), size=(n_permutations, n))
        permuted = returns_arr * signs
        perm_mean = permuted.mean(axis=1)
        perm_std = permuted.std(axis=1, ddof=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            permuted_sharpes = np.where(
                perm_std > 0, perm_mean / perm_std * np.sqrt(PERIODS_PER_YEAR), 0.0
            )
        permutation_p_value = float(
            (np.sum(permuted_sharpes >= actual_sharpe) + 1) / (n_permutations + 1)
        )

    # Autocorrelation-robust and selection-bias-aware diagnostics.
    stationary_bootstrap_p_value = _stationary_bootstrap_p_value(
        returns_arr, n_bootstrap, block_size, rng
    )
    monte_carlo_p_value = _monte_carlo_p_value(returns_arr, n_montecarlo, rng)
    probabilistic_sharpe_ratio = _probabilistic_sharpe_ratio(returns_arr)
    deflated_sharpe_ratio = _deflated_sharpe_ratio(returns_arr, n_trials)

    significant = bool(
        (p_value < alpha) and (permutation_p_value < alpha) and (mean_oos_return > 0)
    )
    robust_significant = bool(
        significant
        and (stationary_bootstrap_p_value < alpha)
        and (deflated_sharpe_ratio > 0.95)
    )

    if robust_significant:
        interpretation = (
            f"Robustly significant (p={p_value:.3f}, bootstrap={stationary_bootstrap_p_value:.3f}, "
            f"DSR={deflated_sharpe_ratio:.3f}). Edge survives autocorrelation and multiple-testing checks."
        )
    elif significant:
        interpretation = (
            f"Significant under IID tests (p={p_value:.3f}) but NOT robust "
            f"(bootstrap={stationary_bootstrap_p_value:.3f}, DSR={deflated_sharpe_ratio:.3f}). "
            "Treat with caution."
        )
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
        stationary_bootstrap_p_value=stationary_bootstrap_p_value,
        monte_carlo_p_value=monte_carlo_p_value,
        probabilistic_sharpe_ratio=probabilistic_sharpe_ratio,
        deflated_sharpe_ratio=deflated_sharpe_ratio,
        robust_significant=robust_significant,
    )
