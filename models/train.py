"""ML model training with MLflow integration for RAFund ML4T."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
import structlog
from mlflow.entities.model_registry import ModelVersion
from mlflow.tracking import MlflowClient
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_squared_error

from config.loader import get_settings
from config.mlflow_config import configure_mlflow, log_training_run
from data.db import DatabaseConnection

logger = structlog.get_logger(__name__)


@dataclass
class ModelMetadata:
    run_id: str
    model_name: str
    model_version: int
    symbol: str
    trained_at: datetime
    train_start: date
    train_end: date
    in_sample_sharpe: float
    feature_names: list[str]
    stage: str


class ModelTrainer:
    def __init__(self, db: DatabaseConnection, settings: Optional[Any] = None) -> None:
        self.db = db
        self.settings = settings or get_settings()

    def train(self, model_type: str, symbol: str) -> ModelMetadata:
        safe_symbol = symbol.replace('/', '_').replace(' ', '_')
        model_name = f"rafund_{model_type}_{safe_symbol}"
        run_name = f"{model_type}_{safe_symbol}_{date.today().isoformat()}"

        training_df = self._load_training_data(symbol)
        if training_df.empty:
            raise ValueError(f"No training data found for symbol: {symbol}")

        feature_names = self._resolve_feature_names(training_df)
        X = training_df[feature_names]
        y = training_df['target']

        X_train, X_test, y_train, y_test = self._time_series_split(X, y)
        model = self._build_model(model_type)
        model.fit(X_train, y_train)

        preds_train = model.predict(X_train)
        preds_test = model.predict(X_test)

        in_sample_sharpe = self._compute_sharpe(y_train, preds_train)
        train_loss = float(mean_squared_error(y_train, preds_train))
        validation_loss = float(mean_squared_error(y_test, preds_test))
        in_sample_drawdown = float(self._compute_max_drawdown(y_train, preds_train) * 100)

        artifact_dir = Path('tmp')
        artifact_dir.mkdir(exist_ok=True)
        features_json_path = artifact_dir / f"{model_name}_features.json"
        with open(features_json_path, 'w', encoding='utf-8') as f:
            json.dump(feature_names, f)

        run_id = ""
        model_version = 0
        trained_at = datetime.utcnow()

        with log_training_run(run_name) as run:
            run_id = run.info.run_id
            mlflow.set_tag('phase', '3')
            mlflow.set_tag('strategy', model_type)
            mlflow.set_tag('symbol', symbol)
            mlflow.log_params({
                'model_type': model_type,
                'symbol': symbol,
                'timeframe': self.settings.timeframe,
                'train_start_date': str(training_df.index.min().date()),
                'train_end_date': str(training_df.index.max().date()),
                'feature_count': len(feature_names),
                'training_rows': len(training_df),
            })
            mlflow.log_metrics({
                'in_sample_sharpe': float(in_sample_sharpe),
                'in_sample_max_drawdown': float(in_sample_drawdown),
                'train_loss': train_loss,
                'validation_loss': validation_loss,
            })
            mlflow.log_artifact(str(features_json_path), artifact_path='artifacts')

            importance_artifact = self._save_feature_importance(model, feature_names, model_name, artifact_dir)
            if importance_artifact is not None:
                mlflow.log_artifact(str(importance_artifact), artifact_path='artifacts')

            mlflow.sklearn.log_model(model, artifact_path='model')
            version = self._register_model(model_name, run_id)
            model_version = int(version.version)
            mlflow.set_tag('mlflow_model_version', str(model_version))
            mlflow.set_tag('stage', 'Staging')
            trained_at = datetime.utcnow()

        logger.info(
            'model_training_completed',
            model_name=model_name,
            symbol=symbol,
            run_id=run_id,
            model_version=model_version,
            in_sample_sharpe=in_sample_sharpe,
            train_loss=train_loss,
            validation_loss=validation_loss,
        )

        return ModelMetadata(
            run_id=run_id,
            model_name=model_name,
            model_version=model_version,
            symbol=symbol,
            trained_at=trained_at,
            train_start=training_df.index.min().date(),
            train_end=training_df.index.max().date(),
            in_sample_sharpe=float(in_sample_sharpe),
            feature_names=feature_names,
            stage='Staging',
        )

    def _load_training_data(self, symbol: str) -> pd.DataFrame:
        query = (
            "SELECT timestamp, symbol_a, symbol_b, spread, spread_mean, spread_std, z_score, hedge_ratio "
            "FROM features WHERE symbol_a = %s OR symbol_b = %s ORDER BY timestamp"
        )
        conn = self.db.get_connection()
        try:
            df = pd.read_sql(query, conn, params=[symbol, symbol])
        finally:
            self.db.return_connection(conn)

        if df.empty:
            return pd.DataFrame()

        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        df = df.set_index('timestamp')
        df = df.dropna(subset=['z_score', 'spread', 'spread_mean', 'spread_std', 'hedge_ratio'])
        if df.empty:
            return pd.DataFrame()

        df['target'] = df['z_score'].shift(-1)
        df = df.dropna(subset=['target'])
        return df

    def _resolve_feature_names(self, df: pd.DataFrame) -> list[str]:
        candidates = ['spread', 'spread_mean', 'spread_std', 'z_score', 'hedge_ratio']
        return [col for col in candidates if col in df.columns]

    def _time_series_split(
        self, X: pd.DataFrame, y: pd.Series
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
        split_idx = int(len(X) * 0.8)
        return X.iloc[:split_idx], X.iloc[split_idx:], y.iloc[:split_idx], y.iloc[split_idx:]

    def _build_model(self, model_type: str) -> Any:
        if model_type == 'stat_arb':
            return RandomForestRegressor(n_estimators=50, random_state=42)
        return LinearRegression()

    def _compute_sharpe(self, y_true: pd.Series, y_pred: np.ndarray) -> float:
        returns = np.sign(y_pred) * y_true.values
        if len(returns) < 2:
            return 0.0
        mean = float(np.mean(returns))
        std = float(np.std(returns, ddof=1))
        return float((mean / std * np.sqrt(252)) if std > 0 else 0.0)

    def _compute_max_drawdown(self, y_true: pd.Series, y_pred: np.ndarray) -> float:
        returns = np.sign(y_pred) * y_true.values
        equity = np.cumsum(returns)
        peak = np.maximum.accumulate(equity)
        drawdown = peak - equity
        return float(np.max(drawdown) if len(drawdown) else 0.0)

    def _save_feature_importance(
        self, model: Any, feature_names: list[str], model_name: str, artifact_dir: Path
    ) -> Optional[Path]:
        importance_path = artifact_dir / f"{model_name}_feature_importance.png"
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            logger.warning('matplotlib_not_installed', model_name=model_name)
            return None

        importance_values = None
        if hasattr(model, 'feature_importances_'):
            importance_values = np.asarray(model.feature_importances_)
        elif hasattr(model, 'coef_'):
            importance_values = np.abs(np.asarray(model.coef_)).flatten()

        if importance_values is None or len(importance_values) != len(feature_names):
            logger.warning('feature_importance_not_available', model_name=model_name)
            return None

        fig, ax = plt.subplots(figsize=(8, 4))
        ax.bar(feature_names, importance_values)
        ax.set_title('Feature Importance')
        ax.set_ylabel('Importance')
        ax.set_xlabel('Feature')
        plt.xticks(rotation=45, ha='right')
        plt.tight_layout()
        fig.savefig(importance_path, dpi=120)
        plt.close(fig)
        return importance_path

    def _register_model(self, model_name: str, run_id: str) -> ModelVersion:
        client = MlflowClient()
        model_uri = f"runs:/{run_id}/model"
        if not self._model_exists(client, model_name):
            client.create_registered_model(model_name)
        version = client.create_model_version(name=model_name, source=model_uri, run_id=run_id)
        client.transition_model_version_stage(
            name=model_name,
            version=version.version,
            stage='Staging',
            archive_existing_versions=False,
        )
        logger.info('mlflow_model_registered', model_name=model_name, version=version.version)
        return version

    def _model_exists(self, client: MlflowClient, model_name: str) -> bool:
        try:
            client.get_registered_model(model_name)
            return True
        except Exception:
            return False
