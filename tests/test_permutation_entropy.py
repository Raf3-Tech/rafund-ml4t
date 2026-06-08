"""Tests for the permutation-entropy feature module (Phase 1, alpha roadmap)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.permutation_entropy import (
    entropy_features,
    permutation_entropy,
    tradeable_mask,
)


def test_monotonic_series_has_zero_entropy():
    # A strictly increasing series has a single ordinal pattern -> PE == 0.
    s = pd.Series(np.arange(200, dtype=float))
    pe = permutation_entropy(s, window=30, embedding_dimension=3)
    established = pe.dropna()
    assert len(established) > 0
    assert np.allclose(established.to_numpy(), 0.0, atol=1e-9)


def test_random_series_has_high_entropy():
    rng = np.random.default_rng(0)
    s = pd.Series(rng.normal(size=500))
    pe = permutation_entropy(s, window=50, embedding_dimension=3)
    # Pure noise should sit near the maximum (1.0); allow generous slack.
    assert pe.dropna().mean() > 0.85


def test_entropy_is_bounded_unit_interval():
    rng = np.random.default_rng(1)
    s = pd.Series(rng.normal(size=300).cumsum())
    pe = permutation_entropy(s, window=40, embedding_dimension=4).dropna()
    assert (pe >= 0.0).all() and (pe <= 1.0 + 1e-9).all()


def test_strictly_causal_no_lookahead():
    """PE at row t must not change when future rows are appended."""
    rng = np.random.default_rng(2)
    base = rng.normal(size=200)
    s_short = pd.Series(base)
    s_long = pd.Series(np.concatenate([base, rng.normal(size=100)]))

    pe_short = permutation_entropy(s_short, window=30, embedding_dimension=3)
    pe_long = permutation_entropy(s_long, window=30, embedding_dimension=3)

    # Overlapping region must be identical to floating-point precision.
    np.testing.assert_allclose(
        pe_short.to_numpy(), pe_long.iloc[: len(s_short)].to_numpy(), equal_nan=True
    )


def test_warmup_rows_are_nan_and_not_tradeable():
    s = pd.Series(np.arange(100, dtype=float))
    feats = entropy_features(s, window=30, embedding_dimension=3, threshold=0.85)
    # First window-1 rows have no full window -> PE NaN, never tradeable.
    assert feats["perm_entropy"].iloc[:29].isna().all()
    assert (~feats["is_tradeable"].iloc[:29]).all()


def test_nan_inside_window_propagates_nan():
    vals = np.arange(100, dtype=float)
    vals[50] = np.nan
    pe = permutation_entropy(pd.Series(vals), window=30, embedding_dimension=3)
    # Any window covering index 50 must be NaN (rows 50..79).
    assert pe.iloc[50:80].isna().all()


def test_entropy_features_columns_and_regime():
    rng = np.random.default_rng(3)
    s = pd.Series(rng.normal(size=400))
    feats = entropy_features(s, window=40, embedding_dimension=3, threshold=0.85)
    assert list(feats.columns) == [
        "perm_entropy",
        "entropy_rank",
        "entropy_regime",
        "is_tradeable",
    ]
    regimes = set(feats["entropy_regime"].dropna().unique())
    assert regimes.issubset({"low", "normal", "high"})
    rank = feats["entropy_rank"].dropna()
    assert (rank >= 0.0).all() and (rank <= 1.0).all()


def test_tradeable_mask_respects_threshold():
    # Structured (low-entropy) segment then noisy segment.
    structured = np.sin(np.linspace(0, 20 * np.pi, 150))
    rng = np.random.default_rng(4)
    noisy = rng.normal(size=150)
    s = pd.Series(np.concatenate([structured, noisy]))
    mask = tradeable_mask(s, threshold=0.85, window=30, embedding_dimension=3)
    # The structured portion should be more tradeable than the noisy portion.
    assert mask.iloc[40:150].mean() > mask.iloc[180:].mean()


def test_invalid_params_raise():
    s = pd.Series(np.arange(50, dtype=float))
    with pytest.raises(ValueError):
        permutation_entropy(s, window=30, embedding_dimension=1)
    with pytest.raises(ValueError):
        permutation_entropy(s, window=3, embedding_dimension=5)
