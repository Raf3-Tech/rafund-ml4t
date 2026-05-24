"""Feature drift detection for RAFund ML4T."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List

import numpy as np
import pandas as pd
import structlog

from data.db import DatabaseConnection

logger = structlog.get_logger(__name__)


@dataclass
class DriftReport:
    symbol: str
    detected_at: datetime
    features_checked: int
    features_drifted: list[str]
    max_psi: float
    mean_psi: float
    drift_detected: bool
    severity: str
    recommended_action: str
    raw_psi_scores: Dict[str, float]


class FeatureDriftDetector:
    def __init__(
        self,
        model_name: str,
        reference_window_days: int = 90,
        drift_threshold: float = 0.15,
    ) -> None:
        self.model_name = model_name
        self.reference_window_days = reference_window_days
        self.drift_threshold = drift_threshold

    def compute_psi(self, reference: pd.Series, current: pd.Series, bins: int = 10) -> float:
        reference = reference.dropna().astype(float)
        current = current.dropna().astype(float)
        if reference.empty or current.empty:
            return 0.0

        reference_values = reference.values.astype(float)
        min_value = np.min(reference_values)
        max_value = np.max(reference_values)
        if min_value == max_value:
            return 0.0

        edges = np.linspace(min_value, max_value, bins + 1)
        ref_counts, _ = np.histogram(reference_values, bins=edges)
        cur_counts, _ = np.histogram(current.values.astype(float), bins=edges)

        ref_pct = ref_counts.astype(float) / max(ref_counts.sum(), 1)
        cur_pct = cur_counts.astype(float) / max(cur_counts.sum(), 1)

        epsilon = 1e-4
        ref_pct = np.maximum(ref_pct, epsilon)
        cur_pct = np.maximum(cur_pct, epsilon)

        psi_values = (ref_pct - cur_pct) * np.log(ref_pct / cur_pct)
        psi = float(np.sum(psi_values))
        return psi

    def load_reference_distribution(
        self,
        db: DatabaseConnection,
        symbol: str,
        timeframe: str,
        feature_names: list[str],
        as_of_date: date,
    ) -> pd.DataFrame:
        start_date = datetime.combine(as_of_date, datetime.min.time()) - timedelta(days=self.reference_window_days)
        end_date = datetime.combine(as_of_date, datetime.min.time())
        columns = ", ".join(feature_names)
        query = (
            f"SELECT timestamp, {columns} FROM features "
            "WHERE (symbol_a = %s OR symbol_b = %s) "
            "AND timestamp >= %s AND timestamp < %s "
            "ORDER BY timestamp"
        )
        conn = db.get_connection()
        try:
            df = pd.read_sql(query, conn, params=[symbol, symbol, start_date, end_date])
        finally:
            db.return_connection(conn)

        if df.empty:
            return pd.DataFrame(columns=feature_names)

        return df[feature_names].copy()

    def detect(self, symbol: str, reference_df: pd.DataFrame, current_df: pd.DataFrame) -> DriftReport:
        raw_scores: Dict[str, float] = {}
        for feature in reference_df.columns:
            raw_scores[feature] = self.compute_psi(reference_df[feature], current_df[feature])

        feature_scores = list(raw_scores.values())
        max_psi = float(max(feature_scores)) if feature_scores else 0.0
        mean_psi = float(np.mean(feature_scores)) if feature_scores else 0.0
        features_drifted = [name for name, psi in raw_scores.items() if psi > self.drift_threshold]
        drift_detected = len(features_drifted) > 0

        if max_psi < 0.10:
            severity = 'none'
            recommended_action = 'continue'
        elif max_psi <= 0.25:
            severity = 'warning'
            recommended_action = 'investigate'
        else:
            severity = 'critical'
            recommended_action = 'halt_trading'

        report = DriftReport(
            symbol=symbol,
            detected_at=datetime.utcnow(),
            features_checked=len(raw_scores),
            features_drifted=features_drifted,
            max_psi=max_psi,
            mean_psi=mean_psi,
            drift_detected=drift_detected,
            severity=severity,
            recommended_action=recommended_action,
            raw_psi_scores=raw_scores,
        )

        if severity == 'none':
            logger.info('drift_report', report=report)
        elif severity == 'warning':
            logger.warning('drift_report_warning', report=report)
        else:
            logger.error('drift_report_critical', report=report)

        return report

    def save_report(self, report: DriftReport, db: DatabaseConnection) -> None:
        payload = {
            'model_name': self.model_name,
            'symbol': report.symbol,
            'detected_at': report.detected_at,
            'features_checked': report.features_checked,
            'features_drifted': report.features_drifted,
            'max_psi': report.max_psi,
            'mean_psi': report.mean_psi,
            'drift_detected': report.drift_detected,
            'severity': report.severity,
            'recommended_action': report.recommended_action,
            'raw_psi_scores': report.raw_psi_scores,
        }
        if not db.insert_drift_report(payload):
            logger.error('drift_report_save_failed', model_name=self.model_name, symbol=report.symbol)
