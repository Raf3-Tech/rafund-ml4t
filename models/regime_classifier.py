"""Regime classifier — predicts which strategies pass CONSERVATIVE/STANDARD in a window.

Training data: engine_results rows (bars_used > min_bars + 30).
Features: regime_trend, regime_volatility, regime_direction, window_years, symbol (one-hot).
Targets: conservative_pass, standard_pass (binary).

Only promoted to production when accuracy >= 60%.  The engine runs without it.
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).resolve().parent / "regime_classifier.pkl"
MIN_ROWS_TO_TRAIN = 200
MIN_ACCURACY_TO_PROMOTE = 0.60


def _load_training_data(db) -> Optional[pd.DataFrame]:
    try:
        df = db.read_sql("""
            SELECT
                regime_trend,
                regime_volatility,
                regime_direction,
                window_years,
                symbol,
                strategy_name,
                conservative_pass,
                standard_pass,
                bars_used,
                params
            FROM engine_results
            WHERE bars_used IS NOT NULL
        """)
        return df
    except Exception as e:
        logger.error("Failed to load training data: %s", e)
        return None


def _prepare_features(df: pd.DataFrame, fit_encoder: bool = True, encoder_state=None):
    """Encode features for the classifier.  Returns (X, feature_names, encoder_state)."""
    features = df[["regime_trend", "regime_volatility", "window_years"]].copy()

    # Direction: bull=1, bear=0, neutral=0.5
    direction_map = {"bull": 1.0, "bear": 0.0, "neutral": 0.5}
    features["regime_direction_enc"] = df["regime_direction"].map(direction_map).fillna(0.5)

    # One-hot encode symbol
    if fit_encoder:
        symbols = sorted(df["symbol"].unique())
        encoder_state = symbols
    else:
        symbols = encoder_state or []

    for sym in symbols:
        features[f"sym_{sym.replace('/', '_')}"] = (df["symbol"] == sym).astype(float)

    feature_names = list(features.columns)
    return features.values.astype(float), feature_names, encoder_state


def train_classifier(db) -> bool:
    """Train a Random Forest classifier on engine_results and save to disk."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score, precision_score, recall_score

    df = _load_training_data(db)
    if df is None or len(df) < MIN_ROWS_TO_TRAIN:
        count = len(df) if df is not None else 0
        logger.warning(
            "Insufficient training data: %d rows (need %d). "
            "Run more engine windows and retry.",
            count, MIN_ROWS_TO_TRAIN,
        )
        return False

    X, feature_names, encoder_state = _prepare_features(df, fit_encoder=True)
    y_cons = df["conservative_pass"].astype(int).values
    y_std = df["standard_pass"].astype(int).values

    results = {}
    for target_name, y in [("conservative", y_cons), ("standard", y_std)]:
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=0.2, random_state=42, stratify=y
        )

        clf = RandomForestClassifier(n_estimators=100, random_state=42, class_weight="balanced")
        clf.fit(X_train, y_train)

        y_pred = clf.predict(X_test)
        acc = accuracy_score(y_test, y_pred)
        prec = precision_score(y_test, y_pred, zero_division=0)
        rec = recall_score(y_test, y_pred, zero_division=0)

        logger.info(
            "Classifier [%s]: accuracy=%.3f precision=%.3f recall=%.3f (n=%d)",
            target_name, acc, prec, rec, len(X),
        )

        if acc < MIN_ACCURACY_TO_PROMOTE:
            logger.warning(
                "Classifier [%s] accuracy %.3f below threshold %.3f — NOT promoted to production. "
                "Dataset may be too small or too imbalanced.",
                target_name, acc, MIN_ACCURACY_TO_PROMOTE,
            )
            results[target_name] = None
        else:
            results[target_name] = clf

    if not any(v is not None for v in results.values()):
        logger.error("No classifier reached production threshold.")
        return False

    bundle = {
        "model_conservative": results.get("conservative"),
        "model_standard": results.get("standard"),
        "feature_names": feature_names,
        "encoder_state": encoder_state,
        "model": results.get("standard") or results.get("conservative"),
    }

    with open(MODEL_PATH, "wb") as f:
        pickle.dump(bundle, f)

    logger.info("Classifier saved to %s", MODEL_PATH)
    return True


def predict(df: pd.DataFrame, target: str = "standard") -> Optional[np.ndarray]:
    """Predict pass/fail for new windows.  Returns probability array or None if no model."""
    if not MODEL_PATH.exists():
        return None
    with open(MODEL_PATH, "rb") as f:
        bundle = pickle.load(f)

    model_key = f"model_{target}"
    model = bundle.get(model_key) or bundle.get("model")
    if model is None:
        return None

    encoder_state = bundle.get("encoder_state", [])
    X, _, _ = _prepare_features(df, fit_encoder=False, encoder_state=encoder_state)

    try:
        return model.predict_proba(X)[:, 1]
    except Exception as e:
        logger.error("Prediction failed: %s", e)
        return None
