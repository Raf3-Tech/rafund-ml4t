"""Tests for monitoring/overfitting.py — the leaderboard overfitting
correction (Phase 6 of the Kraken Prop risk gate).
"""
from __future__ import annotations

import pytest

from monitoring.overfitting import (
    DeflatedSharpeResult,
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
    assert dsr_many_trials.probability < dsr_few_trials.probability


def test_deflated_sharpe_ratio_none_with_insufficient_windows():
    assert deflated_sharpe_ratio(1.5, n_windows=1, trial_sharpes=[1.5, 0.2], n_trials=2) is None


def test_deflated_sharpe_ratio_returns_full_diagnostic_trail():
    result = deflated_sharpe_ratio(1.5, n_windows=100, trial_sharpes=[0.2] * 50 + [1.5], n_trials=51)
    assert isinstance(result, DeflatedSharpeResult)
    assert result.observed_sharpe == 1.5
    assert result.n_trials == 51
    assert result.n_observations == 100
    assert result.skew == 0.0
    assert result.kurtosis == 3.0
    assert isinstance(result.z_score, float)
    assert isinstance(result.expected_max_sharpe_null, float)
    # z_score is exactly what was fed to the CDF, unclamped and signed.
    assert result.probability == pytest.approx(_norm_cdf(result.z_score))


def test_deflated_sharpe_ratio_underflow_flag_distinguishes_zero_from_saturation():
    # math.erf saturates to exactly -1.0 (so the CDF hits exactly 0.0) only
    # once z drops below roughly -8.3 in float64 — verified separately
    # against math.erf directly, not assumed. A few high-Sharpe outliers
    # among many mediocre trials push sigma(SR), and so z, that far out.
    trial_sharpes = [0.1] * 300 + [3.0] * 5
    saturated = deflated_sharpe_ratio(0.05, n_windows=500, trial_sharpes=trial_sharpes, n_trials=305)
    assert saturated.probability == 0.0
    assert saturated.underflowed is True
    assert saturated.z_score < -8.3

    # The real production leaderboard's own worst case (see AGENTS.md's
    # OVERFITTING GATE RULE, z as low as -5.68) rounds to "0.0000" for
    # display but is nowhere near this genuine saturation — underflowed
    # must be False, and the small-but-nonzero probability must survive.
    not_saturated = deflated_sharpe_ratio(0.086, n_windows=26, trial_sharpes=[0.09, 0.09, 0.34, 0.086], n_trials=305)
    assert not_saturated.probability > 0.0
    assert not_saturated.underflowed is False


def test_deflated_sharpe_ratio_never_clamps_or_substitutes_epsilon_for_probability():
    """0.0 must reach the caller exactly as 0.0 — no epsilon floor."""
    trial_sharpes = [0.1] * 300 + [3.0] * 5
    result = deflated_sharpe_ratio(0.05, n_windows=500, trial_sharpes=trial_sharpes, n_trials=305)
    assert result.probability == 0.0  # exact, not approx — proves no epsilon substitution
    expected_se = sharpe_standard_error(0.05, 500)
    assert result.z_score == pytest.approx((0.05 - result.expected_max_sharpe_null) / expected_se)


# ── Task 2 controls: positive, negative, and monotonicity ───────────────────

def test_positive_control_high_genuine_sharpe_small_trial_count_gives_dsr_near_1():
    """The test that proves the function isn't hardcoded to zero: a
    genuinely strong, long-lived track record among very few competing
    trials must score close to 1.0."""
    trial_sharpes = [0.3, 0.4, 2.5]  # the candidate is the "2.5" entry, only 3 trials total
    result = deflated_sharpe_ratio(2.5, n_windows=500, trial_sharpes=trial_sharpes, n_trials=3)
    assert result is not None
    assert result.probability > 0.99
    assert result.underflowed is False


def test_negative_control_noise_level_sharpe_large_trial_count_gives_dsr_near_0():
    """Sharpe consistent with pure noise, searched across as many trials as
    the real production leaderboard (305) — must land at or near 0.0. A few
    standout trials among many mediocre ones (like the real leaderboard's
    mix of strategies) push the expected max under the null well above this
    candidate's own middling Sharpe."""
    trial_sharpes = [0.1] * 300 + [1.0] * 5
    result = deflated_sharpe_ratio(0.1, n_windows=200, trial_sharpes=trial_sharpes, n_trials=305)
    assert result is not None
    assert result.probability < 0.01
    assert result.z_score < -3


def test_monotonic_non_increasing_in_n_trials_holding_track_record_fixed():
    trial_sharpes = [0.1, 0.3, -0.2, 0.5, 0.0, 0.4, -0.1, 0.2]  # fixed sigma(SR) source
    probs = [
        deflated_sharpe_ratio(1.0, n_windows=100, trial_sharpes=trial_sharpes, n_trials=n).probability
        for n in (2, 10, 100, 1000, 10000)
    ]
    assert all(probs[i] >= probs[i + 1] for i in range(len(probs) - 1))


def test_monotonic_non_decreasing_in_observed_sharpe_holding_n_fixed():
    trial_sharpes = [0.1, 0.2, 0.3, 0.15, 0.25]
    probs = [
        deflated_sharpe_ratio(sr, n_windows=100, trial_sharpes=trial_sharpes, n_trials=305).probability
        for sr in (-0.5, 0.0, 0.5, 1.0, 1.5, 2.0)
    ]
    assert all(probs[i] <= probs[i + 1] for i in range(len(probs) - 1))


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
