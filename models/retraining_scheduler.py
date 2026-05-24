"""Automated model retraining and drift scheduling for RAFund ML4T."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

import pandas as pd
import structlog
from apscheduler.schedulers.background import BackgroundScheduler

from config.mlflow_config import configure_mlflow
from data.db import DatabaseConnection
from monitoring.drift_detector import FeatureDriftDetector
from models.blocker import ModelBlocker
from models.train import ModelTrainer
from models.validator import ModelValidator

logger = structlog.get_logger(__name__)


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
        configure_mlflow()
        models = list(self.jobs_config.get('models', []))
        symbols = list(self.jobs_config.get('symbols', []))
        if not models or not symbols:
            logger.warning('retraining_config_missing', models=models, symbols=symbols)
            return

        for model_type in models:
            for symbol in symbols:
                try:
                    trainer = ModelTrainer(self.db)
                    meta = trainer.train(model_type, symbol)
                    logger.info('retraining_completed', model_name=meta.model_name, symbol=symbol, run_id=meta.run_id)

                    splitter_config = self.config.get('walk_forward', {})
                    if splitter_config:
                        from backtesting.splitter import TimeSeriesSplitter
                        splitter = TimeSeriesSplitter(**splitter_config)
                    else:
                        from backtesting.splitter import TimeSeriesSplitter
                        splitter = TimeSeriesSplitter()

                    validator = ModelValidator(self.db, splitter, self.config)
                    decision = validator.validate_for_promotion(meta, symbol, self.config.get('timeframe', '1d'))
                    if decision.approved:
                        logger.info('model_promoted_to_production', model_name=meta.model_name, symbol=symbol)
                    else:
                        logger.warning('model_rejected_retained', model_name=meta.model_name, symbol=symbol, reason=decision.reason)
                except Exception as exc:
                    logger.error('retraining_job_failed', model_type=model_type, symbol=symbol, error=str(exc))

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
        cutoff = datetime.utcnow() - timedelta(hours=24)
        columns = ', '.join(feature_names)
        query = (
            f"SELECT timestamp, {columns} FROM features "
            "WHERE (symbol_a = %s OR symbol_b = %s) "
            "AND timestamp >= %s ORDER BY timestamp"
        )
        conn = self.db.get_connection()
        try:
            df = pd.read_sql(query, conn, params=[symbol, symbol, cutoff])
        finally:
            self.db.return_connection(conn)
        if df.empty:
            return pd.DataFrame(columns=feature_names)
        return df[feature_names].copy()

    def _health_check(self) -> None:
        logger.info('scheduler_alive', timestamp=datetime.utcnow().isoformat())
