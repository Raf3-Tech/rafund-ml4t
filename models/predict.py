"""Model inference, blocking, and latency tracking for Raf3nd ML4T."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import mlflow
import numpy as np
import pandas as pd
import structlog

from config.mlflow_config import configure_mlflow, get_latest_model
from data.db import DatabaseConnection
from models.blocker import ModelBlocker
from models.exceptions import BlockStatus, ModelBlockedError

logger = structlog.get_logger(__name__)


@dataclass
class BenchmarkResult:
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


class Predictor:
    """Wrapper for making predictions with trained models."""

    def __init__(
        self,
        model_name: str,
        symbol: str,
        db: DatabaseConnection,
        model: Any | None = None,
        blocker: Any | None = None,
    ) -> None:
        self.model_name = model_name
        self.symbol = symbol
        self.db = db
        self.blocker = blocker if blocker is not None else ModelBlocker(db)
        self.model = model
        # Per-instance memo of the MLflow-loaded model. (A class-level
        # lru_cache on a bound method is shared across instances and keeps
        # every Predictor alive for the life of the process.)
        self._model_cache: Any | None = None

    def _load_model(self) -> Any:
        configure_mlflow()
        model = get_latest_model(self.model_name, stage='Production')
        if model is None:
            raise ValueError(f"No production model available for {self.model_name}")
        return model

    def _get_model(self) -> Any:
        if self.model is not None:
            return self.model
        if self._model_cache is None:
            self._model_cache = self._load_model()
        return self._model_cache

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        status = self.blocker.is_blocked(self.model_name, self.symbol)
        if status.blocked:
            logger.warning(
                'prediction_blocked',
                model_name=self.model_name,
                symbol=self.symbol,
                reason=status.reason,
            )
            raise ModelBlockedError(f"Model {self.model_name} blocked: {status.reason}")

        model = self._get_model()
        start = time.perf_counter()
        prediction = model.predict(X)
        latency_ms = (time.perf_counter() - start) * 1000.0

        payload = {
            'event': 'prediction_complete',
            'model_name': self.model_name,
            'symbol': self.symbol,
            'latency_ms': float(latency_ms),
            'feature_count': int(X.shape[1]),
            'prediction': prediction.tolist() if isinstance(prediction, np.ndarray) else str(prediction),
        }
        logger.info(payload)

        if latency_ms > 500.0:
            logger.error('prediction_critical_latency', **payload)
        elif latency_ms > 200.0:
            logger.warning('prediction_slow', **payload)

        return np.asarray(prediction)

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        model = self._get_model()
        if hasattr(model, 'predict_proba'):
            return model.predict_proba(X)
        raise ValueError('Model does not support probability predictions')

    def get_feature_importance(self) -> dict:
        model = self._get_model()
        if hasattr(model, 'feature_importances_'):
            return {'importances': model.feature_importances_}
        if hasattr(model, 'coef_'):
            return {'coefficients': model.coef_}
        return {}


class _PassThroughBlocker:
    """Blocker used only by the explicit offline benchmark: never blocks."""

    def is_blocked(self, model_name: str, symbol: str) -> BlockStatus:
        return BlockStatus(
            blocked=False,
            reason=None,
            model_age_days=None,
            last_drift_severity=None,
            safe_to_trade=True,
        )


def _synthetic_model(seed: int, n_features: int = 10) -> Any:
    """Deterministic in-process model for the offline benchmark."""
    from sklearn.linear_model import LinearRegression

    rng = np.random.default_rng(seed)
    columns = [f'feature_{i}' for i in range(n_features)]
    X = pd.DataFrame(rng.normal(size=(256, n_features)), columns=columns)
    y = X.to_numpy() @ rng.normal(size=n_features)
    return LinearRegression().fit(X, y)


def benchmark_predict(
    model_name: str,
    symbol: str,
    n_runs: int = 100,
    *,
    offline: bool = False,
    seed: int = 0,
) -> dict:
    """Benchmark single-row prediction latency through ``Predictor.predict``.

    ``offline=False`` (default) needs a live DB (block gate) and an MLflow
    Production model; any failure there is raised, never papered over.
    ``offline=True`` must be requested explicitly: it uses a synthetic sklearn
    model and a pass-through blocker, touching neither the DB nor MLflow, so it
    measures the predict path's overhead, not a production model.
    """
    db = None
    try:
        if offline:
            logger.warning('benchmark_offline_mode', model_name=model_name, symbol=symbol)
            predictor = Predictor(
                model_name=model_name,
                symbol=symbol,
                db=None,
                model=_synthetic_model(seed),
                blocker=_PassThroughBlocker(),
            )
        else:
            db = DatabaseConnection()
            predictor = Predictor(model_name=model_name, symbol=symbol, db=db)
        model = predictor._get_model()
        feature_names = _infer_feature_names(model)
        rng = np.random.default_rng(seed)
        dummy = pd.DataFrame(rng.normal(size=(1, len(feature_names))), columns=feature_names)

        latencies = []
        for _ in range(n_runs):
            start = time.perf_counter()
            predictor.predict(dummy)
            latencies.append((time.perf_counter() - start) * 1000.0)
    finally:
        if db is not None:
            db.close_pool()

    result = {
        'mean_ms': float(np.mean(latencies)),
        'p50_ms': float(np.percentile(latencies, 50)),
        'p95_ms': float(np.percentile(latencies, 95)),
        'p99_ms': float(np.percentile(latencies, 99)),
        'max_ms': float(np.max(latencies)),
    }

    print(f"Prediction latency benchmark (mode={'offline' if offline else 'online'}, runs={n_runs}):")
    print('mean_ms  p50_ms  p95_ms  p99_ms  max_ms')
    print(f"{result['mean_ms']:.2f}   {result['p50_ms']:.2f}   {result['p95_ms']:.2f}   {result['p99_ms']:.2f}   {result['max_ms']:.2f}")
    return result


def _infer_feature_names(model: Any) -> list[str]:
    try:
        signature = getattr(model, 'metadata', None)
        if signature is not None and signature.signature is not None and signature.signature.inputs:
            return [inp.name for inp in signature.signature.inputs]
    except Exception:
        pass

    if hasattr(model, 'predict') and hasattr(model, 'feature_names_in_'):
        names = getattr(model, 'feature_names_in_', None)
        if names is not None:
            return list(names)

    return [f'feature_{i}' for i in range(10)]
