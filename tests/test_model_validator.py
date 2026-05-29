from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from backtesting.splitter import TimeSeriesSplitter
from models.train import ModelMetadata
from models.validator import ModelValidator


class DummyAggregate:
    def __init__(self, mean_oos_sharpe: float, worst_oos_drawdown: float) -> None:
        self.mean_oos_sharpe = mean_oos_sharpe
        self.worst_oos_drawdown = worst_oos_drawdown


class DummyWalkResult:
    def __init__(self, aggregate: DummyAggregate) -> None:
        self.aggregate = aggregate


def _build_validator(db, incumbent=None):
    with patch("models.validator.mlflow.pyfunc.load_model") as load_model, \
         patch("models.validator.WalkForwardRunner") as runner_cls, \
         patch("models.validator.MlflowClient") as mlflow_client_cls:
        load_model.return_value = MagicMock()
        validator = ModelValidator(db=db, splitter=TimeSeriesSplitter(), engine_config={})
        validator._runner_cls = runner_cls
        return validator, runner_cls, mlflow_client_cls


def _build_candidate_meta() -> ModelMetadata:
    return ModelMetadata(
        run_id="run-456",
        model_name="rafund_factor_model_BTCUSDT",
        model_version=1,
        symbol="BTC/USDT",
        trained_at=datetime.now(timezone.utc),
        train_start=date.today(),
        train_end=date.today(),
        in_sample_sharpe=1.2,
        feature_names=["spread", "spread_mean"],
        stage="Staging",
    )


@patch("models.validator.ModelValidator._load_validation_data")
def test_validate_for_promotion_approves_candidate_with_strong_oos(
    mock_load_validation_data,
):
    db = MagicMock()
    db.get_active_production_model.return_value = None
    mock_load_validation_data.return_value = pd.DataFrame({"spread": [1.0, 2.0]})

    validator = ModelValidator(db=db, splitter=TimeSeriesSplitter(), engine_config={})
    aggregate = DummyAggregate(mean_oos_sharpe=1.2, worst_oos_drawdown=4.0)
    walk_result = DummyWalkResult(aggregate=aggregate)

    with patch("models.validator.mlflow.pyfunc.load_model"), \
         patch("models.validator.WalkForwardRunner") as runner_cls, \
         patch("models.validator.MlflowClient") as mlflow_client_cls:
        runner_inst = MagicMock()
        runner_inst.run.return_value = walk_result
        runner_cls.return_value = runner_inst

        decision = validator.validate_for_promotion(_build_candidate_meta(), "BTC/USDT", "1d")

    assert decision.approved is True
    assert decision.candidate_sharpe == 1.2
    assert decision.candidate_drawdown == 4.0
    assert decision.incumbent_sharpe is None
    assert decision.regression_detected is False


@patch("models.validator.ModelValidator._load_validation_data")
def test_validate_for_promotion_rejects_low_sharpe(mock_load_validation_data):
    db = MagicMock()
    db.get_active_production_model.return_value = None
    mock_load_validation_data.return_value = pd.DataFrame({"spread": [1.0, 2.0]})

    validator = ModelValidator(db=db, splitter=TimeSeriesSplitter(), engine_config={})
    aggregate = DummyAggregate(mean_oos_sharpe=0.6, worst_oos_drawdown=2.0)
    walk_result = DummyWalkResult(aggregate=aggregate)

    with patch("models.validator.mlflow.pyfunc.load_model"), \
         patch("models.validator.WalkForwardRunner") as runner_cls, \
         patch("models.validator.MlflowClient") as mlflow_client_cls:
        runner_inst = MagicMock()
        runner_inst.run.return_value = walk_result
        runner_cls.return_value = runner_inst

        decision = validator.validate_for_promotion(_build_candidate_meta(), "BTC/USDT", "1d")

    assert decision.approved is False
    assert "Gate failed" in decision.reason


@patch("models.validator.ModelValidator._load_validation_data")
def test_validate_for_promotion_does_not_detect_regression_with_acceptable_candidate(
    mock_load_validation_data,
):
    db = MagicMock()
    db.get_active_production_model.return_value = {"oos_sharpe": 1.5, "mlflow_version": 1}
    mock_load_validation_data.return_value = pd.DataFrame({"spread": [1.0, 2.0]})

    validator = ModelValidator(db=db, splitter=TimeSeriesSplitter(), engine_config={})
    aggregate = DummyAggregate(mean_oos_sharpe=1.3, worst_oos_drawdown=4.0)
    walk_result = DummyWalkResult(aggregate=aggregate)

    with patch("models.validator.mlflow.pyfunc.load_model"), \
         patch("models.validator.WalkForwardRunner") as runner_cls, \
         patch("models.validator.MlflowClient") as mlflow_client_cls:
        runner_inst = MagicMock()
        runner_inst.run.return_value = walk_result
        runner_cls.return_value = runner_inst

        decision = validator.validate_for_promotion(_build_candidate_meta(), "BTC/USDT", "1d")

    assert decision.approved is True
    assert decision.regression_detected is False


@patch("models.validator.ModelValidator._load_validation_data")
def test_validate_for_promotion_detects_regression_and_rejects_candidate(
    mock_load_validation_data,
):
    db = MagicMock()
    db.get_active_production_model.return_value = {"oos_sharpe": 1.5, "mlflow_version": 1}
    mock_load_validation_data.return_value = pd.DataFrame({"spread": [1.0, 2.0]})

    validator = ModelValidator(db=db, splitter=TimeSeriesSplitter(), engine_config={})
    aggregate = DummyAggregate(mean_oos_sharpe=1.1, worst_oos_drawdown=4.0)
    walk_result = DummyWalkResult(aggregate=aggregate)

    with patch("models.validator.mlflow.pyfunc.load_model"), \
         patch("models.validator.WalkForwardRunner") as runner_cls, \
         patch("models.validator.MlflowClient") as mlflow_client_cls:
        runner_inst = MagicMock()
        runner_inst.run.return_value = walk_result
        runner_cls.return_value = runner_inst

        decision = validator.validate_for_promotion(_build_candidate_meta(), "BTC/USDT", "1d")

    assert decision.approved is False
    assert decision.regression_detected is True
    assert "Regression guard failed" in decision.reason
