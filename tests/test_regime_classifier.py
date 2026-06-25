"""Tests for models/regime_classifier.py's row-count training gate.

No live database or real model file is touched: db is a stub object and
MODEL_PATH is patched to a tmp path, following the same convention as
tests/test_ops_routes.py.
"""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from models.regime_classifier import MIN_ROWS_TO_TRAIN, train_classifier


class _StubDB:
    def __init__(self, df: pd.DataFrame):
        self._df = df

    def read_sql(self, query: str) -> pd.DataFrame:
        return self._df


def _rows(n: int, separable: bool = False) -> pd.DataFrame:
    rng = np.random.RandomState(0)
    regime_trend = rng.uniform(0.0, 1.0, n)
    if separable:
        # Deterministic boundary on regime_trend so RandomForest easily
        # clears MIN_ACCURACY_TO_PROMOTE — this test is about the row-count
        # gate, not classifier quality.
        cons = (regime_trend > 0.5).astype(int)
        std = (regime_trend > 0.5).astype(int)
    else:
        cons = rng.randint(0, 2, n)
        std = rng.randint(0, 2, n)

    return pd.DataFrame({
        "regime_trend": regime_trend,
        "regime_volatility": rng.uniform(0.0, 5.0, n),
        "regime_direction": rng.choice(["bull", "bear", "neutral"], n),
        "window_years": rng.uniform(1.0, 5.0, n),
        "symbol": rng.choice(["BTC/USDT", "ETH/USDT"], n),
        "strategy_name": "EMA Crossover",
        "conservative_pass": cons,
        "standard_pass": std,
        "bars_used": 500,
        "params": "{}",
    })


def test_below_min_rows_does_not_train(tmp_path):
    pkl_path = tmp_path / "regime_classifier.pkl"
    db = _StubDB(_rows(MIN_ROWS_TO_TRAIN - 1))

    with patch("models.regime_classifier.MODEL_PATH", pkl_path):
        result = train_classifier(db)

    assert result is False
    assert not pkl_path.exists()


def test_at_or_above_min_rows_with_separable_data_trains_and_saves(tmp_path):
    pkl_path = tmp_path / "regime_classifier.pkl"
    db = _StubDB(_rows(MIN_ROWS_TO_TRAIN + 50, separable=True))

    with patch("models.regime_classifier.MODEL_PATH", pkl_path):
        result = train_classifier(db)

    assert result is True
    assert pkl_path.exists()

    import pickle
    with open(pkl_path, "rb") as f:
        bundle = pickle.load(f)
    assert "model" in bundle
    assert bundle["model"] is not None
    assert "feature_names" in bundle
    assert "encoder_state" in bundle


def test_empty_training_data_does_not_train(tmp_path):
    pkl_path = tmp_path / "regime_classifier.pkl"
    db = _StubDB(pd.DataFrame())

    with patch("models.regime_classifier.MODEL_PATH", pkl_path):
        result = train_classifier(db)

    assert result is False
    assert not pkl_path.exists()
