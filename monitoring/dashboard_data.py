"""Read-only data aggregation layer for the ops dashboard.

Provides JSON-serializable snapshots of production model status and drift
status by combining the model registry with the ModelBlocker gate logic.
This module performs no writes and never mutates state.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import structlog

from models.blocker import ModelBlocker

logger = structlog.get_logger(__name__)

_VALID_SEVERITIES = {"none", "warning", "critical", "unknown"}


@dataclass
class ModelStatusRow:
    model_name: str
    symbol: str
    stage: str
    age_days: Optional[int]
    oos_sharpe: Optional[float]
    blocked: bool
    block_reason: Optional[str]
    safe_to_trade: bool


@dataclass
class DriftStatusRow:
    model_name: str
    symbol: str
    severity: str  # one of: "none" | "warning" | "critical" | "unknown"
    detected_at: Optional[datetime]


def _coerce_float(value: Any) -> Optional[float]:
    """Coerce a value to float, returning None on missing/invalid input."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_model_status(db) -> list[ModelStatusRow]:
    """Aggregate production model registry rows with blocker gate status.

    A failure on any single row is logged and skipped so that one bad model
    does not crash the whole dashboard.
    """
    blocker = ModelBlocker(db)
    rows: list[ModelStatusRow] = []

    for entry in db.get_production_models():
        model_name = entry.get("model_name")
        symbol = entry.get("symbol")
        try:
            status = blocker.is_blocked(model_name, symbol)
            rows.append(
                ModelStatusRow(
                    model_name=model_name,
                    symbol=symbol,
                    stage=entry.get("stage"),
                    age_days=status.model_age_days,
                    oos_sharpe=_coerce_float(entry.get("oos_sharpe")),
                    blocked=status.blocked,
                    block_reason=status.reason,
                    safe_to_trade=status.safe_to_trade,
                )
            )
        except Exception as exc:
            logger.warning(
                "dashboard_model_status_row_failed",
                model_name=model_name,
                symbol=symbol,
                error=str(exc),
            )
            continue

    return rows


def get_drift_status(db) -> list[DriftStatusRow]:
    """Map the latest drift reports into DriftStatusRow records.

    Defaults severity to "unknown" when missing or unrecognized.
    """
    rows: list[DriftStatusRow] = []

    for entry in db.get_latest_drift_reports():
        try:
            severity = entry.get("severity")
            if severity not in _VALID_SEVERITIES:
                severity = "unknown"
            rows.append(
                DriftStatusRow(
                    model_name=entry.get("model_name"),
                    symbol=entry.get("symbol"),
                    severity=severity,
                    detected_at=entry.get("detected_at"),
                )
            )
        except Exception as exc:
            logger.warning(
                "dashboard_drift_status_row_failed",
                error=str(exc),
            )
            continue

    return rows


def row_to_dict(row) -> dict:
    """Convert a dataclass row into a JSON-serializable dict.

    datetime values are converted to ISO-8601 strings so the result can be
    returned directly from a Flask ``/api`` endpoint.
    """
    raw = dataclasses.asdict(row)
    result: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, datetime):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result
