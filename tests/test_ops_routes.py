"""Offline tests for the regime-classifier-scoped /api/model-confidence route.

The route imports MODEL_PATH/_prepare_features from models.regime_classifier
inline on each call, so patching those module attributes directly (rather
than monitoring.routes.ops) is what actually takes effect.
"""

from __future__ import annotations

import pickle
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from monitoring.dashboard_app import create_app


@pytest.fixture
def client():
    app = create_app(db=MagicMock(), config={"retraining": {}})
    app.config.update(TESTING=True)
    return app.test_client()


class _FakeModel:
    """Module-level (picklable) stand-in for a trained sklearn classifier."""

    feature_importances_ = np.array([0.7, 0.3])

    def predict_proba(self, X):
        return np.array([[0.2, 0.8]])


def test_model_confidence_requires_symbol(client):
    resp = client.get("/api/model-confidence")
    assert resp.status_code == 400


def test_model_confidence_not_trained_yet(client):
    fake_path = MagicMock()
    fake_path.exists.return_value = False
    with patch("models.regime_classifier.MODEL_PATH", fake_path):
        resp = client.get("/api/model-confidence?symbol=BTC/USDT")
        assert resp.status_code == 200
        assert resp.get_json()["available"] is False


def test_model_confidence_no_regime_data_yet(client):
    fake_path = MagicMock()
    fake_path.exists.return_value = True
    client.application.config["DB"].read_sql.return_value = pd.DataFrame()
    with patch("models.regime_classifier.MODEL_PATH", fake_path):
        resp = client.get("/api/model-confidence?symbol=BTC/USDT")
        assert resp.get_json()["available"] is False


def test_model_confidence_returns_real_predict_proba_and_importances(client, tmp_path):
    bundle = {"model_standard": _FakeModel(), "encoder_state": ["BTC/USDT"]}
    pkl_path = tmp_path / "regime_classifier.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(bundle, f)

    row_df = pd.DataFrame([{
        "regime_trend": "up", "regime_volatility": "low", "regime_direction": "bull",
        "window_years": 2, "symbol": "BTC/USDT",
    }])
    client.application.config["DB"].read_sql.return_value = row_df

    with patch("models.regime_classifier.MODEL_PATH", pkl_path), patch(
        "models.regime_classifier._prepare_features",
        return_value=(np.zeros((1, 2)), ["regime_trend", "window_years"], None),
    ):
        resp = client.get("/api/model-confidence?symbol=BTC/USDT&target=standard")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["available"] is True
    assert data["confidence"] == 0.8
    assert data["feature_importance"] == {"regime_trend": 0.7, "window_years": 0.3}


def test_model_confidence_no_model_in_bundle(client, tmp_path):
    bundle = {"encoder_state": []}
    pkl_path = tmp_path / "regime_classifier.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(bundle, f)

    row_df = pd.DataFrame([{
        "regime_trend": "up", "regime_volatility": "low", "regime_direction": "bull",
        "window_years": 2, "symbol": "BTC/USDT",
    }])
    client.application.config["DB"].read_sql.return_value = row_df

    with patch("models.regime_classifier.MODEL_PATH", pkl_path):
        resp = client.get("/api/model-confidence?symbol=BTC/USDT")

    assert resp.get_json()["available"] is False
