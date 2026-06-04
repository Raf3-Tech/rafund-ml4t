"""Offline tests for the runnable model-lifecycle entrypoints.

Covers the convenience wrappers added over the existing scheduler / validator /
drift code: ``run_retraining_cycle`` and ``run_drift_check``. All external
dependencies (MLflow, the trainer, the validator, and the database) are mocked
so the suite runs without a database or tracking server.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from models.retraining_scheduler import (
    RetrainResult,
    run_drift_check,
    run_retraining_cycle,
)
from monitoring.drift_detector import FeatureDriftDetector


def _config(**overrides):
    retraining = {
        "models": ["stat_arb"],
        "symbols": ["BTC/USDT"],
        "timeframe": "1d",
    }
    retraining.update(overrides)
    return {"retraining": retraining}


# --------------------------------------------------------------------------- #
# run_retraining_cycle
# --------------------------------------------------------------------------- #
@patch("models.retraining_scheduler.configure_mlflow")
@patch("models.retraining_scheduler.ModelValidator")
@patch("models.retraining_scheduler.ModelTrainer")
def test_run_retraining_cycle_promotes_each_pair(trainer_cls, validator_cls, _mlflow):
    db = MagicMock()
    meta = MagicMock(model_name="rafund_stat_arb_BTC_USDT", run_id="run-1")
    trainer_cls.return_value.train.return_value = meta
    validator_cls.return_value.validate_for_promotion.return_value = MagicMock(
        approved=True, reason="passed"
    )

    results = run_retraining_cycle(db, _config())

    assert len(results) == 1
    result = results[0]
    assert isinstance(result, RetrainResult)
    assert result.success is True
    assert result.promoted is True
    assert result.run_id == "run-1"
    assert result.model_name == "rafund_stat_arb_BTC_USDT"
    assert result.error is None
    trainer_cls.return_value.train.assert_called_once_with("stat_arb", "BTC/USDT")


@patch("models.retraining_scheduler.configure_mlflow")
@patch("models.retraining_scheduler.ModelValidator")
@patch("models.retraining_scheduler.ModelTrainer")
def test_run_retraining_cycle_records_rejection(trainer_cls, validator_cls, _mlflow):
    db = MagicMock()
    trainer_cls.return_value.train.return_value = MagicMock(
        model_name="m", run_id="run-2"
    )
    validator_cls.return_value.validate_for_promotion.return_value = MagicMock(
        approved=False, reason="Gate failed"
    )

    results = run_retraining_cycle(db, _config())

    assert results[0].success is True
    assert results[0].promoted is False
    assert results[0].reason == "Gate failed"


@patch("models.retraining_scheduler.configure_mlflow")
@patch("models.retraining_scheduler.ModelTrainer")
def test_run_retraining_cycle_isolates_failures(trainer_cls, _mlflow):
    db = MagicMock()
    trainer_cls.return_value.train.side_effect = ValueError("no training data")

    results = run_retraining_cycle(db, _config(symbols=["BTC/USDT", "ETH/USDT"]))

    assert len(results) == 2
    assert all(r.success is False for r in results)
    assert all(r.promoted is False for r in results)
    assert "no training data" in results[0].error


@patch("models.retraining_scheduler.configure_mlflow")
def test_run_retraining_cycle_no_config_returns_empty(_mlflow):
    db = MagicMock()

    assert run_retraining_cycle(db, {"retraining": {}}) == []


# --------------------------------------------------------------------------- #
# run_drift_check
# --------------------------------------------------------------------------- #
def _patch_distributions(reference: pd.DataFrame, current: pd.DataFrame):
    return patch.multiple(
        FeatureDriftDetector,
        load_reference_distribution=MagicMock(return_value=reference),
        load_current_distribution=MagicMock(return_value=current),
        save_report=MagicMock(return_value=None),
    )


def test_run_drift_check_returns_report_for_stable_features():
    db = MagicMock()
    db.get_active_production_model.return_value = {
        "model_name": "rafund_stat_arb_BTC_USDT",
        "feature_names": '["spread", "z_score"]',
    }
    stable = pd.DataFrame(
        {"spread": np.random.normal(size=400), "z_score": np.random.normal(size=400)}
    )

    with _patch_distributions(stable, stable.copy()):
        report = run_drift_check(db, "rafund_stat_arb_BTC_USDT", "BTC/USDT")

    assert report is not None
    assert report.severity == "none"
    assert report.drift_detected is False
    db.block_model.assert_not_called()


def test_run_drift_check_blocks_on_critical_drift():
    db = MagicMock()
    db.get_active_production_model.return_value = {
        "model_name": "m",
        "feature_names": ["spread"],
    }
    reference = pd.DataFrame({"spread": np.random.normal(0, 1, 400)})
    current = pd.DataFrame({"spread": np.random.normal(5, 1, 400)})

    with _patch_distributions(reference, current):
        report = run_drift_check(db, "m", "BTC/USDT", block_on_critical=True)

    assert report.severity == "critical"
    assert report.recommended_action == "halt_trading"
    db.block_model.assert_called_once_with("m", "BTC/USDT", "drift")


def test_run_drift_check_accepts_registry_entry_dict():
    db = MagicMock()
    entry = {"model_name": "m", "feature_names": ["spread"]}
    frame = pd.DataFrame({"spread": np.random.normal(size=300)})

    with _patch_distributions(frame, frame.copy()):
        report = run_drift_check(db, entry, "BTC/USDT")

    assert report is not None
    # A dict entry should be used directly without a registry lookup.
    db.get_active_production_model.assert_not_called()


def test_run_drift_check_unregistered_model_returns_none():
    db = MagicMock()
    db.get_active_production_model.return_value = None

    assert run_drift_check(db, "missing", "BTC/USDT") is None


def test_run_drift_check_insufficient_data_returns_none():
    db = MagicMock()
    db.get_active_production_model.return_value = {
        "model_name": "m",
        "feature_names": ["spread"],
    }
    empty = pd.DataFrame(columns=["spread"])
    full = pd.DataFrame({"spread": np.random.normal(size=100)})

    with _patch_distributions(full, empty):
        assert run_drift_check(db, "m", "BTC/USDT") is None


# --------------------------------------------------------------------------- #
# FeatureDriftDetector.load_current_distribution
# --------------------------------------------------------------------------- #
@patch("monitoring.drift_detector.pd.read_sql")
def test_load_current_distribution_selects_feature_columns(read_sql):
    db = MagicMock()
    read_sql.return_value = pd.DataFrame(
        {
            "timestamp": pd.date_range("2024-01-01", periods=3, freq="h"),
            "spread": [1.0, 2.0, 3.0],
            "z_score": [0.1, 0.2, 0.3],
        }
    )
    detector = FeatureDriftDetector(model_name="m")

    result = detector.load_current_distribution(db, "BTC/USDT", ["spread", "z_score"])

    assert list(result.columns) == ["spread", "z_score"]
    assert len(result) == 3
    db.return_connection.assert_called_once()


@patch("monitoring.drift_detector.pd.read_sql")
def test_load_current_distribution_empty_returns_named_columns(read_sql):
    db = MagicMock()
    read_sql.return_value = pd.DataFrame()
    detector = FeatureDriftDetector(model_name="m")

    result = detector.load_current_distribution(db, "BTC/USDT", ["spread"])

    assert result.empty
    assert list(result.columns) == ["spread"]
