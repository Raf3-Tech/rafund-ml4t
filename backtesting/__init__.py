"""Backtesting: evaluation engine, prop-firm rules, and DB persistence."""

from backtesting.engine_eval import EvaluationBacktestEngine
from backtesting.evaluation_rules import EvaluationStatus, PropFirmRules
from backtesting.persist import persist_evaluation_run
from backtesting.validation import ValidationResult, run_validation

__all__ = [
    "EvaluationBacktestEngine",
    "PropFirmRules",
    "EvaluationStatus",
    "persist_evaluation_run",
    "run_validation",
    "ValidationResult",
]
