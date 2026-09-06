"""Tests for monitoring/overfitting.py — the leaderboard overfitting
correction (Phase 6 of the Kraken Prop risk gate).
"""
from __future__ import annotations

import pytest

from monitoring.overfitting import (
    _norm_cdf,
    _norm_ppf,
    deflated_sharpe_ratio,
    expected_max_sharpe_under_null,
    sharpe_standard_error,
    walk_forward_oos_within_band,
)


def test_norm_ppf_matches_known_reference_values():
    assert _norm_ppf(0.975) == pytest.approx(1.959964, abs=1e-5)
    assert _norm_ppf(0.5) == pytest.approx(0.0, abs=1e-9)


def test_norm_cdf_is_inverse_of_norm_ppf():
    for p in (0.01, 0.25, 0.5, 0.75, 0.99):
        assert _norm_cdf(_norm_ppf(p)) == pytest.approx(p, abs=1e-6)


def test_sharpe_standard_error_requires_at_least_2_observations():
    assert sharpe_standard_error(1.0, n_obs=1) is None
    assert sharpe_standard_error(1.0, n_obs=0) is None
    assert sharpe_standard_error(1.0, n_obs=10) is not None


def test_expected_max_sharpe_under_null_is_zero_with_one_trial():
    assert expected_max_sharpe_under_null([0.5], n_trials=1) == 0.0


def test_expected_max_sharpe_under_null_grows_with_more_trials():
    # More trials searched -> higher expected max Sharpe from pure noise
    # alone, for the same cross-sectional spread.
    trial_sharpes = [0.1, 0.3, -0.2, 0.5, 0.0, 0.4, -0.1, 0.2]
    sr0_few = expected_max_sharpe_under_null(trial_sharpes, n_trials=8)
    sr0_many = expected_max_sharpe_under_null(trial_sharpes, n_trials=800)
    assert sr0_many > sr0_few


def test_deflated_sharpe_ratio_penalizes_more_trials_searched():
    """The brief's central point: the same observed Sharpe is less credible
    the more trials were searched to find it."""
    trial_sharpes = [0.2] * 50 + [1.5]  # one standout among many mediocre trials
    dsr_few_trials = deflated_sharpe_ratio(1.5, n_windows=100, trial_sharpes=trial_sharpes, n_trials=5)
    dsr_many_trials = deflated_sharpe_ratio(1.5, n_windows=100, trial_sharpes=trial_sharpes, n_trials=5000)
    assert dsr_many_trials < dsr_few_trials


def test_deflated_sharpe_ratio_none_with_insufficient_windows():
    assert deflated_sharpe_ratio(1.5, n_windows=1, trial_sharpes=[1.5, 0.2], n_trials=2) is None


def test_walk_forward_oos_none_with_fewer_than_4_windows():
    assert walk_forward_oos_within_band([1.0, 1.1, 0.9]) is None


def test_walk_forward_oos_passes_when_stable_across_halves():
    window_sharpes = [1.0, 1.1, 0.9, 1.05, 0.95, 1.02]
    assert walk_forward_oos_within_band(window_sharpes) is True


def test_walk_forward_oos_fails_when_performance_collapses_out_of_sample():
    # First half looks great (mean ~2.0, tight); second half is much worse —
    # classic in-sample-overfit signature.
    window_sharpes = [2.0, 2.1, 1.9, 2.05, -0.5, -0.8, -0.3, -0.6]
    assert walk_forward_oos_within_band(window_sharpes) is False


def test_walk_forward_oos_uses_chronological_order_not_sorted_order():
    # Same multiset of values, different time order -> different verdict,
    # proving the split is chronological (by position), not by magnitude.
    stable_order = [1.0, 1.1, 0.9, 1.0, 1.05, 0.95, 1.02, 0.98]
    collapsing_order = [2.0, 2.1, 1.9, 2.05, -0.9, -0.8, -0.85, -0.95]
    assert walk_forward_oos_within_band(stable_order) is True
    assert walk_forward_oos_within_band(collapsing_order) is False
