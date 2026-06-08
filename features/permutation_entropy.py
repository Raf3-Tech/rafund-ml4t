"""Permutation-entropy features (Bandt & Pompe, 2002).

Permutation entropy (PE) quantifies the *complexity* of a time series via the
distribution of ordinal patterns in short embedded sub-sequences. Intuition:

  * PE near 1.0  -> all orderings equally likely -> noisy / unpredictable regime
  * PE near 0.0  -> a few orderings dominate     -> structured / predictable regime

For a mean-reversion / stat-arb book, trading *into* a high-entropy (noisy)
regime is where edge decays and costs dominate. We therefore expose PE as a
causal feature and, downstream, as a tradeability gate ``is_tradeable``.

Every estimate here is **strictly causal**: the value at row ``t`` is computed
only from observations up to and including ``t`` (rolling window for PE,
expanding/rolling percentile for the rank). No row ever sees the future, so the
features are safe for walk-forward training and for live position sizing.

References
----------
Bandt, C. & Pompe, B. (2002). "Permutation Entropy: A Natural Complexity
Measure for Time Series." Physical Review Letters 88(17).
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

# Default ordinal-pattern parameters (see Bandt & Pompe). Embedding dimension m
# in [3, 5] is standard; m! must stay small relative to the window so pattern
# frequencies are estimable.
DEFAULT_WINDOW = 30
DEFAULT_EMBEDDING_DIMENSION = 3
DEFAULT_DELAY = 1

# Regime cut-points on the normalized PE (in [0, 1]). Below LOW the series is
# strongly structured; above HIGH it is essentially noise.
#
# CALIBRATION: these are tuned to *daily crypto spread* data, where the spread
# PE (window 30, m=3) is itself high — empirical quantiles on ADA/DOT & ADA/LINK
# are q10~0.85, median~0.93, q90~0.98. Cut-points appropriate for a smoother
# series (e.g. equities, or a longer window) would be lower. For cross-instrument
# robustness prefer gating on the self-normalizing ``entropy_rank`` percentile.
_REGIME_LOW = 0.90
_REGIME_HIGH = 0.95


def _window_permutation_entropy(
    window: np.ndarray, embedding_dimension: int, delay: int
) -> float:
    """Normalized permutation entropy of a single 1-D window in [0, 1].

    Returns ``nan`` if the window is too short to form an embedding vector.
    """
    m = embedding_dimension
    n = window.shape[0]
    n_vectors = n - (m - 1) * delay
    if n_vectors <= 0:
        return float("nan")

    # Count ordinal patterns (the argsort ordering of each embedded vector).
    counts: dict[tuple, int] = {}
    for i in range(n_vectors):
        vec = window[i : i + (m - 1) * delay + 1 : delay]
        # mergesort -> stable, so ties resolve deterministically.
        pattern = tuple(np.argsort(vec, kind="mergesort"))
        counts[pattern] = counts.get(pattern, 0) + 1

    total = float(sum(counts.values()))
    probs = np.fromiter((c / total for c in counts.values()), dtype=float)
    entropy = -np.sum(probs * np.log(probs))
    norm = math.log(math.factorial(m))  # max entropy = log(m!)
    return float(entropy / norm) if norm > 0 else 0.0


def permutation_entropy(
    series: pd.Series,
    window: int = DEFAULT_WINDOW,
    embedding_dimension: int = DEFAULT_EMBEDDING_DIMENSION,
    delay: int = DEFAULT_DELAY,
) -> pd.Series:
    """Rolling, causal, normalized permutation entropy of ``series``.

    The value at each row uses only the trailing ``window`` observations ending
    at that row. Warm-up rows (and rows whose window contains NaNs) are ``nan``.
    """
    if embedding_dimension < 2:
        raise ValueError("embedding_dimension must be >= 2")
    if window < embedding_dimension + (embedding_dimension - 1) * (delay - 1) + 1:
        raise ValueError("window too small for the chosen embedding/delay")

    s = pd.Series(series, dtype="float64")
    vals = s.to_numpy(dtype=float)
    out = np.full(vals.shape[0], np.nan)
    for t in range(window - 1, vals.shape[0]):
        w = vals[t - window + 1 : t + 1]
        if np.isnan(w).any():
            continue
        out[t] = _window_permutation_entropy(w, embedding_dimension, delay)
    return pd.Series(out, index=s.index, name="perm_entropy")


def _causal_percentile_rank(pe: pd.Series, rank_window: int, min_periods: int) -> pd.Series:
    """Percentile rank of each PE value within its trailing ``rank_window``.

    Causal: rank at ``t`` = fraction of the trailing window <= the current
    value. In [0, 1]; warm-up rows are ``nan``.
    """
    def _rank(x: np.ndarray) -> float:
        cur = x[-1]
        if np.isnan(cur):
            return float("nan")
        valid = x[~np.isnan(x)]
        if valid.size == 0:
            return float("nan")
        return float((valid <= cur).mean())

    return pe.rolling(rank_window, min_periods=min_periods).apply(_rank, raw=True)


def entropy_features(
    series: pd.Series,
    window: int = DEFAULT_WINDOW,
    embedding_dimension: int = DEFAULT_EMBEDDING_DIMENSION,
    delay: int = DEFAULT_DELAY,
    threshold: float = _REGIME_HIGH,
    rank_window: int = 252,
) -> pd.DataFrame:
    """Compute the full causal entropy feature set for ``series``.

    Returns a DataFrame with columns:
      * ``perm_entropy``   - normalized PE in [0, 1]
      * ``entropy_rank``   - causal trailing-window percentile of PE in [0, 1]
      * ``entropy_regime`` - {'low', 'normal', 'high'} (NaN during warm-up)
      * ``is_tradeable``   - PE strictly below ``threshold`` (noisy regimes excluded)
    """
    pe = permutation_entropy(series, window, embedding_dimension, delay)
    rank = _causal_percentile_rank(pe, rank_window=rank_window, min_periods=window)

    regime = pd.Series(np.nan, index=pe.index, dtype="object")
    regime[pe <= _REGIME_LOW] = "low"
    regime[(pe > _REGIME_LOW) & (pe <= _REGIME_HIGH)] = "normal"
    regime[pe > _REGIME_HIGH] = "high"

    # Warm-up rows (PE is NaN) are NOT tradeable: we never trade on an
    # uninformative entropy estimate. Established rows trade only below threshold.
    is_tradeable = (pe < threshold).where(pe.notna(), False).astype(bool)

    return pd.DataFrame(
        {
            "perm_entropy": pe,
            "entropy_rank": rank,
            "entropy_regime": regime,
            "is_tradeable": is_tradeable,
        }
    )


def tradeable_mask(
    series: pd.Series,
    threshold: float,
    window: int = DEFAULT_WINDOW,
    embedding_dimension: int = DEFAULT_EMBEDDING_DIMENSION,
    delay: int = DEFAULT_DELAY,
) -> pd.Series:
    """Boolean gate: True where the regime is calm enough to trade.

    Convenience wrapper used by the strategy layer to filter signals.
    """
    pe = permutation_entropy(series, window, embedding_dimension, delay)
    return (pe < threshold).where(pe.notna(), False).astype(bool)
