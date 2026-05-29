from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from models.blocker import ModelBlocker
from models.exceptions import BlockStatus, ModelBlockedError


def test_is_blocked_returns_not_registered_when_no_active_model():
    db = MagicMock()
    db.get_active_production_model.return_value = None

    blocker = ModelBlocker(db=db)
    status = blocker.is_blocked("rafund_factor_model_BTCUSDT", "BTC/USDT")

    assert status.blocked is True
    assert status.reason == "not_registered"
    assert status.safe_to_trade is False


def test_is_blocked_returns_stale_for_old_model():
    db = MagicMock()
    old_time = datetime.now(timezone.utc) - timedelta(days=31)
    db.get_active_production_model.return_value = {
        "trained_at": old_time.isoformat(),
        "stage": "Production",
        "is_active": True,
    }
    db.get_recent_drift_report.return_value = None

    blocker = ModelBlocker(db=db, max_model_age_days=30)
    status = blocker.is_blocked("rafund_factor_model_BTCUSDT", "BTC/USDT")

    assert status.blocked is True
    assert status.reason == "stale"
    assert status.model_age_days == 31


def test_is_blocked_returns_drift_for_critical_recent_report():
    db = MagicMock()
    db.get_active_production_model.return_value = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "stage": "Production",
        "is_active": True,
    }
    db.get_recent_drift_report.return_value = {"severity": "critical"}

    blocker = ModelBlocker(db=db)
    status = blocker.is_blocked("rafund_factor_model_BTCUSDT", "BTC/USDT")

    assert status.blocked is True
    assert status.reason == "drift"
    assert status.last_drift_severity == "critical"


def test_is_blocked_allows_trading_when_model_is_production_and_clean():
    db = MagicMock()
    db.get_active_production_model.return_value = {
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "stage": "Production",
        "is_active": True,
    }
    db.get_recent_drift_report.return_value = None

    blocker = ModelBlocker(db=db)
    status = blocker.is_blocked("rafund_factor_model_BTCUSDT", "BTC/USDT")

    assert status.blocked is False
    assert status.reason is None
    assert status.safe_to_trade is True


def test_predict_raises_model_blocked_error_when_blocked():
    fake_status = BlockStatus(blocked=True, reason="drift", model_age_days=None, last_drift_severity="critical", safe_to_trade=False)
    with patch("models.predict.ModelBlocker.is_blocked", return_value=fake_status):
        from models.predict import Predictor
        predictor = Predictor(model_name="rafund_factor_model_BTCUSDT", symbol="BTC/USDT", db=MagicMock(), model=MagicMock())
        try:
            predictor.predict(pd.DataFrame([[1.0]], columns=["spread"]))
            assert False, "Expected ModelBlockedError"
        except ModelBlockedError as exc:
            assert "blocked" in str(exc)


def test_block_and_unblock_call_database_methods():
    db = MagicMock()
    db.block_model.return_value = True
    db.unblock_model.return_value = True

    blocker = ModelBlocker(db=db)
    blocker.block("rafund_factor_model_BTCUSDT", "BTC/USDT", reason="drift")
    blocker.unblock("rafund_factor_model_BTCUSDT", "BTC/USDT")

    db.block_model.assert_called_once_with("rafund_factor_model_BTCUSDT", "BTC/USDT", "drift")
    db.unblock_model.assert_called_once_with("rafund_factor_model_BTCUSDT", "BTC/USDT")
