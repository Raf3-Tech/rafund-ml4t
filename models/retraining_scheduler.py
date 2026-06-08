"""Automated model retraining and drift scheduling for Raf3nd ML4T."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import structlog
from apscheduler.schedulers.background import BackgroundScheduler

from config.mlflow_config import configure_mlflow
from data.db import DatabaseConnection
from monitoring.drift_detector import DriftReport, FeatureDriftDetector
from models.blocker import ModelBlocker
from models.train import ModelTrainer
from models.validator import ModelValidator

logger = structlog.get_logger(__name__)


@dataclass
class RetrainResult:
    """Outcome of retraining + validating a single (model_type, symbol) pair."""

    model_type: str
    symbol: str
    success: bool
    promoted: bool
    run_id: Optional[str]
    model_name: Optional[str]
    reason: Optional[str]
    error: Optional[str]


class RetrainingScheduler:
    def __init__(self, db: DatabaseConnection, config: Dict[str, Any]) -> None:
        self.db = db
        self.config = config.get('retraining', {})
        self.jobs_config = self.config
        self.scheduler = BackgroundScheduler(timezone='UTC')

    def setup_jobs(self) -> None:
        schedule_cron = self.jobs_config.get('schedule_cron', '0 2 * * 0')
        drift_minutes = int(self.jobs_config.get('drift_check_interval_minutes', 60))

        self.scheduler.add_job(
            self.retrain_all_models,
            trigger='cron',
            minute=schedule_cron.split()[0],
            hour=schedule_cron.split()[1],
            day_of_week=schedule_cron.split()[4],
            id='retrain_all_models',
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.check_drift,
            trigger='interval',
            minutes=drift_minutes,
            id='check_drift',
            replace_existing=True,
        )
        self.scheduler.add_job(
            self._health_check,
            trigger='interval',
            minutes=5,
            id='health_check',
            replace_existing=True,
        )

        logger.info('scheduler_jobs_registered', jobs=[job.id for job in self.scheduler.get_jobs()])

    def retrain_all_models(self) -> None:
        self.run_cycle()

    def run_cycle(self) -> List[RetrainResult]:
        """Synchronously retrain and validate every configured (model, symbol) pair.

        Unlike the scheduled ``retrain_all_models`` entrypoint, this returns a
        structured per-pair summary so the cycle can be invoked and inspected
        on demand (e.g. via :func:`run_retraining_cycle`).
        """
        configure_mlflow()
        models = list(self.jobs_config.get('models', []))
        symbols = list(self.jobs_config.get('symbols', []))
        if not models or not symbols:
            logger.warning('retraining_config_missing', models=models, symbols=symbols)
            return []

        results: List[RetrainResult] = []
        for model_type in models:
            for symbol in symbols:
                results.append(self._retrain_one(model_type, symbol))
        return results

    def _retrain_one(self, model_type: str, symbol: str) -> RetrainResult:
        try:
            trainer = ModelTrainer(self.db)
            meta = trainer.train(model_type, symbol)
            logger.info('retraining_completed', model_name=meta.model_name, symbol=symbol, run_id=meta.run_id)

            from backtesting.splitter import TimeSeriesSplitter
            splitter_config = self.config.get('walk_forward', {})
            splitter = TimeSeriesSplitter(**splitter_config) if splitter_config else TimeSeriesSplitter()

            # The validator forwards this to WalkForwardRunner -> BacktestEngine(**engine_config),
            # so it must contain ONLY BacktestEngine kwargs. Pass the dedicated 'engine' sub-config
            # (empty -> BacktestEngine defaults), never the whole retraining config, whose
            # models/symbols/timeframe keys are not valid engine kwargs.
            engine_config = self.config.get('engine', {})
            validator = ModelValidator(self.db, splitter, engine_config)
            decision = validator.validate_for_promotion(meta, symbol, self.config.get('timeframe', '1d'))
            if decision.approved:
                logger.info('model_promoted_to_production', model_name=meta.model_name, symbol=symbol)
            else:
                logger.warning('model_rejected_retained', model_name=meta.model_name, symbol=symbol, reason=decision.reason)

            return RetrainResult(
                model_type=model_type,
                symbol=symbol,
                success=True,
                promoted=decision.approved,
                run_id=meta.run_id,
                model_name=meta.model_name,
                reason=decision.reason,
                error=None,
            )
        except Exception as exc:
            logger.error('retraining_job_failed', model_type=model_type, symbol=symbol, error=str(exc))
            return RetrainResult(
                model_type=model_type,
                symbol=symbol,
                success=False,
                promoted=False,
                run_id=None,
                model_name=None,
                reason=None,
                error=str(exc),
            )

    def check_drift(self) -> None:
        configure_mlflow()
        blocker = ModelBlocker(self.db)
        production_models = self.db.get_production_models()
        for entry in production_models:
            try:
                model_name = entry.get('model_name')
                symbol = entry.get('symbol')
                feature_names = entry.get('feature_names')
                if isinstance(feature_names, str):
                    feature_names = json.loads(feature_names)

                detector = FeatureDriftDetector(model_name=model_name)
                reference_df = detector.load_reference_distribution(
                    self.db,
                    symbol,
                    self.config.get('timeframe', '1d'),
                    feature_names,
                    date.today(),
                )
                current_df = self._load_current_distribution(symbol, feature_names)
                if reference_df.empty or current_df.empty:
                    logger.warning('drift_data_insufficient', model_name=model_name, symbol=symbol)
                    continue

                report = detector.detect(symbol, reference_df, current_df)
                detector.save_report(report, self.db)

                if report.recommended_action == 'halt_trading':
                    blocker.block(model_name, symbol, reason='drift')
                    logger.error('drift_halt_triggered', model_name=model_name, symbol=symbol, severity=report.severity)
            except Exception as exc:
                logger.error('drift_check_failed', error=str(exc), entry=entry)

    def start(self) -> None:
        self.setup_jobs()
        self.scheduler.start()
        logger.info('retraining_scheduler_started', jobs=[job.id for job in self.scheduler.get_jobs()])

    def _load_current_distribution(self, symbol: str, feature_names: list[str]) -> pd.DataFrame:
        detector = FeatureDriftDetector(model_name='scheduler')
        return detector.load_current_distribution(self.db, symbol, feature_names)

    def _health_check(self) -> None:
        logger.info('scheduler_alive', timestamp=datetime.now(timezone.utc).isoformat())


def run_retraining_cycle(db: DatabaseConnection, config: Dict[str, Any]) -> List[RetrainResult]:
    """Run a single retraining + validation cycle immediately.

    Convenience entrypoint over :class:`RetrainingScheduler`: it executes the
    full cycle synchronously (no background scheduler is started) and returns a
    per-pair :class:`RetrainResult` summary.
    """
    return RetrainingScheduler(db, config).run_cycle()


def run_drift_check(
    db: DatabaseConnection,
    model: Any,
    symbol: str,
    *,
    timeframe: str = '1d',
    reference_window_days: int = 90,
    block_on_critical: bool = False,
) -> Optional[DriftReport]:
    """Run a single feature-drift check for one production model and symbol.

    ``model`` may be a model-name string or a model-registry entry dict (as
    returned by :meth:`DatabaseConnection.get_production_models`). The reference
    and current feature distributions are loaded and compared, the resulting
    report is persisted, and — when ``block_on_critical`` is set — a critical
    report blocks the model from trading. Returns the :class:`DriftReport`, or
    ``None`` when there is insufficient data to evaluate drift.
    """
    if isinstance(model, dict):
        entry: Optional[Dict[str, Any]] = model
        model_name = entry.get('model_name')
    else:
        model_name = model
        entry = db.get_active_production_model(model_name, symbol)

    if not model_name:
        logger.warning('drift_check_model_unresolved', model=model, symbol=symbol)
        return None
    if entry is None:
        logger.warning('drift_check_model_not_registered', model_name=model_name, symbol=symbol)
        return None

    feature_names = entry.get('feature_names')
    if isinstance(feature_names, str):
        feature_names = json.loads(feature_names)
    if not feature_names:
        logger.warning('drift_check_features_missing', model_name=model_name, symbol=symbol)
        return None

    detector = FeatureDriftDetector(
        model_name=model_name,
        reference_window_days=reference_window_days,
    )
    reference_df = detector.load_reference_distribution(
        db, symbol, timeframe, feature_names, date.today()
    )
    current_df = detector.load_current_distribution(db, symbol, feature_names)
    if reference_df.empty or current_df.empty:
        logger.warning('drift_data_insufficient', model_name=model_name, symbol=symbol)
        return None

    report = detector.detect(symbol, reference_df, current_df)
    detector.save_report(report, db)

    if block_on_critical and report.recommended_action == 'halt_trading':
        ModelBlocker(db).block(model_name, symbol, reason='drift')
        logger.error('drift_halt_triggered', model_name=model_name, symbol=symbol, severity=report.severity)

    return report
