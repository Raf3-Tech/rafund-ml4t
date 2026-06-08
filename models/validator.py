"""Model validation and promotion logic for Raf3nd ML4T."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import mlflow
import pandas as pd
import structlog
from mlflow.pyfunc import PyFuncModel
from mlflow.tracking import MlflowClient

from backtesting.significance import SignificanceResult, test_strategy_significance
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
    significant: Optional[bool] = None
    robust_significant: Optional[bool] = None
    p_value: Optional[float] = None


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
        self._client = None

    @property
    def client(self) -> MlflowClient:
        if self._client is None:
            self._client = MlflowClient()
        return self._client

    def validate_for_promotion(
        self,
        candidate_meta: ModelMetadata,
        symbol: str,
        timeframe: str,
    ) -> ValidationDecision:
        model = mlflow.pyfunc.load_model(f"runs:/{candidate_meta.run_id}/model")
        # LEAKAGE FIX: the validation window must strictly post-date the model's
        # training window, otherwise the "out-of-sample" Sharpe is computed partly
        # on data the model was fit on. We pass train_end and drop any overlap.
        validation_df = self._load_validation_data(symbol, candidate_meta.train_end)
        if validation_df.empty:
            reason = "No validation data available (or none post-dating training window)"
            logger.warning(reason, symbol=symbol)
            return ValidationDecision(False, reason, 0.0, 0.0, None, False, candidate_meta.run_id, datetime.now(timezone.utc))

        strategy = CandidateModelStrategy(model, candidate_meta.feature_names)
        runner = WalkForwardRunner(strategy, self.splitter, self.engine_config)
        walk_result = runner.run(validation_df)

        candidate_sharpe = walk_result.aggregate.mean_oos_sharpe
        candidate_drawdown = walk_result.aggregate.worst_oos_drawdown

        # Statistical-significance gate: pooled OOS fold returns must be
        # distinguishable from luck. Skipped only when no per-fold returns are
        # available (e.g. degenerate runs), in which case it does not block.
        significance = self._run_significance(walk_result)
        significance_ok = significance is None or significance.significant

        incumbent = self.db.get_active_production_model(candidate_meta.model_name, symbol)
        incumbent_sharpe = float(incumbent.get('oos_sharpe')) if incumbent and incumbent.get('oos_sharpe') is not None else None
        regression_detected = False
        approved = False
        reason = ''

        perf_gate = candidate_sharpe >= 1.0 and candidate_drawdown <= 6.0
        if not perf_gate:
            reason = (
                f"Gate failed: sharpe={candidate_sharpe:.2f} < 1.0 or drawdown={candidate_drawdown:.2f}% > 6%"
            )
        elif not significance_ok:
            reason = (
                f"Significance gate failed: p={significance.p_value:.3f}, "
                f"perm_p={significance.permutation_p_value:.3f}, "
                f"bootstrap_p={significance.stationary_bootstrap_p_value:.3f}"
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
                promoted_at=datetime.now(timezone.utc),
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
            validated_at=datetime.now(timezone.utc),
            significant=(significance.significant if significance else None),
            robust_significant=(significance.robust_significant if significance else None),
            p_value=(significance.p_value if significance else None),
        )

    @staticmethod
    def _fold_returns(walk_result: Any) -> List[pd.Series]:
        """Extract one OOS daily-return series per fold for the significance test.

        Robust to mocked/degenerate results: returns an empty list when no fold
        equity curves are present, in which case significance is skipped.
        """
        series: List[pd.Series] = []
        for fold in getattr(walk_result, "folds", None) or []:
            curve = getattr(fold, "equity_curve", None) or []
            if not curve:
                continue
            returns = pd.Series(
                [row.get("daily_return", 0.0) for row in curve], dtype="float64"
            ).dropna()
            if not returns.empty:
                series.append(returns)
        return series

    def _run_significance(self, walk_result: Any) -> Optional[SignificanceResult]:
        fold_returns = self._fold_returns(walk_result)
        if not fold_returns:
            return None
        try:
            return test_strategy_significance(fold_returns, seed=42)
        except ValueError:
            return None

    def promote(self, candidate_meta: ModelMetadata, symbol: str, timeframe: str) -> ValidationDecision:
        decision = self.validate_for_promotion(candidate_meta, symbol, timeframe)
        if not decision.approved:
            raise ModelRejectedError(decision.reason)
        return decision

    @staticmethod
    def _drop_rows_on_or_before(df: pd.DataFrame, train_end: Any) -> pd.DataFrame:
        """Keep only rows whose timestamp strictly post-dates ``train_end``.

        Guarantees the validation set is disjoint from the model's training
        window. ``train_end`` may be a date/datetime/Timestamp or None (no-op).
        """
        if train_end is None or df.empty or 'timestamp' not in df.columns:
            return df
        cutoff = pd.Timestamp(train_end)
        if cutoff.tzinfo is None:
            cutoff = cutoff.tz_localize('UTC')
        ts = pd.to_datetime(df['timestamp'], utc=True)
        return df[ts > cutoff].reset_index(drop=True)

    def _load_validation_data(self, symbol: str, train_end: Any = None) -> pd.DataFrame:
        query = (
            "SELECT f.timestamp, f.symbol_a, f.symbol_b, f.spread, f.spread_mean, "
            "f.spread_std, f.z_score, f.hedge_ratio, p_a.close AS price_a, p_b.close AS price_b "
            "FROM features f "
            "JOIN prices p_a ON p_a.symbol = f.symbol_a AND p_a.timestamp = f.timestamp "
            "JOIN prices p_b ON p_b.symbol = f.symbol_b AND p_b.timestamp = f.timestamp "
            "WHERE (f.symbol_a = %s OR f.symbol_b = %s) "
            # 24-month window: daily ('1d') data over 6 months is only ~170 rows
            # (~5.7 months span), too short for the walk-forward splitter to emit
            # any fold. 24 months gives enough span for multiple OOS folds while
            # staying recent (data goes back to 2019).
            "AND f.timestamp >= NOW() - INTERVAL '24 months' "
            "ORDER BY f.timestamp"
        )
        df = self.db.read_sql(query, [symbol, symbol])

        if df.empty:
            return pd.DataFrame()

        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        df = df.sort_values('timestamp').reset_index(drop=True)
        # Enforce train/validation disjointness (no leakage across the boundary).
        df = self._drop_rows_on_or_before(df, train_end)
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
