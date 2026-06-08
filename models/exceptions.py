"""Custom exceptions for Raf3nd ML4T model lifecycle management."""

from __future__ import annotations

from dataclasses import dataclass


class ModelBlockedError(Exception):
    """Raised when a model is blocked from trading."""
    pass


class ModelRejectedError(Exception):
    """Raised when a candidate model fails validation before promotion."""
    pass


@dataclass
class BlockStatus:
    blocked: bool
    reason: str | None
    model_age_days: int | None
    last_drift_severity: str | None
    safe_to_trade: bool
