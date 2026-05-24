"""Model validation and promotion logic for RAFund ML4T."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import mlflow
import pandas as pd
import structlog
from mlflow.pyfunc import PyFuncModel
from mlflow.tracking import MlflowClient

from backtesting.splitter import TimeSeriesSplitter
from backtesting.walk_forward import WalkForwardRunner
from data.db import DatabaseConnection
from models.train import ModelMetadata
from models.exceptions import ModelRejectedError

logger = structlog.get_logger(__name__)


@dataclass
class ValidationDecision:
    approved: bool
    reason: str
    candidate_sharpe: float
    candidate_drawdown: float
    incumbent_sharpe: Optional[float]
    regression_detected: bool
    run_id: str
    validated_at: datetime


class CandidateModelStrategy:
    def __init__(self, model: PyFuncModel, feature_names: List[str]) -> None:
        self.model = model
        self.feature_names = feature_names

    def prepare(self, train_df: pd.DataFrame) -> None:
        return None

    def generate_signals(self, test_df: pd.DataFrame) -> pd.DataFrame:
        X = test_df[self.feature_names].copy()
        predictions = self.model.predict(X)
        signal = []
        positions_a = []
        positions_b = []
        for idx, pred in enumerate(predictions):
            side = 0
            if pred > 0:
                side = 1
            elif pred < 0:
                side = -1
            signal.append('BUY' if side > 0 else 'SELL' if side < 0 else 'HOLD')
            hedge_ratio = float(test_df.iloc[idx]['hedge_ratio']) if 'hedge_ratio' in test_df.columns else 1.0
            positions_a.append(float(side))
            positions_b.append(float(-side * hedge_ratio))

        result = test_df[['timestamp']].copy()
        result['signal'] = signal
        result['position_a'] = positions_a
        result['position_b'] = positions_b
        return result


class ModelValidator:
    def __init__(
        self,
        db: DatabaseConnection,
        splitter: TimeSeriesSplitter,
        engine_config: Dict[str, Any],
    ) -> None:
        self.db = db
        self.splitter = splitter
        self.engine_config = engine_config
        self.client = MlflowClient()

    def validate_for_promotion(
        self,
        candidate_meta: ModelMetadata,
        symbol: str,
        timeframe: str,
    ) -> ValidationDecision:
        model = mlflow.pyfunc.load_model(f"runs:/{candidate_meta.run_id}/model")
        validation_df = self._load_validation_data(symbol)
        if validation_df.empty:
            reason = "No validation data available"
            logger.warning(reason, symbol=symbol)
            return ValidationDecision(False, reason, 0.0, 0.0, None, False, candidate_meta.run_id, datetime.utcnow())

        strategy = CandidateModelStrategy(model, candidate_meta.feature_names)
        runner = WalkForwardRunner(strategy, self.splitter, self.engine_config)
        walk_result = runner.run(validation_df)

        candidate_sharpe = walk_result.aggregate.mean_oos_sharpe
        candidate_drawdown = walk_result.aggregate.worst_oos_drawdown

        incumbent = self.db.get_active_production_model(candidate_meta.model_name, symbol)
        incumbent_sharpe = float(incumbent.get('oos_sharpe')) if incumbent and incumbent.get('oos_sharpe') is not None else None
        regression_detected = False
        approved = False
        reason = ''

        gate_pass = candidate_sharpe >= 1.0 and candidate_drawdown <= 6.0
        if not gate_pass:
            reason = (
                f"Gate failed: sharpe={candidate_sharpe:.2f} < 1.0 or drawdown={candidate_drawdown:.2f}% > 6%"
            )
        elif incumbent_sharpe is not None:
            regression_detected = candidate_sharpe < incumbent_sharpe * 0.85
            if regression_detected:
                reason = (
                    f"Regression guard failed: candidate_sharpe={candidate_sharpe:.2f} < "
                    f"85% of incumbent_sharpe={incumbent_sharpe:.2f}"
                )
            else:
                reason = "Candidate passed gate and regression guard"
                approved = True
        else:
            reason = "Candidate passed gate with no incumbent"
            approved = True

        if approved:
            self._promote_candidate(candidate_meta.model_name, candidate_meta.model_version, incumbent)
            self.db.save_model_registry_entry(
                model_name=candidate_meta.model_name,
                mlflow_run_id=candidate_meta.run_id,
                mlflow_version=candidate_meta.model_version,
                symbol=symbol,
                stage='Production',
                trained_at=candidate_meta.trained_at,
                promoted_at=datetime.utcnow(),
                in_sample_sharpe=candidate_meta.in_sample_sharpe,
                oos_sharpe=candidate_sharpe,
                feature_names=candidate_meta.feature_names,
                is_active=True,
            )
        else:
            self.db.save_model_registry_entry(
                model_name=candidate_meta.model_name,
                mlflow_run_id=candidate_meta.run_id,
                mlflow_version=candidate_meta.model_version,
                symbol=symbol,
                stage='Staging',
                trained_at=candidate_meta.trained_at,
                promoted_at=None,
                in_sample_sharpe=candidate_meta.in_sample_sharpe,
                oos_sharpe=candidate_sharpe,
                feature_names=candidate_meta.feature_names,
                is_active=False,
            )

        self._tag_run(candidate_meta.run_id, approved, candidate_sharpe, candidate_drawdown)
        logger.info(
            'model_validation_completed',
            model_name=candidate_meta.model_name,
            symbol=symbol,
            approved=approved,
            candidate_sharpe=candidate_sharpe,
            candidate_drawdown=candidate_drawdown,
            incumbent_sharpe=incumbent_sharpe,
            regression_detected=regression_detected,
            reason=reason,
        )

        return ValidationDecision(
            approved=approved,
            reason=reason,
            candidate_sharpe=candidate_sharpe,
            candidate_drawdown=candidate_drawdown,
            incumbent_sharpe=incumbent_sharpe,
            regression_detected=regression_detected,
            run_id=candidate_meta.run_id,
            validated_at=datetime.utcnow(),
        )

    def promote(self, candidate_meta: ModelMetadata, symbol: str, timeframe: str) -> ValidationDecision:
        decision = self.validate_for_promotion(candidate_meta, symbol, timeframe)
        if not decision.approved:
            raise ModelRejectedError(decision.reason)
        return decision

    def _load_validation_data(self, symbol: str) -> pd.DataFrame:
        query = (
            "SELECT f.timestamp, f.symbol_a, f.symbol_b, f.spread, f.spread_mean, "
            "f.spread_std, f.z_score, f.hedge_ratio, p_a.close AS price_a, p_b.close AS price_b "
            "FROM features f "
            "JOIN prices p_a ON p_a.symbol = f.symbol_a AND p_a.timestamp = f.timestamp "
            "JOIN prices p_b ON p_b.symbol = f.symbol_b AND p_b.timestamp = f.timestamp "
            "WHERE (f.symbol_a = %s OR f.symbol_b = %s) "
            "AND f.timestamp >= NOW() - INTERVAL '6 months' "
            "ORDER BY f.timestamp"
        )
        conn = self.db.get_connection()
        try:
            df = pd.read_sql(query, conn, params=[symbol, symbol])
        finally:
            self.db.return_connection(conn)

        if df.empty:
            return pd.DataFrame()

        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        df = df.sort_values('timestamp').reset_index(drop=True)
        return df

    def _promote_candidate(self, model_name: str, version: int, incumbent: Optional[Dict[str, Any]]) -> None:
        if incumbent and incumbent.get('mlflow_version') is not None:
            try:
                self.client.transition_model_version_stage(
                    name=model_name,
                    version=int(incumbent['mlflow_version']),
                    stage='Archived',
                    archive_existing_versions=False,
                )
                logger.info('mlflow_incumbent_archived', model_name=model_name, version=incumbent['mlflow_version'])
            except Exception as exc:
                logger.warning('mlflow_archive_incumbent_failed', error=str(exc), model_name=model_name)

        self.client.transition_model_version_stage(
            name=model_name,
            version=version,
            stage='Production',
            archive_existing_versions=False,
        )
        logger.info('mlflow_candidate_promoted', model_name=model_name, version=version)

    def _tag_run(self, run_id: str, approved: bool, sharpe: float, drawdown: float) -> None:
        status = 'approved' if approved else 'rejected'
        try:
            self.client.set_tag(run_id, 'validation_status', status)
            self.client.log_metric(run_id, 'candidate_sharpe', float(sharpe))
            self.client.log_metric(run_id, 'candidate_drawdown', float(drawdown))
        except Exception as exc:
            logger.warning('mlflow_tagging_failed', error=str(exc), run_id=run_id)
