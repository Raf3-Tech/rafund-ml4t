import pytest
from unittest.mock import MagicMock, patch

from config import mlflow_config


def test_configure_mlflow_creates_experiment_when_missing():
    with patch("config.mlflow_config.mlflow.get_experiment_by_name", return_value=None) as get_experiment, \
         patch("config.mlflow_config.mlflow.create_experiment", return_value="123") as create_experiment, \
         patch("config.mlflow_config.mlflow.set_tracking_uri") as set_tracking_uri:
        experiment_id = mlflow_config.configure_mlflow()

    assert experiment_id == "123"
    get_experiment.assert_called_once_with("rafund-ml4t")
    create_experiment.assert_called_once_with("rafund-ml4t")
    set_tracking_uri.assert_called_once()


@patch("config.mlflow_config.configure_mlflow")
@patch("config.mlflow_config.MlflowClient")
def test_get_latest_model_returns_none_when_no_model_registered(mock_client_cls, mock_configure):
    mock_client = mock_client_cls.return_value
    mock_client.get_latest_versions.return_value = []

    result = mlflow_config.get_latest_model("rafund_test_model")

    assert result is None
    mock_client.get_latest_versions.assert_called_once_with(name="rafund_test_model", stages=["Production"])


@patch("config.mlflow_config.configure_mlflow")
@patch("config.mlflow_config.mlflow.start_run")
@patch("config.mlflow_config.mlflow.set_tag")
@patch("config.mlflow_config.mlflow.end_run")
def test_log_training_run_sets_failed_status_on_exception(
    mock_end_run,
    mock_set_tag,
    mock_start_run,
    mock_configure,
):
    fake_run = MagicMock()
    fake_run.info.run_id = "run-123"
    mock_start_run.return_value = fake_run

    with pytest.raises(RuntimeError):
        with mlflow_config.log_training_run("test_run"):
            raise RuntimeError("boom")

    mock_set_tag.assert_called_once_with("validation_status", "failed")
    mock_end_run.assert_called_once_with(status="FAILED")
