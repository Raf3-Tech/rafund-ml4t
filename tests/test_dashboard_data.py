from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from models.exceptions import BlockStatus
from monitoring.dashboard_data import (
    DriftStatusRow,
    ModelStatusRow,
    get_drift_status,
    get_model_status,
    row_to_dict,
)


def _block_status(
    blocked=False,
    reason=None,
    model_age_days=5,
    last_drift_severity=None,
    safe_to_trade=True,
):
    return BlockStatus(
        blocked=blocked,
        reason=reason,
        model_age_days=model_age_days,
        last_drift_severity=last_drift_severity,
        safe_to_trade=safe_to_trade,
    )


@patch("monitoring.dashboard_data.ModelBlocker")
def test_get_model_status_merges_registry_and_block_status(mock_blocker_cls):
    db = MagicMock()
    db.get_production_models.return_value = [
        {
            "model_name": "rafund_factor_model_BTCUSDT",
            "symbol": "BTC/USDT",
            "stage": "Production",
            "oos_sharpe": "1.5",
        }
    ]
    blocker = MagicMock()
    blocker.is_blocked.return_value = _block_status(
        blocked=False,
        reason=None,
        model_age_days=7,
        safe_to_trade=True,
    )
    mock_blocker_cls.return_value = blocker

    rows = get_model_status(db)

    assert len(rows) == 1
    row = rows[0]
    assert isinstance(row, ModelStatusRow)
    assert row.model_name == "rafund_factor_model_BTCUSDT"
    assert row.symbol == "BTC/USDT"
    assert row.stage == "Production"
    assert row.age_days == 7
    assert row.oos_sharpe == 1.5
    assert row.blocked is False
    assert row.block_reason is None
    assert row.safe_to_trade is True
    blocker.is_blocked.assert_called_once_with(
        "rafund_factor_model_BTCUSDT", "BTC/USDT"
    )


@patch("monitoring.dashboard_data.ModelBlocker")
def test_get_model_status_populates_block_reason(mock_blocker_cls):
    db = MagicMock()
    db.get_production_models.return_value = [
        {
            "model_name": "m1",
            "symbol": "ETH/USDT",
            "stage": "Production",
            "oos_sharpe": None,
        }
    ]
    blocker = MagicMock()
    blocker.is_blocked.return_value = _block_status(
        blocked=True,
        reason="stale",
        model_age_days=45,
        safe_to_trade=False,
    )
    mock_blocker_cls.return_value = blocker

    rows = get_model_status(db)

    assert len(rows) == 1
    row = rows[0]
    assert row.blocked is True
    assert row.block_reason == "stale"
    assert row.safe_to_trade is False
    assert row.age_days == 45
    assert row.oos_sharpe is None


@patch("monitoring.dashboard_data.ModelBlocker")
def test_get_model_status_skips_row_that_raises(mock_blocker_cls):
    db = MagicMock()
    db.get_production_models.return_value = [
        {"model_name": "bad", "symbol": "BTC/USDT", "stage": "Production", "oos_sharpe": 1.0},
        {"model_name": "good", "symbol": "ETH/USDT", "stage": "Production", "oos_sharpe": 2.0},
    ]
    blocker = MagicMock()

    def side_effect(model_name, symbol):
        if model_name == "bad":
            raise RuntimeError("blocker lookup exploded")
        return _block_status(model_age_days=3)

    blocker.is_blocked.side_effect = side_effect
    mock_blocker_cls.return_value = blocker

    rows = get_model_status(db)

    assert len(rows) == 1
    assert rows[0].model_name == "good"
    assert rows[0].oos_sharpe == 2.0


@patch("monitoring.dashboard_data.ModelBlocker")
def test_get_model_status_empty_when_no_models(mock_blocker_cls):
    db = MagicMock()
    db.get_production_models.return_value = []
    mock_blocker_cls.return_value = MagicMock()

    assert get_model_status(db) == []


def test_get_drift_status_maps_rows():
    db = MagicMock()
    detected = datetime(2026, 6, 4, 12, 0, tzinfo=timezone.utc)
    db.get_latest_drift_reports.return_value = [
        {
            "model_name": "m1",
            "symbol": "BTC/USDT",
            "severity": "critical",
            "detected_at": detected,
        },
        {
            "model_name": "m2",
            "symbol": "ETH/USDT",
            "severity": "warning",
            "detected_at": detected,
        },
    ]

    rows = get_drift_status(db)

    assert len(rows) == 2
    assert isinstance(rows[0], DriftStatusRow)
    assert rows[0].severity == "critical"
    assert rows[0].detected_at == detected
    assert rows[1].severity == "warning"


def test_get_drift_status_defaults_missing_severity_to_unknown():
    db = MagicMock()
    db.get_latest_drift_reports.return_value = [
        {"model_name": "m1", "symbol": "BTC/USDT", "detected_at": None},
        {
            "model_name": "m2",
            "symbol": "ETH/USDT",
            "severity": "bogus",
            "detected_at": None,
        },
    ]

    rows = get_drift_status(db)

    assert len(rows) == 2
    assert rows[0].severity == "unknown"
    assert rows[1].severity == "unknown"


def test_row_to_dict_is_json_serializable_with_datetime():
    detected = datetime(2026, 6, 4, 12, 30, tzinfo=timezone.utc)
    row = DriftStatusRow(
        model_name="m1",
        symbol="BTC/USDT",
        severity="warning",
        detected_at=detected,
    )

    result = row_to_dict(row)

    assert result["detected_at"] == detected.isoformat()
    assert isinstance(result["detected_at"], str)
    # Must be JSON-serializable end to end.
    encoded = json.dumps(result)
    assert "warning" in encoded


def test_row_to_dict_handles_model_status_row():
    row = ModelStatusRow(
        model_name="m1",
        symbol="BTC/USDT",
        stage="Production",
        age_days=10,
        oos_sharpe=1.25,
        blocked=False,
        block_reason=None,
        safe_to_trade=True,
    )

    result = row_to_dict(row)

    assert json.loads(json.dumps(result)) == result
    assert result["oos_sharpe"] == 1.25
    assert result["block_reason"] is None
