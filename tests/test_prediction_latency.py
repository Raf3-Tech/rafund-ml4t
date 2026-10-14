import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch

import pytest

from models.exceptions import BlockStatus
from models.predict import Predictor, benchmark_predict


def test_benchmark_predict_returns_latency_dictionary():
    with patch("models.predict.DatabaseConnection") as mock_db_cls, \
         patch("models.predict.Predictor._get_model") as mock_cached_model, \
         patch("models.predict.Predictor.predict") as mock_predict:
        mock_db = MagicMock()
        mock_db.close_pool.return_value = None
        mock_db_cls.return_value = mock_db
        mock_cached_model.return_value = MagicMock(feature_names_in_=np.array(["feature_0", "feature_1"]))
        mock_predict.return_value = np.array([0.0])

        result = benchmark_predict("rafund_factor_model_BTCUSDT", "BTC/USDT", n_runs=10)

    assert set(result.keys()) == {"mean_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"}
    assert result["mean_ms"] >= 0.0


def test_predict_under_200ms_does_not_log_warning():
    model = MagicMock()
    model.predict.return_value = np.array([0.0])
    db = MagicMock()
    predictor = Predictor("rafund_factor_model_BTCUSDT", "BTC/USDT", db=db, model=model)
    blocked_status = BlockStatus(blocked=False, reason=None, model_age_days=None, last_drift_severity=None, safe_to_trade=True)

    with patch("models.predict.ModelBlocker.is_blocked", return_value=blocked_status), \
         patch("models.predict.logger.warning") as mock_warning, \
         patch("models.predict.time.perf_counter", side_effect=[0.0, 0.1]):
        predictor.predict(pd.DataFrame([[1.0]], columns=["spread"]))

    mock_warning.assert_not_called()


def test_predict_over_500ms_logs_error():
    model = MagicMock()
    model.predict.return_value = np.array([0.0])
    db = MagicMock()
    predictor = Predictor("rafund_factor_model_BTCUSDT", "BTC/USDT", db=db, model=model)
    blocked_status = BlockStatus(blocked=False, reason=None, model_age_days=None, last_drift_severity=None, safe_to_trade=True)

    with patch("models.predict.ModelBlocker.is_blocked", return_value=blocked_status), \
         patch("models.predict.logger.error") as mock_error, \
         patch("models.predict.time.perf_counter", side_effect=[0.0, 0.6]):
        predictor.predict(pd.DataFrame([[1.0]], columns=["spread"]))

    mock_error.assert_called_once()


def test_benchmark_offline_touches_neither_db_nor_mlflow(capsys):
    with patch("models.predict.DatabaseConnection", side_effect=AssertionError("db used")), \
         patch("models.predict.ModelBlocker", side_effect=AssertionError("blocker used")), \
         patch("models.predict.configure_mlflow", side_effect=AssertionError("mlflow used")), \
         patch("models.predict.get_latest_model", side_effect=AssertionError("mlflow used")):
        result = benchmark_predict("any_model", "BTC/USDT", n_runs=5, offline=True)

    assert set(result.keys()) == {"mean_ms", "p50_ms", "p95_ms", "p99_ms", "max_ms"}
    assert all(np.isfinite(v) and v >= 0.0 for v in result.values())
    assert "mode=offline" in capsys.readouterr().out


def test_benchmark_online_does_not_fall_back_when_db_unavailable():
    with patch("models.predict.DatabaseConnection", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError, match="db down"):
            benchmark_predict("any_model", "BTC/USDT", n_runs=5)


def test_benchmark_online_closes_pool_on_failure():
    mock_db = MagicMock()
    with patch("models.predict.DatabaseConnection", return_value=mock_db), \
         patch("models.predict.Predictor._load_model", side_effect=ValueError("no model")):
        with pytest.raises(ValueError, match="no model"):
            benchmark_predict("any_model", "BTC/USDT", n_runs=5)
    mock_db.close_pool.assert_called_once()


def test_model_cache_is_per_instance_and_loads_once():
    loads = []

    def fake_load(self):
        loads.append(self)
        return object()

    with patch("models.predict.Predictor._load_model", fake_load):
        a = Predictor("m", "BTC/USDT", db=MagicMock())
        b = Predictor("m", "BTC/USDT", db=MagicMock())
        model_a = a._get_model()
        assert a._get_model() is model_a
        model_b = b._get_model()

    assert model_a is not model_b
    assert loads == [a, b]


def test_injected_model_bypasses_loader():
    model = MagicMock()
    with patch("models.predict.Predictor._load_model", side_effect=AssertionError("loaded")):
        assert Predictor("m", "BTC/USDT", db=MagicMock(), model=model)._get_model() is model
