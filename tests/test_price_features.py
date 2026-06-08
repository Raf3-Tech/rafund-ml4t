"""
Unit tests for features.price_features.calculate_spread_features.

Focus: the z-score divide-by-zero guard (BUG #2). When the rolling
spread_std is 0 (flat/constant spread over the window) the z-score must be
0.0 rather than inf/NaN, while warm-up rows keep their NaN per convention.
"""

import numpy as np
import pandas as pd

from features.price_features import calculate_spread_features


def _make_prices(values):
    """Build a price Series with a simple integer index."""
    return pd.Series(values, dtype=float)


def test_normal_variation_produces_finite_zscores():
    """A spread with real variation yields finite z-scores with ~0 mean
    over the window and the expected sign behavior."""
    window = 20
    n = 100
    rng = np.random.default_rng(42)

    # Asset A oscillates around asset B so the spread varies but stays
    # mean-reverting (non-degenerate rolling std).
    t = np.arange(n)
    prices_b = 100.0 + np.cumsum(rng.normal(0, 0.1, n))
    prices_a = prices_b + np.sin(t / 5.0) + rng.normal(0, 0.05, n)

    df = calculate_spread_features(_make_prices(prices_a),
                                   _make_prices(prices_b),
                                   window=window)

    populated = df['z_score'].iloc[window:]

    # No inf or NaN on the populated (post warm-up) rows.
    assert np.isfinite(populated).all()

    # Z-score is a deviation-from-rolling-mean measure: it should center
    # near zero on average over the populated rows.
    assert abs(populated.mean()) < 1.0

    # Sign behavior: where spread is above its rolling mean the z-score is
    # positive, and vice versa.
    above = df['spread'].iloc[window:] > df['spread_mean'].iloc[window:]
    pos = populated[above]
    neg = populated[~above]
    assert (pos > 0).all()
    assert (neg <= 0).all()


def test_zero_std_window_yields_zero_not_inf():
    """BUG #2: when the rolling spread_std is 0 over some window the z_score
    column must contain NO inf and the affected rows must be 0.0 (not NaN)."""
    window = 20
    n = 60

    # Construct prices so that the normalized spread is constant over a
    # stretch -> rolling std == 0 there.
    # norm_a = price_a / price_a[0], norm_b = price_b / price_b[0].
    # If both assets grow by the same multiplicative factor each step, the
    # normalized series move together and the spread stays constant (0).
    prices_a = _make_prices([100.0 * (1.01 ** i) for i in range(n)])
    prices_b = _make_prices([50.0 * (1.01 ** i) for i in range(n)])

    df = calculate_spread_features(prices_a, prices_b, window=window)

    # There must be windows where std is exactly 0.
    zero_std_rows = df['spread_std'] == 0
    assert zero_std_rows.any(), "test setup failed to produce zero-std windows"

    # No inf anywhere in z_score.
    assert not np.isinf(df['z_score']).any()

    # Where std == 0, z_score is exactly 0.0 (finite, no NaN).
    z_at_zero = df.loc[zero_std_rows, 'z_score']
    assert np.isfinite(z_at_zero).all()
    assert (z_at_zero == 0.0).all()

    # All populated (post warm-up) rows are finite.
    populated = df['z_score'].iloc[window:]
    assert np.isfinite(populated).all()


def test_all_constant_input_no_inf_or_unexpected_nan():
    """Edge case: identical constant price series. Spread is 0 everywhere so
    every full window has std == 0; previously this produced all-NaN/inf."""
    window = 20
    n = 50
    prices_a = _make_prices([42.0] * n)
    prices_b = _make_prices([42.0] * n)

    df = calculate_spread_features(prices_a, prices_b, window=window)

    # No inf at all.
    assert not np.isinf(df['z_score']).any()

    # Post warm-up rows are finite and equal to 0.0.
    populated = df['z_score'].iloc[window:]
    assert np.isfinite(populated).all()
    assert (populated == 0.0).all()
