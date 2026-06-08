"""Tests for the audit-hardened significance diagnostics: stationary bootstrap,
Monte Carlo, Probabilistic and Deflated Sharpe Ratios, and the robust gate."""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtesting.significance import test_strategy_significance as run_significance


def _edge(n=400, mean=0.002, std=0.001, seed=0):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mean, std, n))


def _noise(n=400, std=0.02, seed=1):
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0.0, std, n))


def test_strong_edge_is_robustly_significant():
    res = run_significance(
        [_edge()], n_permutations=400, seed=42, n_bootstrap=400, n_montecarlo=400
    )
    assert res.stationary_bootstrap_p_value < 0.05
    assert res.monte_carlo_p_value < 0.05
    assert res.probabilistic_sharpe_ratio > 0.95
    assert res.deflated_sharpe_ratio > 0.95
    assert res.robust_significant is True


def test_pure_noise_is_not_robust():
    res = run_significance(
        [_noise()], n_permutations=400, seed=42, n_bootstrap=400, n_montecarlo=400
    )
    assert res.significant is False
    assert res.robust_significant is False


def test_bootstrap_and_montecarlo_are_reproducible_with_seed():
    a = run_significance([_edge()], n_permutations=200, seed=7, n_bootstrap=300, n_montecarlo=300)
    b = run_significance([_edge()], n_permutations=200, seed=7, n_bootstrap=300, n_montecarlo=300)
    assert a.stationary_bootstrap_p_value == b.stationary_bootstrap_p_value
    assert a.monte_carlo_p_value == b.monte_carlo_p_value
    assert a.deflated_sharpe_ratio == b.deflated_sharpe_ratio


def test_multiple_trials_penalise_deflated_sharpe():
    # A marginal edge: many trials should not raise (and generally lowers) the
    # deflated Sharpe versus a single trial.
    marginal = _edge(n=250, mean=0.0006, std=0.01, seed=3)
    one = run_significance([marginal], n_permutations=200, seed=5, n_bootstrap=300, n_montecarlo=300, n_trials=1)
    many = run_significance([marginal], n_permutations=200, seed=5, n_bootstrap=300, n_montecarlo=300, n_trials=100)
    assert many.deflated_sharpe_ratio <= one.deflated_sharpe_ratio


def test_backward_compatible_fields_present():
    res = run_significance([_edge()], n_permutations=200, seed=1, n_bootstrap=200, n_montecarlo=200)
    # original contract preserved
    assert isinstance(res.p_value, float)
    assert isinstance(res.permutation_p_value, float)
    assert isinstance(res.significant, bool)
    assert 0.0 <= res.permutation_p_value <= 1.0
