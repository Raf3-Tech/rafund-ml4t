"""MLflow configuration and run utilities for Raf3nd ML4T."""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

import mlflow
from mlflow.exceptions import MlflowException
from mlflow.tracking import MlflowClient
import structlog

logger = structlog.get_logger(__name__)


def configure_mlflow() -> str:
    """Configure MLflow tracking URI and experiment, returning the experiment_id."""
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "file:./mlruns")
    mlflow.set_tracking_uri(tracking_uri)

    experiment_name = os.getenv("MLFLOW_EXPERIMENT", "rafund-ml4t")
    experiment = mlflow.get_experiment_by_name(experiment_name)
    if experiment is None:
        experiment_id = mlflow.create_experiment(experiment_name)
        logger.info(
            "mlflow_experiment_created",
            experiment_name=experiment_name,
            experiment_id=experiment_id,
            tracking_uri=tracking_uri,
        )
    else:
        experiment_id = experiment.experiment_id
        logger.info(
            "mlflow_experiment_loaded",
            experiment_name=experiment_name,
            experiment_id=experiment_id,
            tracking_uri=tracking_uri,
        )

    return experiment_id


@contextmanager
def log_training_run(run_name: str) -> Iterator[mlflow.ActiveRun]:
    """Context manager for MLflow training runs with structured logging."""
    configure_mlflow()
    run = mlflow.start_run(run_name=run_name)
    try:
        yield run
    except Exception as exc:
        try:
            mlflow.set_tag("validation_status", "failed")
            mlflow.end_run(status="FAILED")
        except Exception:
            pass
        logger.error(
            "mlflow_training_run_failed",
            run_id=run.info.run_id,
            run_name=run_name,
            error=str(exc),
        )
        raise
    else:
        logger.info(
            "mlflow_training_run_completed",
            run_id=run.info.run_id,
            run_name=run_name,
            artifact_uri=run.info.artifact_uri,
            status=run.info.status,
        )
        mlflow.end_run(status="FINISHED")


def get_latest_model(model_name: str, stage: str = "Production") -> Optional[mlflow.pyfunc.PythonModel]:
    """Return the latest registered model in the requested stage, or None if none exists."""
    configure_mlflow()
    client = MlflowClient()
    try:
        versions = client.get_latest_versions(name=model_name, stages=[stage])
    except MlflowException as exc:
        logger.error("mlflow_get_latest_versions_failed", model_name=model_name, stage=stage, error=str(exc))
        return None

    if not versions:
        logger.warning("mlflow_no_model_found", model_name=model_name, stage=stage)
        return None

    latest = versions[0]
    model_uri = f"models:/{model_name}/{stage}"
    try:
        model = mlflow.pyfunc.load_model(model_uri)
        logger.info("mlflow_loaded_latest_model", model_name=model_name, stage=stage, version=latest.version)
        return model
    except Exception as exc:
        logger.error("mlflow_load_model_failed", model_name=model_name, stage=stage, error=str(exc))
        return None
