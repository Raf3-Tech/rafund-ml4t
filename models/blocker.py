"""Model blocking and stale/drift gate enforcement for RAFund ML4T."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog

from data.db import DatabaseConnection
from models.exceptions import BlockStatus

logger = structlog.get_logger(__name__)


class ModelBlocker:
    def __init__(self, db: DatabaseConnection, max_model_age_days: int = 30) -> None:
        self.db = db
        self.max_model_age_days = max_model_age_days

    def is_blocked(self, model_name: str, symbol: str) -> BlockStatus:
        active_model = self.db.get_active_production_model(model_name, symbol)
        if active_model is None:
            return BlockStatus(
                blocked=True,
                reason='not_registered',
                model_age_days=None,
                last_drift_severity=None,
                safe_to_trade=False,
            )

        trained_at = active_model.get('trained_at')
        if isinstance(trained_at, str):
            trained_at = datetime.fromisoformat(trained_at)
        if trained_at and trained_at.tzinfo is None:
            trained_at = trained_at.replace(tzinfo=timezone.utc)

        age_days = int((datetime.now(timezone.utc) - trained_at).days) if trained_at else None
        if age_days is not None and age_days > self.max_model_age_days:
            return BlockStatus(
                blocked=True,
                reason='stale',
                model_age_days=age_days,
                last_drift_severity=None,
                safe_to_trade=False,
            )

        since = datetime.now(timezone.utc) - timedelta(hours=2)
        drift_report = self.db.get_recent_drift_report(model_name, symbol, since)
        last_drift_severity = drift_report.get('severity') if drift_report else None
        if last_drift_severity == 'critical':
            return BlockStatus(
                blocked=True,
                reason='drift',
                model_age_days=age_days,
                last_drift_severity=last_drift_severity,
                safe_to_trade=False,
            )

        if active_model.get('stage') != 'Production':
            return BlockStatus(
                blocked=True,
                reason='not_production',
                model_age_days=age_days,
                last_drift_severity=last_drift_severity,
                safe_to_trade=False,
            )

        return BlockStatus(
            blocked=False,
            reason=None,
            model_age_days=age_days,
            last_drift_severity=last_drift_severity,
            safe_to_trade=True,
        )

    def block(self, model_name: str, symbol: str, reason: str) -> None:
        if self.db.block_model(model_name, symbol, reason):
            logger.error(
                'model_blocked',
                model_name=model_name,
                symbol=symbol,
                reason=reason,
            )
        else:
            logger.error('model_block_failed', model_name=model_name, symbol=symbol, reason=reason)

    def unblock(self, model_name: str, symbol: str) -> None:
        if self.db.unblock_model(model_name, symbol):
            logger.info('model_unblocked', model_name=model_name, symbol=symbol)
        else:
            logger.warning('model_unblock_failed', model_name=model_name, symbol=symbol)
