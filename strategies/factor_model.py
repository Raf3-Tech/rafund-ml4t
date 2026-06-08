"""
Factor-based trading strategy.

This module implements a multi-factor scoring strategy. Each *factor* is a time
series carrying a directional view (e.g. momentum, carry, value). Factors are
combined into a single standardized composite score and traded with symmetric
z-thresholds.

Scientific design notes
------------------------
* **Standardize before combining.** Raw factors live on different scales, so a
  naive weighted sum lets the largest-scale factor dominate. We z-score each
  factor (using *frozen training* statistics) so weights mean what they say.
* **Causality / no look-ahead.** ``prepare(train_df)`` fits and FREEZES each
  factor's mean and std on the training fold only. Out-of-sample scoring reuses
  those frozen statistics, so a future test observation can never influence its
  own normalization. (The previous implementation thresholded an *unbounded*
  weighted sum against hard ``0.7 / 0.3`` cut-offs that assumed a [0, 1] score,
  and performed no causal normalization — both are fixed here.)
"""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


class FactorStrategy:
    """Multi-factor strategy with train-frozen standardization."""

    def __init__(self, entry_z: float = 1.0):
        """
        Args:
            entry_z: Absolute standardized-score threshold for taking a position.
                Long when composite z-score > +entry_z, short when < -entry_z.
        """
        self.entry_z = entry_z
        self.factors: Dict[str, pd.Series] = {}
        self.factor_weights: Dict[str, float] = {}
        # Frozen per-factor normalization statistics (set by prepare/fit).
        self._means: Dict[str, float] = {}
        self._stds: Dict[str, float] = {}
        self._fitted = False

    def add_factor(self, name: str, values: pd.Series, weight: float = 1.0) -> None:
        """Register a factor time series and its weight in the composite."""
        self.factors[name] = values
        self.factor_weights[name] = float(weight)

    def prepare(self, train_df: pd.DataFrame) -> None:
        """Freeze per-factor normalization statistics on the TRAIN fold only.

        ``train_df`` columns are matched to registered factor names. Factors not
        present in ``train_df`` keep statistics from their registered series so
        the strategy still works in the in-memory (non-walk-forward) flow.
        """
        self._means, self._stds = {}, {}
        for name in self.factors:
            source = train_df[name] if name in getattr(train_df, "columns", []) else self.factors[name]
            source = pd.Series(source, dtype="float64")
            mean = float(source.mean())
            std = float(source.std(ddof=0))
            self._means[name] = mean if np.isfinite(mean) else 0.0
            # Zero/degenerate-variance factors contribute nothing (std -> inf guard).
            self._stds[name] = std if (np.isfinite(std) and std > 0) else np.nan
        self._fitted = True

    # Walk-forward runner contract alias.
    fit = prepare

    def _standardize(self, name: str, values: pd.Series) -> pd.Series:
        """Z-score a factor with its frozen train stats; zero if degenerate."""
        values = pd.Series(values, dtype="float64")
        if self._fitted and name in self._means:
            mean, std = self._means[name], self._stds.get(name, np.nan)
        else:
            mean = float(values.mean())
            std = float(values.std(ddof=0))
            std = std if (np.isfinite(std) and std > 0) else np.nan
        if not np.isfinite(std) or std == 0 or np.isnan(std):
            return pd.Series(0.0, index=values.index)
        return (values - mean) / std

    def compute_composite_score(self, df: Optional[pd.DataFrame] = None) -> pd.Series:
        """Weight-normalized average of the standardized factors.

        Args:
            df: Optional frame providing out-of-sample factor columns. When None,
                the registered factor series are scored (in-memory mode).
        """
        if not self.factors:
            raise ValueError("No factors added. Use add_factor() first.")

        total_weight = sum(abs(w) for w in self.factor_weights.values())
        if total_weight == 0:
            total_weight = 1.0

        scores: Optional[pd.Series] = None
        for name, registered in self.factors.items():
            values = df[name] if (df is not None and name in getattr(df, "columns", [])) else registered
            z = self._standardize(name, values)
            contribution = z * (self.factor_weights[name] / total_weight)
            scores = contribution if scores is None else scores.add(contribution, fill_value=0.0)

        return scores if scores is not None else pd.Series(dtype="float64")

    def score(self, df: pd.DataFrame) -> pd.Series:
        """Out-of-sample composite score using frozen train statistics."""
        return self.compute_composite_score(df)

    def generate_signals(
        self,
        scores: pd.Series,
        entry_z: Optional[float] = None,
    ) -> pd.Series:
        """Map a STANDARDIZED composite score to positions.

        Long (+1) when score > +entry_z, short (-1) when score < -entry_z, else 0.
        """
        threshold = self.entry_z if entry_z is None else entry_z
        scores = pd.Series(scores, dtype="float64")
        signals = pd.Series(0, index=scores.index, dtype="int64")
        signals[scores > threshold] = 1
        signals[scores < -threshold] = -1
        return signals
